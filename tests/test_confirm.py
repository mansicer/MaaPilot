"""日常期间不做的事（maapilot.tools.confirm）：只有记下花合成玉 / 源石的同意（guard allow）。

没有日常在跑、也没有没收尾的日常时放行（agent 在对话里问过用户）；日常在跑或没收尾（不分手动、定时）时一律拒绝。
不再看 MAAPILOT_DAILY_RUN、进程关系。其余命令（unschedule、reset……）不设关卡。
"""
from __future__ import annotations

import json

from maapilot import cli
from maapilot.flows import daily
from maapilot.flows._common import now
from maapilot.tools import guard


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _allow(capsys):
    return run(capsys, "guard", "allow", "合成玉", "600", "--reason", "用户同意碎石换理智")


def test_guard_allow_when_no_daily(capsys):
    c, out = _allow(capsys)
    assert c == 0 and guard.load_allowances()[0]["amount"] == 600


def test_refused_while_daily_running(capsys, monkeypatch):
    monkeypatch.setattr(daily, "running", lambda: {"run_id": "20261015-0440", "pid": 4242})
    c, out = _allow(capsys)
    assert c == 3 and out["error"]["code"] == "needs_user" and "20261015-0440" in out["error"]["message"]
    assert "daily done" in out["error"]["hint"] and guard.load_allowances() == []


def test_refused_while_daily_open_even_manual(capsys):
    """手动跑的日常没收尾也拦：日常里不临时决定花真实货币。收尾（daily done）后才放行。"""
    daily._write_open({"run_id": "20261015-1640", "trigger": "manual", "started_at": now().isoformat(timespec="seconds")})
    c, out = _allow(capsys)
    assert c == 3 and out["error"]["code"] == "needs_user" and guard.load_allowances() == []
    daily._clear_open()
    c, out = _allow(capsys)
    assert c == 0


def test_stale_open_daily_still_refuses(capsys):
    """超过 6 小时没收尾的也算（下次 daily run 才自动收尾；agent 可以先 daily done）。"""
    from datetime import timedelta
    daily._write_open({"run_id": "20261015-0440", "trigger": "schedule",
                       "started_at": (now() - timedelta(hours=9)).isoformat(timespec="seconds")})
    assert daily.open_daily()["stale"] is True
    c, out = _allow(capsys)
    assert c == 3


def test_old_daily_env_no_longer_matters(capsys, monkeypatch):
    monkeypatch.setenv("MAAPILOT_DAILY_RUN", "20261015-0440")
    c, out = _allow(capsys)
    assert c == 0
