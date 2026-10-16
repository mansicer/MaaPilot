"""hosts 的复查修正：Codex 记录怎么判失败、两个宿主各记会话、推送找 codex、定时任务的环境、Codex 不信任时拦下。
不碰真的 ~/.codex（conftest 把 CODEX_HOME 指到临时目录），不跑真的 codex。"""
from __future__ import annotations

import json
import os

import pytest
import yaml

from maapilot import hosts, paths
from maapilot.cliutil import ENVIRONMENT, CliError
from maapilot.flows.daily import cron
from maapilot.tools import common, doctor, host_cmd
from tests.test_daily import _no_real_crontab, env, fake_crontab, run, script  # noqa: F401 - fixture
from tests.portable import needs_cron, sched_ready  # noqa: F401 - fixture
from tests.test_hosts import codex, _use_codex  # noqa: F401 - fixture


def _write(t, evs):
    t.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in evs), encoding="utf-8")


# --- Codex 的记录 ----------------------------------------------------------------------------------------------------

def test_codex_reconnect_error_is_not_failure(tmp_path):
    t = tmp_path / "a.jsonl"
    _write(t, [{"type": "thread.started", "thread_id": "x"},
               {"type": "error", "message": "Reconnecting... 1/5"},
               {"type": "item.completed", "item": {"type": "agent_message", "text": "结果：完成"}},
               {"type": "turn.completed", "usage": {}}])
    assert hosts.parse("codex", t)["is_error"] is False


@pytest.mark.parametrize("evs", [
    [{"type": "item.completed", "item": {"type": "agent_message", "text": "做到一半"}},
     {"type": "turn.failed", "error": {"message": "x"}}],
    [{"type": "thread.started"}, {"type": "item.completed", "item": {"type": "agent_message", "text": "没做完"}}],
    [{"type": "thread.started"}, {"type": "turn.completed", "usage": {}}],
])
def test_codex_failures(tmp_path, evs):
    t = tmp_path / "a.jsonl"
    _write(t, evs)
    assert hosts.parse("codex", t)["is_error"] is True


def test_codex_exec_flags(tmp_path):
    argv = hosts.command("codex", "/x/codex", "做事", tmp_path / "a.jsonl")
    assert "--skip-git-repo-check" in argv and "--ephemeral" in argv and argv[-1] == "做事"


# --- 用户的会话 ------------------------------------------------------------------------------------------------------

def test_sessions_kept_per_host():
    hosts.record_session({"CODEX_THREAD_ID": "th-1"})
    hosts.record_session({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "c-1"})
    s = hosts.sessions()
    assert s["codex"]["thread"] == "th-1" and s["claude"]["thread"] == "c-1"
    assert hosts.session()["host"] == "claude"


def test_both_env_vars_prefer_claude():
    assert hosts.current_session({"CODEX_THREAD_ID": "th", "CLAUDECODE": "1"})["host"] == "claude"


def test_old_single_record_format_is_read():
    common.write_json(paths.STATE / "agent-session.json", {"host": "codex", "thread": "th-old", "at": common.iso()})
    assert set(hosts.sessions()) == {"codex"} and hosts.sessions()["codex"]["thread"] == "th-old"
    assert hosts.session()["host"] == "codex"
    hosts.record_session({"CLAUDECODE": "1"})                           # 升级成新格式，旧的 Codex 会话还在
    assert hosts.sessions()["codex"]["thread"] == "th-old" and "claude" in hosts.sessions()


def test_push_follows_recency_unless_agent_is_codex(env, codex):
    prog, calls = codex
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["tools"]["codex"] = str(prog)                                   # agent 还是 claude
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    hosts.record_session({"CODEX_THREAD_ID": "th-1"})
    assert hosts.push("a") == {"pushed": True, "thread": "th-1"}      # Codex 是最近的
    hosts.record_session({"CLAUDECODE": "1"})
    r = hosts.push("b")
    assert r["pushed"] is False and r["reason"]                         # Claude Code 更新：它自己挂着 daily watch
    assert [c["argv"][-1] for c in calls()] == ["a"]
    _use_codex(prog)                                                    # agent 是 codex：照样推给记下的 Codex 会话
    assert hosts.push("c") == {"pushed": True, "thread": "th-1"}
    assert calls()[-1]["argv"][0] == "queue"


def test_push_finds_codex_outside_path(env, codex, monkeypatch, tmp_path):
    prog, calls = codex
    hosts.record_session({"CODEX_THREAD_ID": "th-1"})
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))                # 定时任务里 PATH 很短
    monkeypatch.setattr(hosts, "CODEX_FALLBACKS", ())
    assert hosts.codex_path({}) is None
    r = hosts.push("x", {})
    assert r["pushed"] is False and "codex" in r["reason"] and r["thread"] == "th-1"
    monkeypatch.setattr(hosts, "CODEX_FALLBACKS", (str(prog),))
    assert hosts.codex_path({}) == str(prog)
    assert hosts.push("x", {})["pushed"] is True
    assert hosts.codex_path({"tools": {"codex": str(prog)}}) == str(prog)


# --- 定时任务 --------------------------------------------------------------------------------------------------------

def _launch_env(cfg: dict) -> dict:
    """定时命令里交给启动器的环境变量（--env K=V；PATH+= 的键是 "PATH+"）。"""
    argv = cron.command(cfg).argv
    return dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "--env")


def test_launch_env_has_codex_home_and_dir(env, codex, monkeypatch, tmp_path):
    prog, _ = codex
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    cfg = {"tools": {"codex": str(prog)}}
    e = _launch_env(cfg)
    assert e["CODEX_HOME"] == str(tmp_path / "ch") and e["PYTHONUTF8"] == "1"
    assert e["MAAPILOT_PRIVATE"] == str(paths.PRIVATE)
    assert str(prog.parent) not in e["PATH+"].split(os.pathsep)          # agent 是 claude、没记过 Codex 会话
    hosts.record_session({"CODEX_THREAD_ID": "th-1"})
    assert str(prog.parent) in _launch_env(cfg)["PATH+"].split(os.pathsep)
    monkeypatch.delenv("CODEX_HOME")
    assert "CODEX_HOME" not in _launch_env(cfg)


@needs_cron
def test_schedule_refuses_untrusted_codex(capsys, env, codex, tmp_path, monkeypatch, sched_ready):
    prog, _ = codex
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, "")))
    _use_codex(prog)
    code, out = run(capsys, "daily", "schedule")
    assert code == ENVIRONMENT and out["error"]["code"] == "codex_untrusted"
    assert out["error"]["hint"] == "maapilot host install codex"
    code, out = run(capsys, "daily", "schedule", "--dry-run")         # 只看不改：照样给
    assert code == 0, out
    cfgfile = host_cmd.codex_home() / "config.toml"
    cfgfile.parent.mkdir(parents=True, exist_ok=True)
    cfgfile.write_text(f'[projects.{json.dumps(str(paths.ROOT))}]\ntrust_level = "trusted"\n', encoding="utf-8")
    code, out = run(capsys, "daily", "schedule")
    assert code == 0, out


# --- doctor ----------------------------------------------------------------------------------------------------------

class _Probe(doctor.Probe):
    def which(self, prog):
        return prog if os.path.isabs(prog) else None


def test_untrusted_codex_is_required(codex):
    prog, _ = codex
    ctx = doctor.Ctx(False, _Probe())
    ctx.cfg = {"agent": "codex", "tools": {"codex": str(prog)}}
    item = doctor.check_agent(ctx)
    assert item["ok"] is False and item.get("required", True) is True and item["fix"] == "maapilot host install codex"
    ctx.cfg = {"agent": "claude", "tools": {"claude": "/x/claude"}}
    item = doctor.check_agent(ctx)
    assert item.get("required", True) is False


@pytest.mark.parametrize("server,shown", [("Official", True), ("Bilibili", True), ("YoStarEN", False)])
def test_news_brief_for_cn_servers(monkeypatch, server, shown):
    from maapilot import config
    from maapilot.knowledge import news
    monkeypatch.setattr(config, "load_or_defaults", lambda: {"server": server})
    monkeypatch.setattr(news, "load", lambda: {"at": "t", "items": [{"key": "k"}]})
    monkeypatch.setattr(news, "lines", lambda d: ["x"])
    monkeypatch.setattr(news, "due", lambda d: [])
    assert (doctor.news_brief() is not None) is shown
