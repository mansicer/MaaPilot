"""要用户同意的命令（maapilot.tools.confirm）：guard allow、daily schedule / unschedule、reset。

用户可能远程连着会话，不弹本机的框：会话里 agent 在对话里问过就放行；定时日常（用户不在场）里一律拒绝。
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


@pytest.fixture
def cron(tmp_path, monkeypatch, attended):
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1', adb: adb}\ntools: {maa: maa, claude: claude}\n"
                            "schedule: ['04:40', '16:40']\n", encoding="utf-8")
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, OTHER)))
    return tmp_path / "crontab.txt"


def test_schedule_reports_programs_and_records(capsys, cron):
    c, out = run(capsys, "daily", "schedule", "--dry-run")
    assert c == 0 and set(out["data"]["exec"]) == {"maa", "claude", "adb"}
    assert cron.read_text(encoding="utf-8") == OTHER
    c, out = run(capsys, "daily", "schedule")
    assert c == 0 and "maapilot-v2" in cron.read_text(encoding="utf-8") and "exec" in out["data"]
    assert common.read_json(paths.GUARD / "schedule.json")["exec"] == {"maa": "maa", "claude": "claude", "adb": "adb"}


def test_unattended_schedule_change_refused(capsys, cron, monkeypatch):
    run(capsys, "daily", "schedule")
    monkeypatch.setenv(daily.DAILY_ENV, "20261015-0440")
    c, out = run(capsys, "daily", "schedule")                                  # 没变：照常
    assert c == 0
    c, out = run(capsys, "daily", "schedule", "--times", "05:00")
    assert c == 3 and out["error"]["code"] == "needs_user"
    c, out = run(capsys, "daily", "unschedule")
    assert c == 3 and "maapilot-v2" in cron.read_text(encoding="utf-8")


def test_changed_programs_block_scheduled_daily(capsys, cron):
    run(capsys, "daily", "schedule")
    paths.CONFIG.write_text(paths.CONFIG.read_text(encoding="utf-8").replace("claude: claude", "claude: /tmp/evil"),
                            encoding="utf-8")
    c, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert c == 3 and out["error"]["code"] == "config_unconfirmed"
    assert out["data"]["changed"] == {"claude": {"confirmed": "claude", "now": "/tmp/evil"}}
    from maapilot.tools import doctor
    assert doctor.schedule_unconfirmed_brief() == out["data"]["changed"]
    lines = doctor.brief_lines({"setup_done": True, "guard_stop": None, "goals": [], "goals_active": 0,
                                "last_daily": None, "schedule": {"installed": True, "times": []}, "news": {},
                                "sources": [], "unseen_problems": [], "schedule_unconfirmed": out["data"]["changed"]})
    assert any("还没确认" in l and "claude" in l for l in lines)
    c, out = run(capsys, "daily", "schedule")                                  # 会话里用户同意后重跑：记下新的
    assert c == 0 and daily.unconfirmed(__import__("maapilot.config", fromlist=["load"]).load()) is None


def test_first_scheduled_run_trusts_existing_setup(cron):
    from maapilot import config
    cfg = config.load()
    daily.check_confirmed(cfg)                                                 # 旧版装的：没记过 → 记下现在的
    assert daily.unconfirmed(cfg) is None and (paths.GUARD / "schedule.json").exists()


def test_reset_refused_when_unattended(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, OTHER)))
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\n", encoding="utf-8")
    monkeypatch.setenv(daily.DAILY_ENV, "20261015-0440")
    c, out = run(capsys, "reset")
    assert c in (2, 3) and paths.CONFIG.exists()
