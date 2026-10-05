"""daily run / schedule：不碰模拟器、不起真的 claude、不改真的 crontab。
maapilot 子命令换成进程内的假实现；无头 agent 是一个假的 claude 脚本（真的子进程）；crontab 是一个假脚本。"""
import json
import os
import stat
import sys
import textwrap
from datetime import timedelta

import pytest
import yaml

from maapilot import cli, paths
from maapilot.crew import lock
from maapilot.flows import daily
from maapilot.flows._common import now


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def script(path, body):
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


FAKE_CLAUDE = """
import json, os, sys, time
rec = os.environ["FAKE_CLAUDE_LOG"]
with open(rec, "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                        "token": os.environ.get("MAAPILOT_LOCK_TOKEN"),
                        "daily": os.environ.get("MAAPILOT_DAILY_RUN")}, ensure_ascii=False) + "\\n")
if os.environ.get("FAKE_CLAUDE_SLEEP"):
    time.sleep(float(os.environ["FAKE_CLAUDE_SLEEP"]))
prompt = sys.argv[sys.argv.index("-p") + 1]
print(json.dumps({"type": "system", "subtype": "init"}))
text = os.environ.get("FAKE_CLAUDE_RESULT", "关掉了一个公告弹窗，重跑后做完了。\\n结果：完成")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                  "total_cost_usd": 0.12, "num_turns": 5}, ensure_ascii=False))
"""


@pytest.fixture(autouse=True)
def _no_real_crontab(tmp_path, monkeypatch):
    """任何测试都碰不到真的 crontab：默认指向一个不存在的程序，要用的测试换成假脚本。"""
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(tmp_path / "no-crontab"))


def test_maapilot_call_runs_real_cli(tmp_path):
    code, env_ = daily.maapilot_call(["goals", "list"], dict(os.environ))
    assert code == 0 and env_["ok"] is True and env_["data"]["goals"] == []
    code, env_ = daily.maapilot_call(["goals", "update", "x", "--note", "y"], dict(os.environ))
    assert code == 2 and env_["error"]["code"] == "unknown_goal"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """配置、假 claude、假的 maapilot 子命令。返回 calls 列表与可调的行为表。"""
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    claude = script(tmp_path / "claude", FAKE_CLAUDE)
    log = tmp_path / "claude-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    cfg = {"emulator": {"address": "emu:1"}, "tools": {"claude": str(claude)},
           "daily": {"tasks": ["startup", "recruit", "infrast", "award"], "default_stage": "1-7"}}
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    calls, behavior = [], {"fail": set(), "guard_check": [], "depot": [{"4001": 100000, "30012": 5},
                                                                      {"4001": 88000, "30012": 19}]}

    def fake(args, env_, timeout_s=None):
        calls.append({"args": args, "token": env_.get(lock.TOKEN_ENV), "daily": env_.get(daily.DAILY_ENV)})
        # 子进程里的命令拿得到嵌套锁
        assert env_.get(lock.TOKEN_ENV) == lock.current()["token"]
        if args[0] == "guard":
            snap = behavior["depot"].pop(0) if behavior["depot"] else None
            if snap is not None:
                paths.STATE.mkdir(parents=True, exist_ok=True)
                (paths.STATE / "depot.json").write_text(json.dumps({"at": f"t{len(calls)}", "items": snap}))
            if args[1] == "check" and behavior["guard_check"]:
                code = behavior["guard_check"].pop(0)
                if code == 3:
                    return 3, {"ok": False, "data": {"unexplained": {"orundum": -600}},
                               "error": {"code": "currency_decreased", "message": "合成玉少了 600"}}
            return 0, {"ok": True, "data": {}, "error": None}
        task = args[1]
        if task in behavior["fail"]:
            return 1, {"ok": False, "data": {"log": "private/log/maa/x.log",
                                             **({"evidence": behavior["evidence"]} if behavior.get("evidence") else {})},
                       "error": {"code": behavior.get("fail_code", "maa_failed"), "message": f"{task} 卡住了"}}
        if task == "fight":
            return 0, {"ok": True, "data": {"stage": args[2], "times_done": 12, "sanity_used": 72,
                                            "medicine_used": 0}, "error": None}
        return 0, {"ok": True, "data": {"task": task}, "error": None}

    monkeypatch.setattr(daily, "maapilot_call", fake)
    return {"calls": calls, "behavior": behavior, "claude_log": log}


def claude_calls(env):
    p = env["claude_log"]
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def read_report(d):
    return (paths.ROOT / d["report"]).read_text(encoding="utf-8")     # 绝对路径时 / 取右边


def maa_tasks(env):
    return [" ".join(c["args"][1:]) for c in env["calls"] if c["args"][0] == "maa"]


def test_all_ok_no_goals_fights_default_stage(capsys, env):
    code, out = run(capsys, "daily", "run")
    assert code == 0, out
    d = out["data"]
    assert maa_tasks(env) == ["startup", "recruit", "infrast", "fight 1-7", "award"]
    assert [c["args"] for c in env["calls"] if c["args"][0] == "guard"] == [["guard", "snapshot"], ["guard", "check"]]
    assert d["ok"] is True and d["failed"] == [] and d["guard"] == {"ok": True}
    assert d["farm"]["stage"] == "1-7" and d["farm"]["times_done"] == 12
    assert {"id": "30012", "name": d["depot_change"][1]["name"], "delta": 14} in d["depot_change"]
    assert claude_calls(env) == []
    md = read_report(d)
    assert "全部做完" in md and "1-7 打了 12 次" in md and "花掉：" in md
    assert not (paths.PRIVATE / "daily-run.json").exists() and lock.current() is None
    assert (paths.LOGS / f"daily-{d['run_id']}.jsonl").exists()


def test_schedule_failure_wakes_recovery_agent_and_continues(capsys, env):
    env["behavior"]["fail"] = {"infrast"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    d = out["data"]
    assert maa_tasks(env) == ["startup", "recruit", "infrast", "fight 1-7", "award"]   # 接管后接着跑
    (call,) = claude_calls(env)
    argv = call["argv"]
    assert argv[0] == "-p" and "maapilot-recovery" in argv[1] and "基建" in argv[1] and "infrast" in argv[1]
    assert argv[2:] == ["--output-format", "stream-json", "--verbose",
                        "--settings", str(paths.ROOT / ".claude" / "settings.json"),
                        "--permission-mode", "default", "--permission-prompts", "none"]
    assert "maapilot maa infrast" in argv[1]
    assert call["cwd"] == str(paths.ROOT) and call["token"] and call["daily"] == d["run_id"]
    infra = next(t for t in d["tasks"] if t["task"] == "infrast")
    assert infra["ok"] is False and infra["recovered"] is True
    assert infra["agent"]["summary"].startswith("关掉了一个公告弹窗") and infra["agent"]["cost_usd"] == 0.12
    assert d["failed"] == []
    # 接管之后对了一次账，最后又对一次
    assert [c["args"][1] for c in env["calls"] if c["args"][0] == "guard"] == ["snapshot", "check", "check"]
    md = read_report(d)
    assert "agent 接手（处理好了）" in md


def test_schedule_agent_not_recovered_is_failed(capsys, env, monkeypatch):
    env["behavior"]["fail"] = {"recruit"}
    monkeypatch.setenv("FAKE_CLAUDE_RESULT", "公招页面一直转圈。\n结果：没做成")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 1 and out["error"]["code"] == "daily_incomplete"
    assert out["data"]["failed"] == ["recruit"] and out["data"]["status"] == "done"


def test_manual_failure_stops_and_hands_over(capsys, env):
    """会话里跑：第一项失败就停下交给在场的 agent，后面的不跑；hint 里是接着跑的命令。"""
    env["behavior"]["fail"] = {"infrast"}
    code, out = run(capsys, "daily", "run", "--interactive")
    assert code == 1 and out["error"]["code"] == "handoff"
    d = out["data"]
    assert d["status"] == "handed_off" and claude_calls(env) == []
    assert d["handoff"]["task"] == "infrast" and d["handoff"]["rest"][0] == "infrast"
    assert "award" not in maa_tasks(env) and "award" in d["handoff"]["rest"]
    assert "--only " + ",".join(d["handoff"]["rest"]) in out["error"]["hint"]
    assert "交给在场的 agent" in read_report(d)


EVIDENCE = {"shot_now": "/p/private/shots/20261002-160918-fail-infrast.png",
            "maa_shots": [{"path": "/maa/debug/interface/2026.10.02-16.09.17.997_raw.png", "kind": "interface",
                           "at": "2026-10-02T16:09:17+08:00"}],
            "window": {"start": "2026-10-02T16:01:02+08:00", "end": "2026-10-02T16:09:18+08:00"},
            "why": "maapilot maa why --task infrast"}


def test_manual_handoff_carries_evidence(capsys, env):
    env["behavior"]["fail"] = {"infrast"}
    env["behavior"]["evidence"] = EVIDENCE
    code, out = run(capsys, "daily", "run")
    assert code == 1 and out["error"]["code"] == "handoff"
    assert out["data"]["handoff"]["evidence"] == EVIDENCE
    assert "evidence" in out["error"]["hint"] and "maa why" in out["error"]["hint"]


def test_recovery_prompt_lists_evidence(capsys, env):
    env["behavior"]["fail"] = {"infrast"}
    env["behavior"]["evidence"] = {**EVIDENCE, "missing": ["MAA 的截图：找不到 MAA 的日志目录"]}
    run(capsys, "daily", "run", "--trigger", "schedule")
    (call,) = claude_calls(env)
    prompt = call["argv"][1]
    assert EVIDENCE["shot_now"] in prompt and EVIDENCE["maa_shots"][0]["path"] in prompt
    assert "maapilot maa why --task infrast" in prompt and "找不到 MAA 的日志目录" in prompt
    assert "maapilot maa startup" in prompt                     # 被登出就直接重新登录


def test_schedule_offline_also_wakes_agent(capsys, env, monkeypatch):
    env["behavior"]["fail"] = {"recruit"}
    env["behavior"]["fail_code"] = "offline"
    monkeypatch.setenv("FAKE_CLAUDE_RESULT", "重新登录了。\n结果：处理好了")
    run(capsys, "daily", "run", "--trigger", "schedule")
    assert claude_calls(env)


def test_no_agent_flag(capsys, env):
    env["behavior"]["fail"] = {"startup"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule", "--no-agent")
    assert code == 1 and claude_calls(env) == []


def test_active_goals_go_to_goal_agent(capsys, env):
    paths.GOALS.write_text("## 遥 精2\n- 状态：进行中\n- 对象：干员 遥 → 精2 1级\n- 原话：x\n- 同意：y\n- 花合成玉/源石：否\n",
                           encoding="utf-8")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    assert "fight 1-7" not in maa_tasks(env) and maa_tasks(env)[-1] == "award"
    (call,) = claude_calls(env)
    assert "maapilot-daily" in call["argv"][1] and "遥 精2" in call["argv"][1]
    goals_row = next(t for t in out["data"]["tasks"] if t["task"] == "goals")
    assert goals_row["ok"] is True
    assert [c["args"][1] for c in env["calls"] if c["args"][0] == "guard"] == ["snapshot", "check", "check"]


def test_active_goals_manual_are_pending(capsys, env):
    paths.GOALS.write_text("## 甲\n- 状态：进行中\n- 对象：其他 x\n## 乙\n- 状态：暂停\n", encoding="utf-8")
    code, out = run(capsys, "daily", "run")
    assert code == 0
    assert out["data"]["goals_pending"] is True and out["data"]["goals_active"] == 1
    assert "fight 1-7" not in maa_tasks(env) and claude_calls(env) == []
    # award 要在刷理智之后：交给在场的 agent
    assert "award" not in maa_tasks(env) and out["data"]["award_pending"] is True
    assert "领奖励：等目标部分" in read_report(out["data"])


def test_guard_failure_after_agent_stops_run(capsys, env):
    env["behavior"]["fail"] = {"recruit"}
    env["behavior"]["guard_check"] = [3]
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 3 and out["error"]["code"] == "currency_decreased"
    assert maa_tasks(env) == ["startup", "recruit"]                     # 停下，后面的不跑
    d = out["data"]
    assert d["status"] == "stopped" and d["guard"]["ok"] is False and d["guard"]["unexplained"] == {"orundum": -600}
    md = read_report(d)
    assert "停下了" in md and "没经同意的减少" in md


def test_guard_stop_refuses_to_start(capsys, env):
    paths.GUARD.mkdir(parents=True, exist_ok=True)
    (paths.GUARD / "stop.json").write_text("{}")
    code, out = run(capsys, "daily", "run")
    assert code == 3 and out["error"]["code"] == "guard_stop" and env["calls"] == []


def test_only(capsys, env):
    code, out = run(capsys, "daily", "run", "--only", "recruit,farm")
    assert code == 0 and maa_tasks(env) == ["recruit", "fight 1-7"]
    code, out = run(capsys, "daily", "run", "--only", "nope")
    assert code == 2


def test_agent_timeout_kills_it(capsys, env, monkeypatch):
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["daily"]["recovery_timeout_min"] = 0.02                          # 1.2 秒（接管失败项的 agent）
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    env["behavior"]["fail"] = {"startup"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule", "--only", "startup")
    assert code == 1
    agent = out["data"]["tasks"][0]["agent"]
    assert agent["timed_out"] is True and agent["recovered"] is False and agent["duration_s"] < 25


def test_missing_claude_is_reported(capsys, env):
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["tools"]["claude"] = "/nonexistent/claude"
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    env["behavior"]["fail"] = {"startup"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule", "--only", "startup")
    assert code == 1 and "起不来 claude" in out["data"]["tasks"][0]["agent"]["error"]


def test_overlap_and_nested(capsys, env, monkeypatch):
    (paths.PRIVATE / "daily-run.json").write_text(json.dumps({"run_id": "x", "pid": os.getpid(),
                                                              "host": __import__("socket").gethostname()}))
    code, out = run(capsys, "daily", "run")
    assert code == 4 and out["error"]["code"] == "daily_running"
    (paths.PRIVATE / "daily-run.json").write_text(json.dumps({"run_id": "x", "pid": 99999999,
                                                              "host": __import__("socket").gethostname()}))
    code, out = run(capsys, "daily", "run")                             # 残留的标记不挡
    assert code == 0
    monkeypatch.setenv(daily.DAILY_ENV, "20260930-0440")
    code, out = run(capsys, "daily", "run")
    assert code == 2 and out["error"]["code"] == "nested_daily"


def test_busy_emulator_writes_aborted_report(capsys, env, monkeypatch):
    held = lock.acquire("someone")
    try:
        monkeypatch.setattr(lock.os, "getpid", lambda: 4242)             # 别的进程
        monkeypatch.setattr(lock, "pid_alive", lambda pid: True)
        code, out = run(capsys, "daily", "run")
    finally:
        monkeypatch.undo()
        held.release()
    assert code == 4
    code, out = run(capsys, "report", "show")
    assert out["data"]["summary"]["status"] == "aborted" and "没跑完" in out["data"]["markdown"]


def test_status_and_report_write(capsys, env, tmp_path, monkeypatch):
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, "")))
    run(capsys, "daily", "run")
    code, out = run(capsys, "daily", "status")
    assert code == 0 and out["data"]["running"] is None and out["data"]["last"]["ok"] is True
    assert out["data"]["schedule"]["installed"] is False
    rid = out["data"]["last"]["run_id"]
    code, out = run(capsys, "report", "write", rid, "--append", "遥练到了精2 30 级。")
    assert code == 0
    code, out = run(capsys, "report", "show", rid)
    assert "遥练到了精2 30 级。" in out["data"]["markdown"] and out["data"]["summary"]["appendix"]
    code, out = run(capsys, "report", "list")
    assert [r["run_id"] for r in out["data"]["reports"]] == [rid]


def test_parse_transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('noise\n{"type":"assistant"}\n{"type":"result","is_error":true,"result":"结果：没做成"}\n')
    info = daily.parse_transcript(p)
    assert info["is_error"] is True and info["recovered"] is False
    assert daily.parse_transcript(tmp_path / "missing")["summary"] is None


# --- crontab -----------------------------------------------------------------------------------------------------

FAKE_CRONTAB = """
import os, sys
store = os.environ["FAKE_CRONTAB_FILE"]
if sys.argv[1] == "-l":
    if not os.path.exists(store):
        sys.stderr.write("crontab: no crontab for tester\\n"); sys.exit(1)
    sys.stdout.write(open(store, encoding="utf-8").read())
elif sys.argv[1] == "-":
    open(store, "w", encoding="utf-8").write(sys.stdin.read())
"""

OTHER = ("SHELL=/bin/sh\n"
         "*/15 * * * * cd /Users/x/Developer/MAA && .venv/maapilot job tick >> private/log/tick.log 2>&1\n"
         "0 3 * * * /usr/local/bin/backup.sh  # 别人的\n")


def fake_crontab(tmp_path, monkeypatch, initial: str | None):
    store = tmp_path / "crontab.txt"
    if initial is not None:
        store.write_text(initial, encoding="utf-8")
    monkeypatch.setenv("FAKE_CRONTAB_FILE", str(store))
    return script(tmp_path / "crontab", FAKE_CRONTAB)


@pytest.fixture
def cron(tmp_path, monkeypatch):
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\nschedule: ['04:40', '16:40']\n", encoding="utf-8")
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, OTHER)))
    return tmp_path / "crontab.txt"


def test_schedule_keeps_other_lines(capsys, cron):
    code, out = run(capsys, "daily", "schedule")
    assert code == 0, out
    text = cron.read_text(encoding="utf-8")
    assert text.startswith(OTHER)                                        # 别的行一个字节都不动
    mine = text[len(OTHER):].splitlines()
    assert len(mine) == 2 and mine[0].startswith("40 4 * * * PATH=") and mine[1].startswith("40 16 * * * ")
    assert all(ln.endswith(f"# maapilot-v2 {paths.ROOT}") and "daily run --trigger schedule" in ln for ln in mine)
    assert "PYTHONUTF8=1" in mine[0] and str(paths.ROOT / ".venv" / "bin" / "maapilot") in mine[0]
    assert out["data"]["old_entries"] == [OTHER.splitlines()[1]] and out["data"]["removed_old"] == []
    # 重复执行结果相同
    code, out = run(capsys, "daily", "schedule")
    assert cron.read_text(encoding="utf-8") == text and out["data"]["changed"] is False
    # 换时间：只换自己的行
    code, out = run(capsys, "daily", "schedule", "--times", "05:10")
    text2 = cron.read_text(encoding="utf-8")
    assert text2.startswith(OTHER) and text2[len(OTHER):].startswith("10 5 * * * ") and text2.count("maapilot-v2") == 1
    code, out = run(capsys, "daily", "status")
    assert out["data"]["schedule"] == {"installed": True, "times": ["05:10"], "old_entries": [OTHER.splitlines()[1]]}
    assert out["data"]["next_at"]


def test_schedule_dry_run_and_replace_old(capsys, cron):
    code, out = run(capsys, "daily", "schedule", "--dry-run", "--replace-old")
    assert code == 0 and cron.read_text(encoding="utf-8") == OTHER      # 没写
    assert "job tick" not in out["data"]["crontab"] and "backup.sh" in out["data"]["crontab"]
    code, out = run(capsys, "daily", "schedule", "--replace-old")
    text = cron.read_text(encoding="utf-8")
    assert "job tick" not in text and "SHELL=/bin/sh\n0 3 * * * /usr/local/bin/backup.sh  # 别人的\n" in text
    assert out["data"]["removed_old"]


def test_unschedule_only_removes_ours(capsys, cron):
    other_root = "40 4 * * * /elsewhere/maapilot daily run # maapilot-v2 /elsewhere\n"
    cron.write_text(OTHER + other_root, encoding="utf-8")
    run(capsys, "daily", "schedule")
    code, out = run(capsys, "daily", "unschedule", "--dry-run")
    assert code == 0 and len(out["data"]["removed"]) == 2 and "maapilot-v2 /elsewhere" in out["data"]["crontab"]
    code, out = run(capsys, "daily", "unschedule")
    assert cron.read_text(encoding="utf-8") == OTHER + other_root


def test_schedule_bad_time(capsys, cron):
    code, out = run(capsys, "daily", "schedule", "--times", "25:00")
    assert code == 2 and cron.read_text(encoding="utf-8") == OTHER


def test_empty_crontab(capsys, tmp_path, monkeypatch):
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\n", encoding="utf-8")
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, None)))
    code, out = run(capsys, "daily", "schedule", "--times", "04:40")
    assert code == 0 and (tmp_path / "crontab.txt").read_text(encoding="utf-8").count("\n") == 1
    code, out = run(capsys, "daily", "unschedule")
    assert (tmp_path / "crontab.txt").read_text(encoding="utf-8") == ""


def test_annihilation_skipped_when_week_capped(capsys, env):
    from maapilot.tools import maa_cmd
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["daily"]["tasks"] = ["startup", "annihilation", "award"]
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    code, out = run(capsys, "daily", "run")
    assert code == 0 and "annihilation" in maa_tasks(env)
    # 打了、认出了掉落、没有合成玉 → 本周拿满；掉落一样都没认出不下结论
    assert maa_cmd.record_annihilation(1, 0, drops_seen=False)["capped"] is False
    assert maa_cmd.record_annihilation(1, 0, drops_seen=True)["capped"] is True
    env["calls"].clear()
    code, out = run(capsys, "daily", "run")
    assert code == 0 and "annihilation" not in maa_tasks(env)
    row = next(t for t in out["data"]["tasks"] if t["task"] == "annihilation")
    assert row["skipped"] and row["ok"] is True
    assert "没做（本周剿灭的合成玉已经拿满）" in read_report(out["data"])


def test_annihilation_week_resets_and_counts_orundum(monkeypatch):
    from maapilot.tools import maa_cmd
    assert maa_cmd.record_annihilation(1, 1500, drops_seen=True)["capped"] is False
    assert maa_cmd.record_annihilation(1, 300, drops_seen=True)["capped"] is True     # 累计到 1800
    (paths.STATE / "annihilation.json").write_text(json.dumps({"week": "2000-01-03", "runs": 9, "orundum": 1800,
                                                                "capped": True}))
    assert maa_cmd.annihilation_week() == {"week": maa_cmd.annihilation_week()["week"], "runs": 0, "orundum": 0,
                                           "capped": False, "failures": 0}


def test_children_get_run_id(capsys, env, monkeypatch):
    seen = []
    real = daily.maapilot_call

    def spy(args, env_, timeout_s=None):
        seen.append(env_.get("MAAPILOT_RUN_ID"))
        return real(args, env_, timeout_s)
    monkeypatch.setattr(daily, "maapilot_call", spy)
    code, out = run(capsys, "daily", "run")
    assert code == 0 and seen and set(seen) == {out["data"]["run_id"]}


def test_startup_runs_before_guard_snapshot(capsys, env):
    code, out = run(capsys, "daily", "run")
    assert code == 0
    firsts = [" ".join(c["args"]) for c in env["calls"]][:2]
    assert firsts == ["maa startup", "guard snapshot"]


def test_snapshot_that_turned_into_failed_check_stops_run(capsys, env, monkeypatch):
    real = daily.maapilot_call

    def fake(args, env_, timeout_s=None):
        if args == ["guard", "snapshot"]:
            env["calls"].append({"args": args})
            return 3, {"ok": False, "data": {"before": {"orundum": 1000}, "after": {"orundum": 400},
                                             "unexplained": {"orundum": -600}},
                       "error": {"code": "currency_decreased", "message": "合成玉少了 600"}}
        return real(args, env_, timeout_s)
    monkeypatch.setattr(daily, "maapilot_call", fake)
    code, out = run(capsys, "daily", "run")
    assert code == 3 and out["data"]["status"] == "stopped"
    assert maa_tasks(env) == ["startup"]


def test_recovery_prompt_has_real_command_for_farm(capsys, env, monkeypatch):
    env["behavior"]["fail"] = {"fight"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    prompts = [c["argv"][1] for c in claude_calls(env)]
    assert any("maapilot maa fight 1-7" in p for p in prompts)
    assert not any("maa farm" in p for p in prompts)


def test_agent_gets_new_token_afterwards(capsys, env):
    env["behavior"]["fail"] = {"recruit"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    (call,) = claude_calls(env)
    after = [c["token"] for c in env["calls"]][-1]
    assert call["token"] and after and after != call["token"]           # agent 手里的旧 token 不能再进来


def test_leftover_maa_after_agent_is_stopped(capsys, env, monkeypatch):
    monkeypatch.setattr(daily, "LEFTOVER_WAIT_S", 0)
    killed = []
    monkeypatch.setattr(daily, "_leftover_maa", lambda: [99999991] if not killed else [])
    monkeypatch.setattr(daily.os, "kill", lambda pid, sig: killed.append(pid))
    env["behavior"]["fail"] = {"recruit"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert killed == [99999991] and any("maa 进程" in a for a in out["data"]["alerts"])


def test_annihilation_blocked_after_repeated_failures(capsys, env, monkeypatch):
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["daily"]["tasks"] = ["startup", "annihilation", "award"]
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    env["behavior"]["fail"] = {"annihilation"}
    monkeypatch.setenv("FAKE_CLAUDE_RESULT", "剿灭不能代理。\n结果：没做成")
    run(capsys, "daily", "run", "--trigger", "schedule")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert any("这周的日常先不打了" in a for a in out["data"]["alerts"])
    env["calls"].clear()
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert "annihilation" not in maa_tasks(env)
    row = next(t for t in out["data"]["tasks"] if t["task"] == "annihilation")
    assert "没打成" in row["skipped"]


# --- 刷理智的时限按理智算 ------------------------------------------------------------------------------------------

def test_farm_timeout_from_sanity(monkeypatch):
    monkeypatch.setattr(daily, "_stage_cost", lambda stage: (6, stage == "1-7"))
    secs, info = daily.farm_timeout({}, "1-7", 358, "snapshot")          # 超上限也照算
    assert info["fights"] == 60 and info["min"] == 60 * 3 + 15 and secs == info["min"] * 60
    secs, info = daily.farm_timeout({"daily": {"farm_min_per_fight": 2, "farm_timeout_margin_min": 0}}, "ZZ-9", 13, "x")
    assert info["fights"] == 3 and info["min"] == 6 and info["stage_cost_known"] is False
    secs, info = daily.farm_timeout({"daily": {"task_timeout_min": 40}}, "1-7", None, "none")
    assert secs == 40 * 60 and info["basis"] == "task_timeout_min"
    secs, info = daily.farm_timeout({}, "1-7", 0, "snapshot")             # 没理智也给一次的时间
    assert info["fights"] == 1


def test_sanity_estimate_regenerates_below_cap():
    from maapilot.tools import common
    at = (now() - timedelta(minutes=120)).isoformat(timespec="seconds")
    assert daily._sanity_estimate({"at": at, "current": 100, "max": 205}) == 120
    assert daily._sanity_estimate({"at": at, "current": 200, "max": 205}) == 205
    assert daily._sanity_estimate({"at": at, "current": 358, "max": 205}) == 358
    assert daily._sanity_estimate(None) is None


def test_read_sanity_for_farm_sources(monkeypatch):
    from maapilot.tools import common
    calls = []

    def fake(args, env, timeout_s=None):
        calls.append(args)
        return (0, {"ok": True, "data": {"current": 77, "max": 205}}) if fake.ok else (1, {"ok": False, "data": None})
    fake.ok = True
    monkeypatch.setattr(daily, "maapilot_call", fake)
    common.write_json(paths.STATE / "sanity.json", {"at": common.iso(), "current": 50, "max": 205})
    assert daily.read_sanity_for_farm({}) == (50, "snapshot") and calls == []
    old = (now() - timedelta(hours=5)).isoformat(timespec="seconds")
    common.write_json(paths.STATE / "sanity.json", {"at": old, "current": 50, "max": 205})
    assert daily.read_sanity_for_farm({}) == (77, "state sanity") and calls == [["state", "sanity"]]
    fake.ok = False
    assert daily.read_sanity_for_farm({}) == (100, "old snapshot")    # 50 + 300 分钟 / 6


def test_daily_farm_uses_sanity_timeout(capsys, env, monkeypatch):
    """没有目标时刷 default_stage：先读理智，按理智给 maa fight 时限，记在这一项的 timeout 里。"""
    from maapilot.tools import common
    common.write_json(paths.STATE / "sanity.json", {"at": common.iso(), "current": 30, "max": 205})
    monkeypatch.setattr(daily, "_stage_cost", lambda stage: (6, True))
    inner, seen = daily.maapilot_call, {}

    def spy(args, env_, timeout_s=None):
        if args[:2] == ["maa", "fight"]:
            seen["timeout_s"] = timeout_s
        return inner(args, env_, timeout_s)
    monkeypatch.setattr(daily, "maapilot_call", spy)
    code, out = run(capsys, "daily", "run")
    assert code == 0, out
    assert seen["timeout_s"] == (5 * 3 + 15) * 60
    farm = next(t for t in out["data"]["tasks"] if t["task"] == "farm")
    assert farm["timeout"]["fights"] == 5 and farm["timeout"]["sanity_source"] == "snapshot"


# --- 识别干员练度（每个游戏日一次） ---------------------------------------------------------------------------------

def all_cmds(env):
    return [" ".join(c["args"]) for c in env["calls"] if c["args"][0] in ("maa", "state")]


def test_box_read_once_per_game_day(capsys, env):
    code, out = run(capsys, "daily", "run")
    assert code == 0, out
    cmds = all_cmds(env)
    # 其余 MAA 项之后、刷理智之前
    assert cmds.index("state box") > cmds.index("maa infrast") and cmds.index("state box") < cmds.index("maa fight 1-7")
    box = next(t for t in out["data"]["tasks"] if t["task"] == "box")
    assert box["ok"] is True and "skipped" not in box
    # 今天（游戏日）读过了：跳过
    from maapilot.tools import common
    common.write_json(paths.STATE / "box.json", {"at": common.iso(), "operators": {}})
    env["calls"].clear()
    code, out = run(capsys, "daily", "run")
    assert "state box" not in all_cmds(env)
    box = next(t for t in out["data"]["tasks"] if t["task"] == "box")
    assert box["skipped"].startswith("今天（游戏日）已经读过") and "识别干员练度" in read_report(out["data"])


def test_box_fresh_uses_game_day_boundary(monkeypatch):
    from datetime import datetime
    from maapilot.tools import common
    from maapilot.knowledge import calendar
    t = calendar.to_game_tz(datetime(2026, 10, 5, 10, 0).astimezone())
    monkeypatch.setattr(daily, "now", lambda: t)
    common.write_json(paths.STATE / "box.json", {"at": t.replace(hour=4, minute=10).isoformat()})
    assert daily._box_fresh()
    common.write_json(paths.STATE / "box.json", {"at": t.replace(hour=3, minute=50).isoformat()})   # 昨天的游戏日
    assert daily._box_fresh() is None


def test_box_disabled_and_only(capsys, env):
    code, out = run(capsys, "daily", "run", "--only", "box")
    assert code == 0, out
    assert all_cmds(env) == ["state box"]
    env["calls"].clear()
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["daily"]["read_box"] = False
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    code, out = run(capsys, "daily", "run")
    assert code == 0 and "state box" not in all_cmds(env)
    assert not any(t["task"] == "box" for t in out["data"]["tasks"])


def test_box_failure_hands_over_in_session(capsys, env):
    env["behavior"]["fail"] = {"box"}
    code, out = run(capsys, "daily", "run")
    assert code == 1 and out["error"]["code"] == "handoff"
    h = out["data"]["handoff"]
    assert h["task"] == "box" and h["rest"][:2] == ["box", "farm"]
    assert "maa fight 1-7" not in all_cmds(env)
    assert not (paths.STATE / "box.json").exists()                      # 失败不写 box.json


def test_box_failure_scheduled_recovery_prompt(capsys, env):
    env["behavior"]["fail"] = {"box"}
    run(capsys, "daily", "run", "--trigger", "schedule")
    (call,) = claude_calls(env)
    prompt = call["argv"][1]
    assert "识别干员练度" in prompt and "maapilot state box" in prompt and "maapilot maa box" not in prompt
    assert "maa why" not in prompt


def test_box_has_its_own_timeout(capsys, env, monkeypatch):
    inner, seen = daily.maapilot_call, {}

    def spy(args, env_, timeout_s=None):
        if args[:2] == ["state", "box"]:
            seen["t"] = timeout_s
        return inner(args, env_, timeout_s)
    monkeypatch.setattr(daily, "maapilot_call", spy)
    run(capsys, "daily", "run")
    assert seen["t"] == 15 * 60


def _skland_configured():
    from maapilot.tools import skland_cmd
    skland_cmd.save_store({"token": "T" * 30, "did": "B", "uid": "1"})


def test_box_uses_skland_when_configured(capsys, env):
    _skland_configured()
    code, out = run(capsys, "daily", "run")
    assert code == 0, out
    cmds = all_cmds(env)
    assert "state skland" in cmds and "state box" not in cmds
    box = next(t for t in out["data"]["tasks"] if t["task"] == "box")
    assert box["ok"] is True and box["source"] == "skland" and out["data"]["alerts"] == []


def test_box_falls_back_to_maa_when_skland_fails(capsys, env):
    _skland_configured()
    env["behavior"]["fail"] = {"skland"}
    code, out = run(capsys, "daily", "run")
    assert code == 0, out                                              # 不算失败
    cmds = all_cmds(env)
    assert cmds.index("state skland") < cmds.index("state box")
    assert any("森空岛读练度没成" in a and "改用 MAA 识别" in a for a in out["data"]["alerts"])
    assert out["data"]["failed"] == []
