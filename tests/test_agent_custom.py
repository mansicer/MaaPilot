"""自定义的无头 agent（config.yaml 的 agent 写成 {name, command, prompt}）与 `daily done`（agent 交代结果）。
不起真的 claude / codex / dsh：agent 是假程序（tests/portable.fake_program），daily done 由它用本 Python 真的跑一次。"""
from __future__ import annotations

import json
import sys
import types

import pytest
import yaml

from maapilot import hosts, paths
from maapilot.cliutil import CliError
from maapilot.crew import lock
from maapilot.flows import daily
from tests.portable import sched_ready  # noqa: F401 - fixture
from tests.test_daily import _no_real_crontab, env, read_report, run, script  # noqa: F401 - fixture

# 假的 harness：记下 argv、stdin、环境；按环境变量决定说什么、交不交代、退出码几
FAKE_HARNESS = """
import json, os, subprocess, sys
a = sys.argv[1:]
prompt = a[-1] if os.environ.get("FAKE_PROMPT_ARG") else sys.stdin.read()
with open(os.environ["FAKE_HARNESS_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": a, "prompt": prompt, "cwd": os.getcwd(), "daily": os.environ.get("MAAPILOT_DAILY_RUN"),
                        "token": os.environ.get("MAAPILOT_LOCK_TOKEN")}, ensure_ascii=False) + "\\n")
done = os.environ.get("FAKE_DONE")
if done:
    p = subprocess.run([sys.executable, "-m", "maapilot.cli", "daily", "done", "--summary", done],
                       capture_output=True, text=True, encoding="utf-8")
    with open(os.environ["FAKE_HARNESS_LOG"] + ".done", "a", encoding="utf-8") as f:
        f.write(json.dumps({"code": p.returncode, "out": p.stdout}, ensure_ascii=False) + "\\n")
out = os.environ.get("FAKE_STDOUT", "")
if out:
    sys.stdout.write(out)
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


@pytest.fixture
def harness(tmp_path, monkeypatch, env):
    """config.yaml 的 agent 换成假的 dsh（command 是绝对路径加两个参数）。返回 (程序, 读调用记录, 改 agent 配置)。"""
    prog = script(tmp_path / "dsh", FAKE_HARNESS)
    log = tmp_path / "harness.jsonl"
    monkeypatch.setenv("FAKE_HARNESS_LOG", str(log))

    def set_agent(agent):
        cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
        cfg["agent"] = agent
        paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    def calls(suffix=""):
        p = log.with_name(log.name + suffix)
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    set_agent({"name": "dsh", "command": [str(prog), "--profile", "headless"]})
    env["behavior"]["fail"] = {"infrast"}                     # 基建失败：叫 agent 接管
    return prog, calls, set_agent


def _infra(out):
    return next(t for t in out["data"]["tasks"] if t["task"] == "infrast")


# --- config 的 agent ------------------------------------------------------------------------------------------------

def test_spec_accepts_builtins_and_mappings():
    assert hosts.spec({}) == {"name": "claude", "command": None, "prompt": "stdin"}
    assert hosts.spec({"agent": " Codex "})["name"] == "codex"
    assert hosts.spec({"agent": {"name": "codex"}}) == {"name": "codex", "command": None, "prompt": "stdin"}
    sp = hosts.spec({"agent": {"name": "dsh", "command": ["dsh", "--profile", "headless"]}})
    assert sp == {"name": "dsh", "command": ["dsh", "--profile", "headless"], "prompt": "stdin"}
    assert hosts.spec({"agent": {"name": "x-1", "command": ["x"], "prompt": "arg"}})["prompt"] == "arg"
    # claude / codex 写了 command：照 command 起
    assert hosts.spec({"agent": {"name": "claude", "command": ["/opt/claude", "-p"]}})["command"] == ["/opt/claude", "-p"]


@pytest.mark.parametrize("agent", [
    "dsh",                                                   # 字符串只认 claude / codex
    ["dsh"],
    {"command": ["dsh"]},                                    # 没写 name
    {"name": "D S H", "command": ["dsh"]},
    {"name": "dsh"},                                         # 不是内置的又没写 command
    {"name": "dsh", "command": []},
    {"name": "dsh", "command": "dsh --profile headless"},    # 要写成列表
    {"name": "dsh", "command": ["dsh", 3]},
    {"name": "dsh", "command": ["dsh"], "prompt": "file"},
    {"name": "dsh", "command": ["dsh"], "env": {}},          # 不认识的字段
])
def test_spec_rejects_bad_shapes(agent):
    with pytest.raises(CliError) as e:
        hosts.spec({"agent": agent})
    assert e.value.code == "invalid_config" and e.value.exit == 2 and "agent" in e.value.message


def test_bad_agent_config_refuses_scheduled_daily(capsys, harness):
    _, calls, set_agent = harness
    set_agent({"name": "dsh"})
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 2 and out["error"]["code"] == "invalid_config" and "command" in out["error"]["message"]
    assert calls() == []


def test_custom_command_line():
    sp = {"name": "dsh", "command": ["dsh", "--profile", "headless"], "prompt": "arg"}
    t = paths.LOGS / "a.jsonl"
    assert hosts.command("dsh", "/x/dsh", "做事", t, sp=sp) == ["/x/dsh", "--profile", "headless", "做事"]
    assert hosts.command("dsh", "/x/dsh", "做事", t, stdin=True, sp=sp) == ["/x/dsh", "--profile", "headless"]
    assert hosts.custom_on_stdin({**sp, "prompt": "stdin"}, "/x/dsh") and not hosts.custom_on_stdin(sp, "/x/dsh")


def test_custom_batch_file_forces_stdin(monkeypatch):
    """Windows 上 .cmd 经 cmd.exe 转，多行参数会被截断：写了 prompt: arg 也改走 stdin。"""
    from maapilot.oscompat import procs
    monkeypatch.setattr(procs, "WINDOWS", True)
    assert hosts.custom_on_stdin({"command": ["dsh"], "prompt": "arg"}, r"C:\tools\dsh.cmd")


def test_stdout_tail_drops_json_events_and_keeps_the_end(tmp_path):
    t = tmp_path / "out.txt"
    t.write_text('{"type": "start"}\n前面的话\n' + "很长的一行\n" * 400 + "最后：做完了。\n结果：完成\n", encoding="utf-8")
    tail = hosts.stdout_tail(t)
    assert tail.endswith("结果：完成") and '{"type"' not in tail and len(tail) <= hosts.STDOUT_TAIL
    assert tail.startswith("很长的一行")                       # 从整行开始
    t.write_text('{"type": "a"}\n{"type": "b"}\n', encoding="utf-8")
    assert hosts.stdout_tail(t) is None
    assert hosts.stdout_tail(tmp_path / "none") is None


# --- 自定义 harness 跑定时日常 --------------------------------------------------------------------------------------

def test_custom_agent_without_daily_done_uses_stdout(capsys, harness, monkeypatch):
    prog, calls, _ = harness
    monkeypatch.setenv("FAKE_STDOUT", '{"type": "event"}\n关掉了弹窗，重跑后做完了。\n结果：完成\n')
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    (call,) = calls()
    assert call["argv"] == ["--profile", "headless"] and "maapilot-recovery" in call["prompt"]   # 提示从 stdin 给
    assert "maapilot daily done" in call["prompt"] and "Bash 超时" not in call["prompt"]
    assert call["cwd"] == str(paths.ROOT) and call["daily"] == out["data"]["run_id"] and call["token"]
    a = _infra(out)["agent"]
    assert a["host"] == "dsh" and a["reported"] is False and a["ok"] is True and a["recovered"] is True
    assert a["summary"] == "关掉了弹窗，重跑后做完了。\n结果：完成"
    assert _infra(out)["recovered"] is True and out["data"]["failed"] == []
    assert "agent 接手（处理好了）" in read_report(out["data"])


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 上假程序是 .cmd：prompt: arg 也改走 stdin（见 test_custom_batch_file_forces_stdin）")
def test_custom_agent_prompt_as_argument(capsys, harness, monkeypatch):
    prog, calls, set_agent = harness
    set_agent({"name": "dsh", "command": [str(prog), "--profile", "headless"], "prompt": "arg"})
    monkeypatch.setenv("FAKE_PROMPT_ARG", "1")
    monkeypatch.setenv("FAKE_STDOUT", "处理好了。\n结果：完成")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    (call,) = calls()
    assert call["argv"][:2] == ["--profile", "headless"] and "maapilot-recovery" in call["argv"][2]


def test_custom_agent_reports_with_daily_done(capsys, harness, monkeypatch):
    _, calls, _ = harness
    monkeypatch.setenv("FAKE_DONE", "公告挡住了，关掉后基建做完了。\n结果：完成")
    monkeypatch.setenv("FAKE_STDOUT", "随便说的最后一句（不当总结）")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    (done,) = calls(".done")
    assert done["code"] == 0 and json.loads(done["out"])["data"]["recorded"] is True
    a = _infra(out)["agent"]
    assert a["reported"] is True and a["summary"].startswith("公告挡住了") and a["recovered"] is True and a["ok"]
    assert not (paths.STATE / "agent-done.json").exists()          # 读走了


def test_custom_agent_nonzero_exit_is_not_ok(capsys, harness, monkeypatch):
    monkeypatch.setenv("FAKE_DONE", "没进得去基建。\n结果：没做成")
    monkeypatch.setenv("FAKE_EXIT", "1")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 1 and out["error"]["code"] == "daily_incomplete"
    a = _infra(out)["agent"]
    assert a["ok"] is False and a["exit"] == 1 and a["reported"] is True and a["recovered"] is False
    assert out["data"]["failed"] == ["infrast"]


def test_custom_agent_silent_is_an_error(capsys, harness, monkeypatch):
    monkeypatch.setenv("FAKE_STDOUT", '{"type": "only-events"}\n')
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 1
    a = _infra(out)["agent"]
    assert a["summary"] is None and a["is_error"] is True and a["ok"] is False and a["reported"] is False
    assert "没有留下总结" in read_report(out["data"])


def test_custom_goal_agent(capsys, env, harness, monkeypatch):
    """目标部分也走自定义的 agent；总结进「目标部分」。"""
    _, calls, _ = harness
    env["behavior"]["fail"] = set()
    paths.GOALS.write_text("## 遥 精2\n- 状态：进行中\n- 对象：干员 遥 → 精2 1级\n- 原话：x\n- 同意：y\n- 花合成玉/源石：否\n",
                           encoding="utf-8")
    monkeypatch.setenv("FAKE_DONE", "刷了 3 次 1-7。\n结果：完成")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    (call,) = calls()
    assert "maapilot-daily" in call["prompt"]
    md = read_report(out["data"])
    assert "## 目标部分" in md and "刷了 3 次 1-7" in md and "结果：完成" not in md


def test_builtin_claude_daily_done_wins_over_result(capsys, env, tmp_path, monkeypatch):
    """claude 交代过就用交代的（花费还从它的输出里读）；没交代的照旧读最后的 result（见 test_daily）。"""
    body = FAKE_HARNESS.replace('prompt = a[-1] if os.environ.get("FAKE_PROMPT_ARG") else sys.stdin.read()',
                                'prompt = a[a.index("-p") + 1]')
    claude = script(tmp_path / "claude2", body)
    monkeypatch.setenv("FAKE_STDOUT", json.dumps({"type": "result", "is_error": False, "result": "最后的回复。\n结果：没做成",
                                                  "total_cost_usd": 0.3, "num_turns": 4}, ensure_ascii=False) + "\n")
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["tools"]["claude"] = str(claude)
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("FAKE_HARNESS_LOG", str(tmp_path / "h.jsonl"))
    monkeypatch.setenv("FAKE_DONE", "交代的总结。\n结果：完成")
    env["behavior"]["fail"] = {"infrast"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    a = _infra(out)["agent"]
    assert a["reported"] is True and a["summary"].startswith("交代的总结") and a["recovered"] is True
    assert a["cost_usd"] == 0.3 and code == 0


# --- daily done 本身 ------------------------------------------------------------------------------------------------

def test_daily_done_outside_daily_is_refused(capsys, monkeypatch):
    code, out = run(capsys, "daily", "done", "--summary", "x")
    assert code == 2 and out["error"]["code"] == "not_in_daily"
    monkeypatch.setenv(daily.DAILY_ENV, "20261019-0440")
    monkeypatch.setenv(lock.TOKEN_ENV, "not-the-token")
    code, out = run(capsys, "daily", "done", "--summary", "x")
    assert code == 2 and out["error"]["code"] == "not_inherited"


@pytest.fixture
def handed(monkeypatch):
    """像日常把模拟器交给了 agent 那样：日常的标记在、锁在，环境里有运行编号和锁的 token。"""
    rid = "20261019-0440"
    daily._take_marker(rid, "schedule")
    with lock.acquire("daily run", job="daily") as lk:
        monkeypatch.setenv(daily.DAILY_ENV, rid)
        monkeypatch.setenv(lock.TOKEN_ENV, lk.data["token"])
        yield rid
    daily._drop_marker()


def test_daily_done_records_last_call(capsys, handed, tmp_path):
    code, out = run(capsys, "daily", "done", "--summary", "第一次")
    assert code == 0 and out["data"]["recorded"] is True and out["data"]["recovered"] is None
    f = tmp_path / "s.txt"
    f.write_text("基建做完了。\n结果：没做成\n", encoding="utf-8")
    code, out = run(capsys, "daily", "done", "--summary-file", str(f), "--result", "完成")
    assert code == 0 and out["data"]["recovered"] is True
    d = daily.agent._take_done(handed)
    assert d["summary"] == "基建做完了。\n结果：完成"            # 最后一次为准；--result 换掉原来那行
    assert daily.agent._take_done(handed) is None
    for argv in (["daily", "done"], ["daily", "done", "--summary", "a", "--summary-file", str(f)],
                 ["daily", "done", "--summary", "  "], ["daily", "done", "--summary-file", str(tmp_path / "none")]):
        code, out = run(capsys, *argv)
        assert code == 2 and out["error"]["code"] == "bad_arguments", argv


def test_daily_done_from_other_run_is_ignored(handed):
    daily.report_done("别的运行交代的", None, None)
    assert daily.agent._take_done("20990101-0000") is None


def test_daily_done_after_daily_ended_is_refused(capsys, handed):
    daily._drop_marker()
    code, out = run(capsys, "daily", "done", "--summary", "x")
    assert code == 2 and out["error"]["code"] == "not_in_daily"


# --- doctor、host status、定时要执行的程序 --------------------------------------------------------------------------

def _cfg(prog):
    return {"agent": {"name": "dsh", "command": [str(prog), "--profile", "headless"]}, "tools": {}}


def test_doctor_custom_agent(tmp_path):
    from maapilot.tools import doctor
    prog = script(tmp_path / "dsh", "pass\n")
    ctx = doctor.Ctx(offline=True, probe=doctor.PROBE)
    ctx.cfg = _cfg(prog)
    item = doctor.check_agent(ctx)
    assert item["name"] == "dsh" and item["ok"] is True and item["custom"] is True and item["path"] == str(prog)
    assert "没实测过" in item["detail"] and item["required"] is False
    ctx.cfg = _cfg(tmp_path / "missing-dsh")
    item = doctor.check_agent(ctx)
    assert item["ok"] is False and "agent.command" in item["fix"]
    ctx.cfg = {"agent": {"name": "dsh"}}
    assert doctor.check_agent(ctx)["name"] == "agent"


def test_exec_paths_and_preflight_with_custom_agent(tmp_path, sched_ready):
    from maapilot.flows.daily import cron
    prog = script(tmp_path / "dsh", "pass\n")
    cfg = {**_cfg(prog), "emulator": {"adb": "adb"}, "tools": {"maa": "maa"}}
    ex = cron._exec_paths(cfg)
    assert ex["dsh"] == str(prog) and "claude" not in ex
    cmd = types.SimpleNamespace(argv=[sys.executable])
    assert cron.preflight(cfg, cmd) == []
    cfg = {**cfg, **_cfg(tmp_path / "missing-dsh")}
    probs = cron.preflight(cfg, cmd)
    assert [p["what"] for p in probs] == ["dsh"] and probs[0]["problem"] == "missing"


def test_host_status_shows_custom_agent(capsys, tmp_path):
    prog = script(tmp_path / "dsh", "pass\n")
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text(yaml.safe_dump(_cfg(prog)), encoding="utf-8")
    code, out = run(capsys, "host", "status")
    assert code == 0 and out["data"]["agent"] == "dsh"
    assert out["data"]["agent_command"] == {"command": [str(prog), "--profile", "headless"], "prompt": "stdin",
                                            "path": str(prog)}
    assert set(out["data"]["hosts"]) == {"claude", "codex"}


def test_long_command_rule_is_generic_for_custom():
    from maapilot.flows.daily.prompts import REPORT_RULES, long_command_rule
    assert "Bash" in long_command_rule("claude") and "叫不醒" in long_command_rule("codex")
    assert "Bash" not in long_command_rule("dsh") and "前台" in long_command_rule("dsh")
    assert "maapilot daily done --summary" in REPORT_RULES and "不要用 maapilot report write" in REPORT_RULES


def test_push_ignores_custom_agent(monkeypatch):
    """自定义的 agent 不推：没记过 Codex 会话就不推，名字也不会被当成 codex。"""
    r = hosts.push("x", {"agent": {"name": "dsh", "command": ["dsh"]}})
    assert r["pushed"] is False
