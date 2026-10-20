"""对账的几处漏洞：几百几百的小花费累计、读不到仓库时悄悄算通过；report seen 已经去掉。

不碰模拟器：guard.read_now 换成假的数，daily.maapilot_call 换成假的返回。
"""
from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from maapilot import cli
from maapilot.cliutil import NEEDS_USER
from maapilot.flows import daily, report as reportmod
from maapilot.flows._common import now
from maapilot.flows.daily import tasks
from maapilot.flows.daily.base import _Stop
from maapilot.tools import common, guard


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# --- 1. 小花费累计 -----------------------------------------------------------------------------------------------------

def test_tolerated_decrease_keeps_baseline_so_repeated_small_spends_add_up():
    """仓库识别的约数（不是顶栏精确值）：每次少 600 都在读数误差里，但起点不往下挪，累计到 1200 就拦下。"""
    guard.take_snapshot({"originite": 10, "orundum": 5000})
    d = guard.check({"originite": 10, "orundum": 4400})
    assert d["unexplained"] == {}
    assert common.read_json(guard.snapshot_file())["orundum"] == 5000       # 没往下挪
    with pytest.raises(guard.CliError) as e:
        guard.check({"originite": 10, "orundum": 3800})
    assert e.value.exit == NEEDS_USER and e.value.data["unexplained"] == {"orundum": -1200}


def test_exact_topbar_readings_get_no_tolerance():
    """前后都是顶栏精确值：少 600 就是花了 600。"""
    guard.take_snapshot({"originite": 16, "orundum": 16566, "exact": True})
    with pytest.raises(guard.CliError) as e:
        guard.check({"originite": 16, "orundum": 15966, "exact": True})
    assert e.value.data["unexplained"] == {"orundum": -600}


def test_rounded_depot_reading_still_tolerated_against_exact_baseline():
    """10-05 误报：起点是顶栏精确值 16866，这次只有仓库约数 16000——不算花费，起点留在 16866。"""
    guard.take_snapshot({"originite": 16, "orundum": 16866, "exact": True})
    d = guard.check({"originite": 16, "orundum": 16000})
    assert d["unexplained"] == {}
    snap = common.read_json(guard.snapshot_file())
    assert snap["orundum"] == 16866 and snap["exact"] is False


def test_increases_and_allowed_spends_still_move_baseline():
    guard.take_snapshot({"originite": 10, "orundum": 5000})
    guard.check({"originite": 12, "orundum": 7000})
    assert common.read_json(guard.snapshot_file())["orundum"] == 7000
    guard.allow("合成玉", 1800, "用户同意抽 3 次")
    d = guard.check({"originite": 12, "orundum": 5200})
    assert d["unexplained"] == {} and d["used"][0]["amount"] == 1800
    assert common.read_json(guard.snapshot_file())["orundum"] == 5200


def test_read_now_marks_exact_only_when_topbar_agrees(monkeypatch):
    from maapilot.tools import state
    monkeypatch.setattr(state, "read_depot", lambda: {"items": {"4002": 16, "4003": 16000}})
    monkeypatch.setattr(state, "read_home", lambda: ({"originite": 16, "orundum": 16566}, None))
    assert guard.read_now() == {"originite": 16, "orundum": 16566, "exact": True}
    monkeypatch.setattr(state, "read_home", lambda: ({"originite": 61, "orundum": 16566}, None))
    assert guard.read_now() == {"originite": 16, "orundum": 16000, "exact": False}


# --- 2. 读不到仓库 → 算问题 --------------------------------------------------------------------------------------------

def _run():
    r = SimpleNamespace(data={"alerts": [], "guard": {"ok": None}}, events=[])
    r.event = lambda *a, **k: r.events.append((a, k))
    return r


def _fake_calls(monkeypatch, codes):
    """guard 命令依次返回 codes 里的退出码；别的命令（maa startup）返回 0。"""
    def call(args, env, timeout_s=None):
        if args[0] != "guard":
            return 0, {"ok": True, "data": {}, "error": None}
        c = codes.pop(0)
        return c, {"ok": c == 0, "data": {}, "error": None if c == 0 else {"code": "unreadable",
                                                                            "message": "仓库识别什么都没认出"}}
    monkeypatch.setattr(daily, "maapilot_call", call)


def test_unreadable_check_marks_problem(monkeypatch):
    r = _run()
    _fake_calls(monkeypatch, [0, 1])
    tasks._guard(r, "snapshot", {}, "上次对账")
    assert "guard_unreadable" not in r.data
    tasks._guard(r, "check", {}, "日常")
    assert r.data["guard_unreadable"] is True and r.data["guard"]["ok"] is None


def test_unreadable_snapshot_marks_problem_until_a_check_passes(monkeypatch):
    r = _run()
    _fake_calls(monkeypatch, [1, 1, 0])                  # 快照重试后仍读不到；后面的对账做成了
    tasks._guard(r, "snapshot", {}, "上次对账")
    assert r.data["guard_unreadable"] is True and any("没能记下" in a for a in r.data["alerts"])
    tasks._guard(r, "check", {}, "日常")
    assert "guard_unreadable" not in r.data and r.data["guard"] == {"ok": True}


def test_stop_still_raises(monkeypatch):
    r = _run()
    _fake_calls(monkeypatch, [3])
    with pytest.raises(_Stop):
        tasks._guard(r, "check", {}, "日常")
    assert r.data["status"] == "stopped" and "guard_unreadable" not in r.data


def _report(**kw):
    d = {"run_id": "20261015-0440", "trigger": "schedule", "started_at": (now() - timedelta(minutes=30)).isoformat(),
         "finished_at": now().isoformat(), "status": "done", "ok": True, "tasks": [], "failed": [], "alerts": [],
         "guard": {"ok": True}, **kw}
    reportmod.save(d)
    return d


def test_guard_unreadable_is_a_problem_and_has_a_line():
    d = _report(guard={"ok": None, "error": "仓库识别什么都没认出"}, guard_unreadable=True)
    assert reportmod.has_problem(d)
    assert "没能核对合成玉 / 源石（读不到仓库），这次的花费没有对账" in reportmod.summary_lines(d)
    ok = dict(d, guard={"ok": True}, guard_unreadable=False)
    assert not reportmod.has_problem(ok)


# --- 3. report seen 去掉了 -------------------------------------------------------------------------------------------

def test_report_seen_is_gone(capsys):
    _report()
    code, out = run(capsys, "report", "seen", "20261015-0440")           # 不认识的子命令
    assert code == 2 and out["error"]["code"] == "bad_arguments"
    code, out = run(capsys, "report", "show", "20261015-0440")          # 别的子命令照常
    assert code == 0 and "seen" not in out["data"]
