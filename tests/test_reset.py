"""`maapilot reset`：private/ 整个挪成备份、只删本项目的定时行、--memory 挪自动记忆、--dry-run 不动、忙时拒绝。

全部在临时目录里：private/ 是 conftest 的临时目录，crontab 用假的（MAAPILOT_CRONTAB），Claude Code 的 projects 目录用
MAAPILOT_CLAUDE_PROJECTS_DIR 指到临时目录。不碰真的 crontab、真的 ~/.claude。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from maapilot import cli, paths
from maapilot.crew import lock
from maapilot.flows import daily, reset
from maapilot.tools import common
from tests.test_daily import OTHER, fake_crontab


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


MINE = f"40 4 * * * PATH=/x /r/.venv/bin/maapilot daily run --trigger schedule # maapilot-v2 {paths.ROOT}\n"
FOREIGN_V2 = "40 5 * * * /other/maapilot daily run --trigger schedule # maapilot-v2 /some/other/checkout\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """装好、设置过的样子：private/ 下有配置、目标、日志、对账、maa-cli 配置；crontab 里有本项目的行和别人的行；有自动记忆。"""
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    monkeypatch.delenv(daily.DAILY_ENV, raising=False)
    p = paths.PRIVATE
    for rel, text in {"config.yaml": "emulator: {address: 'emu:1'}\n", "setup.json": "{}", "goals.md": "# 目标\n",
                      "log/2026-10-02.jsonl": "{}\n", "guard/snapshot.json": "{}", "shots/a.png": "png",
                      "maa-config/cli.toml": "x = 1\n", "maa-config/profiles/default.toml": "y = 2\n",
                      "maa-config/tasks/maapilot-fight.toml": "[[tasks]]\n"}.items():
        f = p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    store = tmp_path / "crontab.txt"
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, OTHER + MINE + FOREIGN_V2)))
    projects = tmp_path / "claude-projects"
    monkeypatch.setenv(reset.PROJECTS_ENV, str(projects))
    mem = projects / reset.project_slug(paths.ROOT) / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- 用户说理智液可以用（2026-10-01）\n", encoding="utf-8")
    return {"crontab": store, "memory": mem}


def test_project_slug_matches_claude_code():
    assert reset.project_slug(Path("/Users/sicer/Developer/MaaPilot")) == "-Users-sicer-Developer-MaaPilot"
    assert reset.project_slug(Path("/private/tmp/claude-501/x_y.z")) == "-private-tmp-claude-501-x-y-z"


def test_dry_run_changes_nothing(capsys, world):
    before = sorted(str(f) for f in paths.PRIVATE.rglob("*"))
    cron_before = world["crontab"].read_text(encoding="utf-8")
    code, env = run(capsys, "reset", "--dry-run", "--memory")
    assert code == 0, env
    d = env["data"]
    assert d["dry_run"] is True and d["schedule"]["remove"] == [MINE.strip()]
    assert d["private"]["move"] == str(paths.PRIVATE) and ".bak-" in d["private"]["to"]
    assert {"config.yaml", "setup.json", "goals.md", "log", "guard", "shots", "maa-config"} <= set(d["private"]["entries"])
    assert d["memory"]["move"] == str(world["memory"]) and d["memory"]["entries"] == ["MEMORY.md"]
    assert sorted(str(f) for f in paths.PRIVATE.rglob("*")) == before
    assert world["crontab"].read_text(encoding="utf-8") == cron_before
    assert (world["memory"] / "MEMORY.md").exists()


def test_reset_moves_private_and_removes_only_our_cron_lines(capsys, world):
    code, env = run(capsys, "reset")
    assert code == 0, env
    d = env["data"]
    bak = Path(d["private"]["to"])
    assert bak.parent == paths.PRIVATE.parent and bak.name.startswith("private.bak-")
    assert (bak / "config.yaml").exists() and (bak / "goals.md").exists() and (bak / "guard" / "snapshot.json").exists()
    assert (bak / "log" / "2026-10-02.jsonl").exists() and (bak / "shots" / "a.png").exists()
    # 新的 private/ 里只有复制回来的 maa-cli 配置与任务模板
    assert sorted(p.name for p in paths.PRIVATE.iterdir()) == ["maa-config"]
    assert (paths.PRIVATE / "maa-config" / "tasks" / "maapilot-fight.toml").exists()
    assert (bak / "maa-config" / "cli.toml").exists()
    # crontab：只少了本项目那一行，别的（包括别的 checkout 的 v2 行）一个字节不动
    assert world["crontab"].read_text(encoding="utf-8") == OTHER + FOREIGN_V2
    assert d["schedule"]["removed"] == [MINE.strip()]
    # 自动记忆默认不动
    assert (world["memory"] / "MEMORY.md").exists() and d["memory"]["move"] is None
    assert d["next"] == "setup"
    # 下次会话进入设置
    assert not paths.SETUP.exists()                                      # doctor --brief 的 next 是 setup
    # 再来一次：备份名不重复
    code, env = run(capsys, "reset")
    assert code == 0 and Path(env["data"]["private"]["to"]) != bak and bak.exists()


def test_reset_memory_moves_memory_next_to_it(capsys, world):
    code, env = run(capsys, "reset", "--memory")
    assert code == 0, env
    mbak = Path(env["data"]["memory"]["to"])
    assert not world["memory"].exists() and mbak.parent == world["memory"].parent
    assert mbak.name.startswith("memory.bak-") and (mbak / "MEMORY.md").exists()


def test_reset_refuses_when_busy(capsys, world, monkeypatch):
    # 日常在跑
    common.write_json(paths.PRIVATE / "daily-run.json", {"run_id": "20261002-1640", "pid": os.getpid(),
                                                          "host": __import__("socket").gethostname(), "step": "infrast"})
    code, env = run(capsys, "reset")
    assert code == 4 and env["error"]["code"] == "daily_running"
    (paths.PRIVATE / "daily-run.json").unlink()
    # 模拟器锁被占着
    common.write_json(paths.LOCK_FILE, {"owner": "maa infrast", "pid": os.getpid(), "host": lock._host(),
                                        "job": "maa infrast", "token": "secret", "acquired_at": common.iso(),
                                        "heartbeat_at": common.iso(), "ttl_s": 900})
    code, env = run(capsys, "reset", "--dry-run")
    assert code == 4 and env["error"]["code"] == "busy" and "token" not in env["data"]["holder"]
    paths.LOCK_FILE.unlink()
    # 什么都没动
    assert (paths.PRIVATE / "config.yaml").exists() and MINE in world["crontab"].read_text(encoding="utf-8")


def test_reset_keeps_preferences_unless_memory(capsys, world):
    prefs = paths.PRIVATE / "memory"
    prefs.mkdir()
    (prefs / "MEMORY.md").write_text("- [理智液](s.md) — 快到期的可以用\n", encoding="utf-8")
    code, env = run(capsys, "reset")
    assert code == 0 and (paths.PRIVATE / "memory" / "MEMORY.md").exists()          # 用户偏好留着
    assert Path(env["data"]["private"]["to"], "memory", "MEMORY.md").exists()      # 备份里也有


def test_reset_memory_drops_preferences(capsys, world):
    (paths.PRIVATE / "memory").mkdir()
    code, env = run(capsys, "reset", "--memory")
    assert code == 0 and not (paths.PRIVATE / "memory").exists() and not world["memory"].exists()


def test_reset_without_scheduler(capsys, world, monkeypatch):
    """这台电脑上还设不了定时（Windows，任务计划程序接上之前）：当没有定时，照常重置。"""
    from maapilot.oscompat import sched
    monkeypatch.setattr(sched, "WINDOWS", True)
    code, env = run(capsys, "reset")
    assert code == 0, env
    s = env["data"]["schedule"]
    assert s["remove"] == [] and "removed" not in s and s["unavailable"] == sched.WINDOWS_TODO
    assert MINE in world["crontab"].read_text(encoding="utf-8")         # 没碰 crontab
    assert Path(env["data"]["private"]["to"], "config.yaml").exists()


def test_reset_private_in_use(capsys, world, monkeypatch):
    """private/ 里有文件被别的程序开着（Windows 上改不了名）：说清楚，什么都没动（定时也还在）。"""
    real = os.rename

    def busy(src, dst):
        if Path(src) == paths.PRIVATE:
            raise PermissionError(32, "另一个程序正在使用此文件")
        return real(src, dst)
    monkeypatch.setattr(reset.os, "rename", busy)
    code, env = run(capsys, "reset")
    assert code == 4 and env["error"]["code"] == "in_use" and env["error"]["hint"]
    assert (paths.PRIVATE / "config.yaml").exists() and MINE in world["crontab"].read_text(encoding="utf-8")


def test_reset_unschedule_failure_puts_private_back(capsys, world, monkeypatch):
    from maapilot.cliutil import ENVIRONMENT, CliError

    def fail(dry_run):
        if dry_run:
            return {"removed": [MINE.strip()], "old_entries": []}
        raise CliError("crontab_failed", "写不了 crontab", exit=ENVIRONMENT)
    monkeypatch.setattr(daily, "unschedule", fail)
    code, env = run(capsys, "reset")
    assert code == 5 and env["error"]["code"] == "crontab_failed"
    assert (paths.PRIVATE / "config.yaml").exists() and not list(paths.PRIVATE.parent.glob("private.bak-*"))
