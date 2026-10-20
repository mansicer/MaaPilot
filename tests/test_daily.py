"""daily run / done / status：不碰模拟器、不改真的 crontab。maapilot 子命令换成进程内的假实现；crontab 是一个假脚本。
agent 一直在场：要判断的事都停下交回它；没收尾的日常（open daily）由 `daily done` 收尾，`--only` 接着写同一份汇报。"""
import json
import os
import signal
import sys
import time
from datetime import timedelta

import pytest
import yaml

from maapilot import cli, paths
from maapilot.crew import lock
from maapilot.flows import daily
from maapilot.flows import report as reportmod
from maapilot.flows._common import now
from tests.portable import fake_program

script = fake_program


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def close(capsys, summary="日常做完了。"):
    """agent 收尾（daily done）。"""
    code, out = run(capsys, "daily", "done", "--summary", summary)
    assert code == 0, out
    return out


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
    """配置、假的 maapilot 子命令。返回 calls 列表与可调的行为表。"""
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    cfg = {"emulator": {"address": "emu:1"}, "tools": {},
           "daily": {"tasks": ["startup", "recruit", "infrast", "award"], "default_stage": "1-7"}}
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    calls, behavior = [], {"fail": set(), "guard_check": [], "depot": [{"4001": 100000, "30012": 5},
                                                                      {"4001": 88000, "30012": 19}]}

    def fake(args, env_, timeout_s=None):
        calls.append({"args": args, "token": env_.get(lock.TOKEN_ENV)})
        # 子进程里的命令拿得到嵌套锁
        assert env_.get(lock.TOKEN_ENV) == lock.current()["token"]
        if args[0] == "guard":
            snap = behavior["depot"].pop(0) if behavior["depot"] else None
            if snap is not None:
                paths.STATE.mkdir(parents=True, exist_ok=True)
                (paths.STATE / "depot.json").write_text(json.dumps({"at": f"t{len(calls)}", "items": snap}), encoding="utf-8")
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
    # 开始前的检查：不联网、不碰模拟器（各测试按需改 behavior）
    behavior.update(news_error=None, maintenance=None, version=None, version_error=None, versions=[])
    news_calls = []

    def fake_refresh():
        news_calls.append("refresh")
        if behavior["news_error"]:
            raise behavior["news_error"]
        return True

    def fake_version():
        if behavior["version_error"]:
            raise behavior["version_error"]
        if behavior["versions"]:
            return behavior["versions"].pop(0)
        return behavior["version"] or {"installed": {"version": "2.6.01"}, "latest": {"version": "2.6.01"},
                                       "update_needed": False, "accept_terms": None}

    monkeypatch.setattr(daily, "refresh_news", fake_refresh)
    monkeypatch.setattr(daily, "maintenance_now", lambda: behavior["maintenance"])
    monkeypatch.setattr(daily, "version_info", fake_version)
    return {"calls": calls, "behavior": behavior, "news_calls": news_calls}


def read_report(d):
    return (paths.ROOT / d["report"]).read_text(encoding="utf-8")     # 绝对路径时 / 取右边


def maa_tasks(env):
    return [" ".join(c["args"][1:]) for c in env["calls"] if c["args"][0] == "maa"]


GOAL = "## 遥 精2\n- 状态：进行中\n- 对象：干员 遥 → 精2 1级\n- 原话：x\n- 同意：y\n- 花合成玉/源石：否\n"


def test_all_ok_no_goals_fights_default_stage(capsys, env):
    code, out = run(capsys, "daily", "run")
    assert code == 0, out
    d = out["data"]
    assert maa_tasks(env) == ["startup", "recruit", "infrast", "fight 1-7", "award"]
    assert [c["args"] for c in env["calls"] if c["args"][0] == "guard"] == [["guard", "snapshot"], ["guard", "check"]]
    assert d["ok"] is True and d["failed"] == [] and d["guard"] == {"ok": True} and d["trigger"] == "manual"
    assert d["farm"]["stage"] == "1-7" and d["farm"]["times_done"] == 12
    assert {"id": "30012", "name": d["depot_change"][1]["name"], "delta": 14} in d["depot_change"]
    md = read_report(d)
    assert "全部做完" in md and "1-7 打了 12 次" in md and "花掉：" in md and "（手动）" in md
    assert not (paths.PRIVATE / "daily-run.json").exists() and lock.current() is None
    assert (paths.LOGS / f"daily-{d['run_id']}.jsonl").exists()
    # 进程结束了，这次日常还没收尾：等 agent daily done
    assert d["open"] is True and "daily done" in d["next"]
    assert daily.open_daily()["run_id"] == d["run_id"]


def test_schedule_trigger_is_only_a_label(capsys, env):
    """--trigger schedule：做法和手动一样（失败照样停下交回 agent），只写进汇报。"""
    env["behavior"]["fail"] = {"infrast"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 1 and out["error"]["code"] == "handoff"
    d = out["data"]
    assert d["trigger"] == "schedule" and d["status"] == "handed_off" and "award" not in maa_tasks(env)
    assert read_report(d).startswith("# 日常汇报：") and "（定时）" in read_report(d).splitlines()[0]
    assert "定时日常" in d["lines"][0]


def test_removed_flags(capsys, env):
    for flag in ("--interactive", "--no-agent"):
        code, out = run(capsys, "daily", "run", flag)
        assert code == 2 and out["error"]["code"] == "bad_arguments"
    assert env["calls"] == []


def test_manual_failure_stops_and_hands_over(capsys, env):
    """第一项失败就停下交给在场的 agent，后面的不跑；hint 里是接着跑的命令。"""
    env["behavior"]["fail"] = {"infrast"}
    code, out = run(capsys, "daily", "run")
    assert code == 1 and out["error"]["code"] == "handoff"
    d = out["data"]
    assert d["status"] == "handed_off"
    assert d["handoff"]["task"] == "infrast" and d["handoff"]["rest"][0] == "infrast"
    assert "award" not in maa_tasks(env) and "award" in d["handoff"]["rest"]
    assert "--only " + ",".join(d["handoff"]["rest"]) in out["error"]["hint"]
    assert "--only " + ",".join(d["handoff"]["rest"]) in d["next"] and "maapilot-recovery" in d["next"]
    assert "交给 agent 处理" in read_report(d)


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


def test_offline_also_hands_over(capsys, env):
    env["behavior"]["fail"] = {"recruit"}
    env["behavior"]["fail_code"] = "offline"
    code, out = run(capsys, "daily", "run")
    assert code == 1 and out["data"]["handoff"]["task"] == "recruit"


def test_active_goals_are_pending(capsys, env):
    paths.GOALS.write_text("## 甲\n- 状态：进行中\n- 对象：其他 x\n## 乙\n- 状态：暂停\n", encoding="utf-8")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0
    d = out["data"]
    assert d["goals_pending"] is True and d["goals_active"] == 1
    assert "fight 1-7" not in maa_tasks(env)
    # award 要在刷理智之后：交给在场的 agent
    assert "award" not in maa_tasks(env) and d["award_pending"] is True
    assert "领奖励：等目标部分" in read_report(d)
    assert "maapilot-daily" in d["next"] and "maa award" in d["next"] and "guard check" in d["next"]


def test_guard_failure_stops_run(capsys, env):
    env["behavior"]["guard_check"] = [3]
    code, out = run(capsys, "daily", "run")
    assert code == 3 and out["error"]["code"] == "currency_decreased"
    d = out["data"]
    assert d["status"] == "stopped" and d["guard"]["ok"] is False and d["guard"]["unexplained"] == {"orundum": -600}
    md = read_report(d)
    assert "停下了" in md and "没经同意的减少" in md
    assert "货币对账停机" in d["next"] and d["open"] is True


def test_guard_stop_refuses_to_start(capsys, env):
    paths.GUARD.mkdir(parents=True, exist_ok=True)
    (paths.GUARD / "stop.json").write_text("{}", encoding="utf-8")
    code, out = run(capsys, "daily", "run")
    assert code == 3 and out["error"]["code"] == "guard_stop" and env["calls"] == []
    assert daily.open_daily() is None


def test_only(capsys, env):
    code, out = run(capsys, "daily", "run", "--only", "recruit,farm")
    assert code == 0 and maa_tasks(env) == ["recruit", "fight 1-7"]
    code, out = run(capsys, "daily", "run", "--only", "nope")
    assert code == 2


def test_overlap(capsys, env, monkeypatch):
    (paths.PRIVATE / "daily-run.json").write_text(json.dumps({"run_id": "x", "pid": os.getpid(),
                                                              "host": __import__("socket").gethostname()}), encoding="utf-8")
    code, out = run(capsys, "daily", "run")
    assert code == 4 and out["error"]["code"] == "daily_running" and daily.open_daily() is None
    (paths.PRIVATE / "daily-run.json").write_text(json.dumps({"run_id": "x", "pid": 99999999,
                                                              "host": __import__("socket").gethostname()}), encoding="utf-8")
    code, out = run(capsys, "daily", "run")                             # 残留的标记不挡
    assert code == 0
    monkeypatch.setenv("MAAPILOT_DAILY_RUN", "20260930-0440")         # 旧版的环境变量不再看
    close(capsys)
    code, out = run(capsys, "daily", "run")
    assert code == 0


def test_busy_emulator_writes_aborted_report_and_leaves_nothing_open(capsys, env, monkeypatch):
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
    assert out["data"]["summary"]["open"] is False and daily.open_daily() is None   # 什么都没做：不留"没收尾"


def test_status_and_report_write(capsys, env):
    run(capsys, "daily", "run")
    code, out = run(capsys, "daily", "status")
    d = out["data"]
    assert code == 0 and d["running"] is None and d["last"]["ok"] is True and d["last"]["open"] is True
    assert d["open"]["run_id"] == d["last"]["run_id"] and d["open"]["stale"] is False and d["open"]["trigger"] == "manual"
    assert "schedule" not in d and "next_at" not in d
    rid = d["last"]["run_id"]
    code, out = run(capsys, "report", "write", rid, "--append", "遥练到了精2 30 级。")
    assert code == 0
    code, out = run(capsys, "report", "show", rid)
    assert "遥练到了精2 30 级。" in out["data"]["markdown"] and out["data"]["summary"]["appendix"]
    assert "## 补充" in out["data"]["markdown"] and "## 目标部分" not in out["data"]["markdown"]
    code, out = run(capsys, "report", "list")
    assert [r["run_id"] for r in out["data"]["reports"]] == [rid]


# --- 没收尾的日常：--only 接着跑、拒绝、6 小时自动收尾、daily done ------------------------------------------------------

def test_only_continues_the_open_daily(capsys, env):
    """handoff 后 --only <rest> 接着写同一个运行编号、同一份汇报；重跑成了的项从 failed 里拿掉。"""
    env["behavior"]["fail"] = {"infrast"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    first = out["data"]
    assert code == 1 and first["failed"] == ["infrast"]
    rest = first["handoff"]["rest"]
    env["behavior"]["fail"] = set()
    env["behavior"]["depot"] = [{"4001": 90000, "30012": 7}, {"4001": 80000, "30012": 30}]
    code, out = run(capsys, "daily", "run", "--only", ",".join(rest))
    assert code == 0, out
    d = out["data"]
    assert d["run_id"] == first["run_id"] and d["report"] == first["report"] and d["segments"] == 2
    assert d["trigger"] == "schedule" and d["started_at"] == first["started_at"]       # 还是那次定时日常
    assert d["failed"] == [] and d["ok"] is True and d["status"] == "done" and "handoff" not in d
    assert [t["task"] for t in d["tasks"]] == ["startup", "recruit", "infrast", "infrast", "box", "farm", "award"]
    assert d["tasks"][2]["retried"] is True and d["tasks"][3]["ok"] is True
    assert maa_tasks(env)[-3:] == ["infrast", "fight 1-7", "award"]
    # 仓库差按这次日常的第一份算（第一段开头的快照 → 第二段结尾）
    assert {"id": "30012", "name": next(c["name"] for c in d["depot_change"] if c["id"] == "30012"), "delta": 25} \
        in d["depot_change"]
    assert len(reportmod.runs()) == 1
    md = read_report(d)
    assert "没做成——infrast 卡住了（后面重跑了）" in md and "全部做完" in md


def test_plain_run_refused_while_open(capsys, env):
    code, out = run(capsys, "daily", "run")
    rid = out["data"]["run_id"]
    n = len(env["calls"])
    code, out = run(capsys, "daily", "run")
    assert code == 2 and out["error"]["code"] == "daily_open" and rid in out["error"]["message"]
    assert "daily done" in out["error"]["hint"] and "--only" in out["error"]["hint"]
    assert out["data"]["open"]["run_id"] == rid and len(env["calls"]) == n
    close(capsys)
    code, out = run(capsys, "daily", "run")
    assert code == 0 and out["data"]["run_id"] != rid


def test_stale_open_daily_auto_closed(capsys, env):
    """超过 6 小时没收尾的算放弃：自动收尾（汇报里加一条告警），再开新的。"""
    code, out = run(capsys, "daily", "run")
    old = out["data"]["run_id"]
    op = daily.open_daily()
    daily._write_open({**op, "started_at": (now() - timedelta(hours=7)).isoformat(timespec="seconds")})
    assert daily.open_daily()["stale"] is True
    code, out = run(capsys, "daily", "run")
    assert code == 0 and out["data"]["run_id"] != old
    o = reportmod.load(old)
    assert o["open"] is False and o["closed_by"] == "auto" and any("没有收尾" in a for a in o["alerts"])
    assert "没有收尾" in reportmod.md_path(old).read_text(encoding="utf-8")
    assert daily.open_daily()["run_id"] == out["data"]["run_id"]


def test_stale_open_with_only_starts_fresh(capsys, env):
    code, out = run(capsys, "daily", "run")
    old = out["data"]["run_id"]
    daily._write_open({**daily.open_daily(), "started_at": (now() - timedelta(hours=8)).isoformat(timespec="seconds")})
    code, out = run(capsys, "daily", "run", "--only", "recruit")
    assert code == 0 and out["data"]["run_id"] != old and "segments" not in out["data"]


def test_daily_done_closes_and_writes_goals_section(capsys, env):
    paths.GOALS.write_text(GOAL, encoding="utf-8")
    code, out = run(capsys, "daily", "run")
    rid = out["data"]["run_id"]
    summary = "刷了 6 次 CE-6，遥到精2 了。\n\n- 花了 40 万龙门币\n\n结果：完成"
    code, out = run(capsys, "daily", "done", "--summary", summary, "--result", "完成")
    assert code == 0, out
    assert out["data"]["run_id"] == rid and out["data"]["closed"] is True and daily.open_daily() is None
    d = reportmod.load(rid)
    assert d["open"] is False and d["closed_by"] == "agent" and d["goals_summary"]["text"].endswith("结果：完成")
    assert "next" not in d
    md = reportmod.md_path(rid).read_text(encoding="utf-8")
    sec = md.split("## 目标部分")[1]
    assert "刷了 6 次 CE-6" in sec and "- 花了 40 万龙门币" in sec and "结果：" not in md and "还没收尾" not in md
    assert "见下面「目标部分」" in md
    code, out = run(capsys, "daily", "done", "--summary", "再交一次")              # 已经收尾了
    assert code == 2 and out["error"]["code"] == "not_in_daily"


def test_daily_done_without_goals(capsys, env):
    """没有目标（只有固定日常）也照样用 daily done 收尾。"""
    code, out = run(capsys, "daily", "run")
    rid = out["data"]["run_id"]
    close(capsys, "固定日常都做完了，刷了 12 次 1-7。")
    md = reportmod.md_path(rid).read_text(encoding="utf-8")
    assert "## 目标部分" in md and "刷了 12 次 1-7" in md


def test_daily_done_errors(capsys, env, tmp_path):
    code, out = run(capsys, "daily", "done", "--summary", "x")
    assert code == 2 and out["error"]["code"] == "not_in_daily"
    run(capsys, "daily", "run")
    code, out = run(capsys, "daily", "done")
    assert code == 2 and out["error"]["code"] == "bad_arguments"
    code, out = run(capsys, "daily", "done", "--summary", "   ")
    assert code == 2
    f = tmp_path / "s.txt"
    f.write_text("从文件读的总结", encoding="utf-8")
    (paths.PRIVATE / "daily-run.json").write_text(json.dumps({"run_id": "x", "pid": os.getpid(),
                                                              "host": __import__("socket").gethostname()}), encoding="utf-8")
    code, out = run(capsys, "daily", "done", "--summary-file", str(f))              # daily run 还在跑：跑完再收尾
    assert code == 4 and out["error"]["code"] == "daily_running"
    (paths.PRIVATE / "daily-run.json").unlink()
    code, out = run(capsys, "daily", "done", "--summary-file", str(f))
    assert code == 0 and out["data"]["chars"] == len("从文件读的总结")


def test_daily_done_truncates_long_summary(capsys, env):
    run(capsys, "daily", "run")
    code, out = run(capsys, "daily", "done", "--summary", "字" * 30000 + "\n结果：完成")
    assert code == 0 and out["data"]["truncated"] is True and out["data"]["chars"] <= 20000


# --- 剿灭 ------------------------------------------------------------------------------------------------------------

def test_annihilation_skipped_when_week_capped(capsys, env):
    from maapilot.tools import maa_cmd
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["daily"]["tasks"] = ["startup", "annihilation", "award"]
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    code, out = run(capsys, "daily", "run")
    assert code == 0 and "annihilation" in maa_tasks(env)
    close(capsys)
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
                                                                "capped": True}), encoding="utf-8")
    assert maa_cmd.annihilation_week() == {"week": maa_cmd.annihilation_week()["week"], "runs": 0, "orundum": 0,
                                           "capped": False}


def test_annihilation_failures_always_handed_back(capsys, env):
    """剿灭没打成每次都交回 agent（原因由它判断），程序不计数、不整周跳过（10-20 用户定）。"""
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["daily"]["tasks"] = ["startup", "annihilation", "award"]
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    env["behavior"]["fail"] = {"annihilation"}
    for _ in range(3):
        code, out = run(capsys, "daily", "run") if _ == 0 else run(capsys, "daily", "run", "--only", "annihilation")
        assert code == 1 and out["data"]["handoff"]["task"] == "annihilation"
    assert "annihilation" in maa_tasks(env)
    from maapilot.tools import maa_cmd
    assert "blocked" not in maa_cmd.annihilation_week() and "failures" not in maa_cmd.annihilation_week()


# --- 子进程 ----------------------------------------------------------------------------------------------------------

def test_children_get_run_id(capsys, env, monkeypatch):
    seen = []
    real = daily.maapilot_call

    def spy(args, env_, timeout_s=None):
        seen.append(env_.get("MAAPILOT_RUN_ID"))
        assert "MAAPILOT_DAILY_RUN" not in env_                          # 旧版给无头 agent 的标记不再设
        return real(args, env_, timeout_s)
    monkeypatch.setattr(daily, "maapilot_call", spy)
    code, out = run(capsys, "daily", "run")
    assert code == 0 and seen and set(seen) == {out["data"]["run_id"]}


def test_startup_runs_before_guard_snapshot(capsys, env):
    code, out = run(capsys, "daily", "run")
    assert code == 0
    firsts = [" ".join(c["args"]) for c in env["calls"]][:2]
    assert firsts == ["maa startup", "guard snapshot"]


def test_failed_snapshot_retried_after_startup(capsys, env, monkeypatch):
    real = daily.maapilot_call
    fails = [1]

    def fake(args, env_, timeout_s=None):
        if args == ["guard", "snapshot"] and fails:
            fails.pop()
            env["calls"].append({"args": args})
            return 1, {"ok": False, "data": None, "error": {"code": "depot_failed", "message": "仓库识别失败"}}
        return real(args, env_, timeout_s)
    monkeypatch.setattr(daily, "maapilot_call", fake)
    code, out = run(capsys, "daily", "run")
    assert code == 0
    firsts = [" ".join(c["args"]) for c in env["calls"]][:4]
    assert firsts == ["maa startup", "guard snapshot", "maa startup", "guard snapshot"]
    assert not any("没能记下" in a for a in out["data"]["alerts"])


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


def test_task_timeout_stops_leftover_maa(capsys, env, monkeypatch):
    """任务超时、maapilot maa 被杀之后 maa 还在：日常结束掉它，告警里写明。"""
    killed = []
    monkeypatch.setattr(daily, "TIMEOUT_LEFTOVER_WAIT_S", 0)
    monkeypatch.setattr(daily, "_leftover_maa", lambda: [99999991] if not killed else [])
    monkeypatch.setattr(daily.procs, "kill_pids", lambda pids, grace_s: killed.extend(pids))
    env["behavior"]["fail"] = {"recruit"}
    env["behavior"]["fail_code"] = "timeout"
    code, out = run(capsys, "daily", "run")
    assert killed == [99999991] and any("maa 进程" in a for a in out["data"]["alerts"])


def _wait_dead(pid, s=10.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(sys.platform == "win32", reason="脚本靠 shebang 跑、POSIX 进程组")
def test_timeout_kills_groups_even_if_main_exits_on_term(tmp_path):
    """主进程收到 TERM 就走了，子孙另起的组不理 TERM：最后也要 KILL 掉。"""
    pidfile = tmp_path / "child.pid"
    parent = script(tmp_path / "parent", f"""
import subprocess, sys, time
p = subprocess.Popen([sys.executable, "-c",
                      "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
                     start_new_session=True)
open({str(pidfile)!r}, "w").write(str(p.pid))
time.sleep(60)
""")
    code, _, timed_out = daily._run_group([str(parent)], env=dict(os.environ), timeout_s=3)
    child = int(pidfile.read_text(encoding="utf-8"))
    try:
        assert timed_out and _wait_dead(child)
    finally:
        try:
            os.kill(child, signal.SIGKILL)
        except OSError:
            pass


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
    close(capsys)
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
    close(capsys)
    env["calls"].clear()
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg["daily"]["read_box"] = False
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    code, out = run(capsys, "daily", "run")
    assert code == 0 and "state box" not in all_cmds(env)
    assert not any(t["task"] == "box" for t in out["data"]["tasks"])


def test_box_failure_hands_over(capsys, env):
    env["behavior"]["fail"] = {"box"}
    code, out = run(capsys, "daily", "run")
    assert code == 1 and out["error"]["code"] == "handoff"
    h = out["data"]["handoff"]
    assert h["task"] == "box" and h["rest"][:2] == ["box", "farm"]
    assert "maa fight 1-7" not in all_cmds(env)
    assert not (paths.STATE / "box.json").exists()                      # 失败不写 box.json


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


# --- 开始前：公告、维护、游戏版本、限时目标 ----------------------------------------------------------------------------

MAINT = {"cid": "9001", "title": "10月9日闪断更新公告", "start": "2026-10-09T06:00:00+08:00",
         "end": "2026-10-09T12:00:00+08:00", "forced_update": True}
OLD = {"installed": {"version": "2.6.01", "code": 100}, "latest": {"version": "2.6.21", "url": "u", "size": 1},
       "update_needed": True, "accept_terms": False}
NEW = {**OLD, "installed": {"version": "2.6.21", "code": 101}, "update_needed": False}


@pytest.mark.parametrize("trigger", ["manual", "schedule"])
def test_maintenance_skips_everything(capsys, env, trigger):
    env["behavior"]["maintenance"] = MAINT
    code, out = run(capsys, "daily", "run", "--trigger", trigger)
    assert code == 0, out
    d = out["data"]
    assert d["status"] == "maintenance" and d["ok"] is True and d["failed"] == []
    assert d["maintenance"] == MAINT and d["tasks"] == []
    assert env["calls"] == []                                         # 没跑 MAA、没对账（agent 就在场）
    assert d["lines"][1] == "官方停机维护中（10月9日 06:00–12:00），这次没跑。维护后要强制更新游戏。"
    assert reportmod.has_problem(d) is False
    md = read_report(d)
    assert "官方停机维护中（10月9日 06:00–12:00），这次没跑。维护后要强制更新游戏。" in md
    assert "仓库变化" not in md and "没能核对" not in md
    assert "赶上官方停机维护" in reportmod.summary_lines(d)[0]
    assert d["open"] is True                                          # agent 告诉用户后 daily done 收尾


def test_news_due_in_result(capsys, env, monkeypatch):
    from maapilot.tools import doctor
    monkeypatch.setattr(doctor, "news_brief", lambda: {"at": "t", "lines": ["10月9日 12:00 开始：昨日海"],
                                                       "due": ["6774:公告"]})
    code, out = run(capsys, "daily", "run")
    assert code == 0 and out["data"]["news"]["due"] == ["6774:公告"]
    assert any("官方日程有 1 条消息" in ln for ln in out["data"]["lines"])


def test_news_refresh_failure_is_not_fatal(capsys, env):
    env["behavior"]["news_error"] = OSError("连不上")
    code, out = run(capsys, "daily", "run")
    assert code == 0, out
    assert env["news_calls"] == ["refresh"]
    assert maa_tasks(env) == ["startup", "recruit", "infrast", "fight 1-7", "award"]
    assert any("官方公告没拉下来" in a and "连不上" in a for a in out["data"]["alerts"])


def test_refresh_news_only_when_stale(monkeypatch):
    from maapilot.knowledge import news
    calls = []
    monkeypatch.setattr(news, "refresh", lambda: calls.append(1))
    monkeypatch.setattr(news, "stale", lambda: False)
    assert daily.refresh_news() is False and calls == []
    monkeypatch.setattr(news, "stale", lambda: True)
    assert daily.refresh_news() is True and calls == [1]


def test_version_info_failure_is_not_fatal(capsys, env):
    from maapilot.cliutil import CliError
    env["behavior"]["version_error"] = CliError("offline", "连不上模拟器")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    assert maa_tasks(env) == ["startup", "recruit", "infrast", "fight 1-7", "award"]
    assert any("没查到游戏版本" in a for a in out["data"]["alerts"])


@pytest.mark.parametrize("trigger", ["manual", "schedule"])
def test_update_needed_hands_off_then_continues(capsys, env, trigger):
    env["behavior"]["versions"] = [OLD, NEW]
    code, out = run(capsys, "daily", "run", "--trigger", trigger, "--only", "recruit,farm")
    assert code == 1 and out["error"]["code"] == "handoff"
    d = out["data"]
    assert d["status"] == "handed_off" and maa_tasks(env) == []
    h = d["handoff"]
    assert h["task"] == "game_update" and h["error"] == "游戏客户端要更新（2.6.01 → 2.6.21）"
    assert h["rest"] == ["recruit", "farm"]
    assert "maapilot game update" in out["error"]["hint"] and "--only recruit,farm" in out["error"]["hint"]
    assert "maapilot-events" in d["next"] and "--only recruit,farm" in d["next"]
    assert "停下交给 agent 更新" in read_report(d)
    # agent 更新好了，接着跑：同一份汇报
    code, out = run(capsys, "daily", "run", "--only", "recruit,farm")
    assert code == 0, out
    assert out["data"]["run_id"] == d["run_id"] and maa_tasks(env) == ["recruit", "fight 1-7"]
    assert out["data"]["game_version"]["update_needed"] is False


EXPIRED = [{"title": "打完活动 SV-8"}]


@pytest.fixture
def no_goal_snapshot(monkeypatch):
    """goals.snapshot 还没有时的样子：只靠 expire_due 的返回值。"""
    from maapilot.flows import goals
    monkeypatch.setattr(goals, "snapshot", None, raising=False)
    monkeypatch.setattr(goals, "expire_due", lambda: EXPIRED, raising=False)


@pytest.mark.parametrize("trigger", ["manual", "schedule"])
def test_expired_goals_listed_for_the_agent(capsys, env, no_goal_snapshot, trigger):
    code, out = run(capsys, "daily", "run", "--trigger", trigger)
    assert code == 0, out
    d = out["data"]
    assert d["goals_expired"] == EXPIRED
    md = read_report(d)
    assert "## 目标变动" in md and "- 移除：打完活动 SV-8（到期）" in md
    assert "目标移除：打完活动 SV-8（到期）" in d["lines"]
    assert any("活动商店" in ln for ln in d["lines"])                 # 定时的也写（以前由无头 agent 看）
    assert reportmod.has_problem(d) is False


def test_goals_change_recorded_and_relayed(capsys, env, monkeypatch):
    from maapilot.flows import goals
    snaps = iter([{"n": 1}, {"n": 2}])
    seen = []
    change = {"removed": [{"title": "打完活动 SV-8", "fields": {},
                           "done_entry": {"status": "到期", "ended": "2026-10-09 到期，期限 2026-10-09 04:00 已过，自动结束"}},
                          {"title": "遥 精2", "fields": {}, "done_entry": {"status": "完成", "ended": "2026-10-09 完成，精2 1级"}}],
              "added": [{"title": "新目标 甲"}],
              "changed": [{"title": "乙", "field": "status", "before": "进行中", "after": "暂停"}]}

    def fake_diff(a, b):
        seen.append((a, b))
        return change
    monkeypatch.setattr(goals, "snapshot", lambda: next(snaps), raising=False)
    monkeypatch.setattr(goals, "diff", fake_diff, raising=False)
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    d = out["data"]
    assert seen == [({"n": 1}, {"n": 2})] and d["goals_change"] == change
    md = read_report(d)
    assert "## 目标变动" in md
    assert "- 移除：打完活动 SV-8（到期，期限 2026-10-09 04:00 已过，自动结束）" in md
    assert "- 移除：遥 精2（完成，精2 1级）" in md and "- 新加：新目标 甲" in md
    assert "- 改了：乙 的状态：进行中 → 暂停" in md
    assert reportmod.has_problem(d) is False
    lines = reportmod.summary_lines(d)
    assert "目标移除：遥 精2（完成，精2 1级）" in lines and "目标改了：乙 的状态：进行中 → 暂停" in lines


def test_goals_unchanged_or_diff_error(capsys, env, monkeypatch):
    from maapilot.flows import goals
    monkeypatch.setattr(goals, "snapshot", lambda: {}, raising=False)
    monkeypatch.setattr(goals, "diff", lambda a, b: {"removed": [], "added": [], "changed": []}, raising=False)
    code, out = run(capsys, "daily", "run")
    assert code == 0 and "goals_change" not in out["data"] and "目标变动" not in read_report(out["data"])
    close(capsys)

    def boom(a, b):
        raise ValueError("坏了")
    monkeypatch.setattr(goals, "diff", boom)
    code, out = run(capsys, "daily", "run")
    assert code == 0 and any("没能比较这次日常前后的目标" in a for a in out["data"]["alerts"])


def test_goals_change_lines_vanished_goal():
    d = {"goals_change": {"removed": [{"title": "丙", "fields": {}, "done_entry": None}], "added": [],
                          "changed": [{"title": "丁", "field": "deadline", "before": None, "after": "2026-10-30 04:00"}]}}
    assert reportmod.goals_change_lines(d) == ["移除：丙（已完成清单里没有它：不是通过移除目标挪走的）",
                                               "改了：丁 的期限：（空） → 2026-10-30 04:00"]


def test_goals_change_across_segments_with_real_snapshot(capsys, env):
    """真的 goals.snapshot / diff：agent 在两段之间把目标做完挪走，接着跑的那段结束时汇报里有（和这次日常开头比）。"""
    from maapilot.flows import goals
    paths.GOALS.write_text(GOAL, encoding="utf-8")
    code, out = run(capsys, "daily", "run")
    assert code == 0 and out["data"]["goals_pending"] is True
    goals.remove("遥 精2", goals.DONE, "精2 1级 做完了")
    code, out = run(capsys, "daily", "run", "--only", "farm,award")
    assert code == 0, out
    (rm,) = out["data"]["goals_change"]["removed"]
    assert rm["title"] == "遥 精2" and rm["done_entry"]["status"].startswith(goals.DONE)
    assert any(ln.startswith("目标移除：遥 精2（") and "精2 1级 做完了" in ln for ln in out["data"]["lines"])
    assert out["data"]["goals_pending"] is False and out["data"]["award_pending"] is False
    assert maa_tasks(env)[-2:] == ["fight 1-7", "award"]


def test_expire_goals_missing_is_harmless(monkeypatch):
    from maapilot.flows import goals
    monkeypatch.delattr(goals, "expire_due", raising=False)
    assert daily.expire_goals() == []


# --- crontab（tests/test_oscompat_sched.py 等用） ---------------------------------------------------------------------

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
