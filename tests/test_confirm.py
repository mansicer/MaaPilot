"""定时日常里不做的事（maapilot.tools.confirm）：只有记下花合成玉 / 源石的同意（guard allow）。

会话里 agent 在对话里问过用户就放行；定时日常（用户不在场）里一律拒绝。其余命令（schedule、reset……）不设关卡。
"""
from __future__ import annotations

import json

import pytest

from maapilot import cli, paths
from maapilot.flows import daily
from maapilot.tools import common, guard
from tests.test_daily import OTHER, fake_crontab


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def attended(monkeypatch):
    monkeypatch.delenv(daily.DAILY_ENV, raising=False)


def test_guard_allow_in_session(capsys, attended):
    c, out = run(capsys, "guard", "allow", "合成玉", "600", "--reason", "用户同意碎石换理智")
    assert c == 0 and guard.load_allowances()[0]["amount"] == 600


def test_unattended_never_allows(capsys, monkeypatch):
    monkeypatch.setenv(daily.DAILY_ENV, "20261015-0440")
    c, out = run(capsys, "guard", "allow", "源石", "1", "--reason", "x")
    assert c == 3 and out["error"]["code"] == "needs_user" and "定时日常" in out["error"]["message"]
    assert guard.load_allowances() == []


def test_empty_daily_env_still_counts_as_unattended(capsys, monkeypatch):
    monkeypatch.setenv(daily.DAILY_ENV, "")
    c, out = run(capsys, "guard", "allow", "源石", "1", "--reason", "x")
    assert c == 3 and guard.load_allowances() == []


def test_child_of_running_daily_is_unattended(capsys, monkeypatch, attended):
    """agent 把环境变量清掉也不行：是正在跑的日常进程的子孙就算定时日常里。"""
    from maapilot.oscompat import procs
    monkeypatch.setattr(daily, "running", lambda: {"run_id": "r1", "pid": 4242})
    monkeypatch.setattr(procs, "ancestors", lambda pid=None: [999, 4242])
    c, out = run(capsys, "guard", "allow", "源石", "1", "--reason", "x")
    assert c == 3 and "r1" in out["error"]["message"]
    monkeypatch.setattr(procs, "ancestors", lambda pid=None: [999])           # 用户会话里，日常恰好在跑
    c, out = run(capsys, "guard", "allow", "源石", "1", "--reason", "x")
    assert c == 0


def test_ancestors_of_this_process():
    import os
    from maapilot.oscompat import procs
    assert os.getppid() in procs.ancestors(os.getpid()) and procs.ancestors() == procs.ancestors(os.getpid())


@pytest.fixture
def cron(tmp_path, monkeypatch, attended):
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1', adb: adb}\ntools: {maa: maa, claude: claude}\n"
                            "schedule: ['04:40', '16:40']\n", encoding="utf-8")
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, OTHER)))
    return tmp_path / "crontab.txt"


def test_schedule_reports_programs(capsys, cron):
    c, out = run(capsys, "daily", "schedule", "--dry-run")
    assert c == 0 and set(out["data"]["exec"]) == {"maa", "claude", "adb"}
    assert cron.read_text(encoding="utf-8") == OTHER
    c, out = run(capsys, "daily", "schedule")
    assert c == 0 and "maapilot-v2" in cron.read_text(encoding="utf-8") and "exec" in out["data"]


def test_schedule_and_unschedule_have_no_gate(capsys, cron, monkeypatch):
    """改定时日常不设关卡（以用户在对话里的同意为准）。"""
    monkeypatch.setenv(daily.DAILY_ENV, "20261015-0440")
    c, out = run(capsys, "daily", "schedule", "--times", "05:00")
    assert c == 0
    c, out = run(capsys, "daily", "unschedule")
    assert c == 0 and "maapilot-v2" not in cron.read_text(encoding="utf-8")
