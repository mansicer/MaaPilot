"""`daily watch`（等下一次日常跑完）、`report seen`（告诉过用户）、doctor --brief 的 unseen_problems。

用假的汇报（reportmod.save 写进临时 private/）和假的运行标记；不碰模拟器、不起 claude、不读真的 crontab。
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from maapilot import cli
from maapilot.cliutil import CliError
from maapilot.flows import daily, report as reportmod, watch
from maapilot.flows._common import now


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def iso(t):
    return t.isoformat(timespec="seconds")


def rep(run_id, *, status="done", ok=True, started=None, finished=None, failed=(), **kw):
    started = started or now() - timedelta(minutes=30)
    d = {"run_id": run_id, "trigger": kw.pop("trigger", "schedule"), "started_at": iso(started),
         "finished_at": iso(finished) if finished else None, "status": status, "ok": ok,
         "tasks": kw.pop("tasks", []), "failed": list(failed), "alerts": kw.pop("alerts", []),
         "guard": kw.pop("guard", {"ok": True}), **kw}
    reportmod.save(d)
    return d


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(daily, "schedule_info", lambda: {"installed": True, "times": ["04:40", "16:40"],
                                                         "old_entries": []})
    monkeypatch.setattr(daily, "running", lambda: None)


def test_waits_for_a_run_that_finishes_later(monkeypatch):
    rep("20261004-0440", finished=now() - timedelta(hours=10))          # 之前跑完、告诉过用户的不算
    reportmod.mark_seen("20261004-0440")
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:                                           # 等了一会儿，下一次日常跑完了
            rep("20261004-1640", finished=now(), tasks=[{"task": "recruit", "ok": True}])
    d = watch.watch(5, poll_s=25, sleep=sleep)
    assert d["run_id"] == "20261004-1640" and d["status"] == "done" and d["seen"] is False
    assert d["lines"][0].endswith("都做成了。") and len(sleeps) == 3 and sleeps[0] == 25


def test_problem_run_exits_1_with_reasons(monkeypatch, capsys):
    """开始等时正在跑的那次：等它跑完；对账停下 + 有没做成的项 → 退出码 1，带原因。"""
    live = {"v": {"run_id": "20261004-1640", "pid": 1}}
    monkeypatch.setattr(daily, "running", lambda: live["v"])
    rep("20261004-1640", status="running", ok=None, finished=None)

    def sleep(s):
        live["v"] = None
        rep("20261004-1640", status="stopped", ok=False, finished=now(), failed=["infrast"],
            tasks=[{"task": "infrast", "ok": False, "error": "基建没做成（maa 退出码 1）", "code": "maa_failed"}],
            guard={"ok": False, "before": {"orundum": 16866}, "after": {"orundum": 16000},
                   "unexplained": {"orundum": -866}},
            stop_reason="日常之后发现合成玉 / 源石有没经同意的减少")
    with pytest.raises(CliError) as e:
        watch.watch(5, poll_s=1, sleep=sleep)
    err = e.value
    assert err.code == "daily_problem" and err.exit == 1
    d = err.data
    assert d["status"] == "stopped" and d["failed"][0]["label"] == "基建" and "maa 退出码 1" in d["failed"][0]["error"]
    assert d["guard_stop"]["unexplained"] == {"orundum": -866}
    text = " ".join(d["lines"])
    assert "因为货币对账停下了" in text and "合成玉 -866" in text and "没做成：基建" in text


def test_crashed_run_counts_as_aborted(monkeypatch):
    live = {"v": {"run_id": "20261004-1640", "pid": 1}}
    monkeypatch.setattr(daily, "running", lambda: live["v"])
    rep("20261004-1640", status="running", ok=None)

    def sleep(s):
        live["v"] = None                                               # 进程没了，汇报还写着 running
    with pytest.raises(CliError) as e:
        watch.watch(5, poll_s=1, sleep=sleep)
    assert e.value.data["status"] == "aborted" and "进程不在了" in e.value.data["alerts"][-1]


def test_timeout_and_cli(capsys):
    rep("20261004-0440", finished=now() - timedelta(hours=30))          # 太早的没告诉过也不补
    code, env = run(capsys, "daily", "watch", "--timeout-min", "0")
    assert code == 0 and env["ok"] and env["error"] is None                # 等满了不算出错
    assert env["data"]["kind"] == "timeout" and env["data"]["seen"] is True
    assert env["data"]["running"] is None and env["data"]["next_at"]


def test_seen_and_unseen_problems(capsys, monkeypatch):
    rep("20261003-0440", finished=now() - timedelta(days=2))                        # 顺利的不算
    rep("20261003-1640", status="stopped", ok=False, finished=now() - timedelta(days=1, hours=20),
        stop_reason="对账不过", guard={"ok": False, "unexplained": {"orundum": -600}})
    rep("20261004-1640", status="done", ok=False, failed=["mall"], finished=now() - timedelta(hours=2),
        tasks=[{"task": "mall", "ok": False, "error": "信用商店卡住了"}])
    rep("20250901-0440", status="aborted", ok=False, started=now() - timedelta(days=30),
        finished=now() - timedelta(days=30))                                       # 太早的不算
    rep("20261005-0440", status="running", ok=None, started=now())                   # 还在跑的不算
    ids = [p["run_id"] for p in reportmod.unseen_problems()]
    assert ids == ["20261004-1640", "20261003-1640"]

    from maapilot import paths
    from maapilot.tools import doctor
    paths.SETUP.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(doctor, "schedule_info", lambda probe: {"installed": True, "times": ["04:40"]})
    b = doctor.brief()
    assert [p["run_id"] for p in b["unseen_problems"]] == ids
    assert any("还没告诉用户的问题" in l and "信用商店卡住了" in l for l in b["lines"])

    code, env = run(capsys, "report", "seen", "20261004-1640")
    assert code == 0 and env["data"]["run_id"] == "20261004-1640"
    assert [p["run_id"] for p in reportmod.unseen_problems()] == ["20261003-1640"]
    code, env = run(capsys, "report", "seen", "20990101-0000")
    assert code == 2 and env["error"]["code"] == "unknown_run"


def test_watch_says_whether_seen():
    def sleep(s):
        rep("20261004-1640", status="done", ok=False, failed=["mall"], finished=now())
        reportmod.mark_seen("20261004-1640")
    with pytest.raises(CliError) as e:
        watch.watch(5, poll_s=1, sleep=sleep)
    assert e.value.data["seen"] is True
