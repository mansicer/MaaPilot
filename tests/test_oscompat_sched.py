"""定时后端（oscompat.sched）、各系统的提示（oscompat.hints）、doctor 的 .claude/skills 链接项。
crontab 用假脚本（MAAPILOT_CRONTAB）；Windows 用改 WINDOWS 标志模拟（任务计划程序还没接上：设不了定时，别的命令照常）。"""
import json
import os
from pathlib import Path

import pytest

from maapilot import cli, paths
from maapilot.flows import daily
from maapilot.flows.daily import cron as dcron
from maapilot.oscompat import hints, sched, venv
from maapilot.tools import doctor
from tests.portable import WINDOWS, needs_cron
from tests.test_daily import _no_real_crontab, fake_crontab, OTHER  # noqa: F401 - fixture


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def cfg():
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\nschedule: ['04:40', '16:40']\n", encoding="utf-8")


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sched, "WINDOWS", True)


# --- cron 后端 -------------------------------------------------------------------------------------------------------

@needs_cron
def test_cron_backend_round_trip(tmp_path, monkeypatch, cfg):
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, OTHER)))
    b = sched.backend()
    assert isinstance(b, sched.CronBackend) and b.available() == (True, None)
    cmd = dcron.command({"emulator": {"address": "emu:1"}})
    r = b.install([(4, 40), (16, 40)], cmd)
    assert r["changed"] and len(r["installed"]) == 2 and r["old_entries"] == [OTHER.splitlines()[1]]
    entries = b.read()
    mine = [e for e in entries if e.mine]
    assert [e.times for e in mine] == [["04:40"], ["16:40"]] and [e.ident for e in entries if e.old] == r["old_entries"]
    line = mine[0].ident
    assert line.startswith("40 4 * * * PATH=") and " PYTHONUTF8=1 " in line and line.endswith(f"# maapilot-v2 {paths.ROOT}")
    assert f"{venv.venv_exe('maapilot')} daily run --trigger schedule >> " in line
    assert b.install([(4, 40), (16, 40)], cmd)["changed"] is False        # 重复执行结果相同
    assert b.remove()["removed"] == [e.ident for e in mine]
    assert (tmp_path / "crontab.txt").read_text(encoding="utf-8") == OTHER


def test_cron_path_uses_venv_and_system_dirs(cfg):
    line = daily.cron_lines([(4, 40)], {"emulator": {"address": "emu:1"}})[0]
    path = line.split("PATH=", 1)[1].split(" ", 1)[0].strip("'")
    dirs = path.split(os.pathsep)
    assert dirs[0] == str(venv.venv_bin()) and dirs[-len(venv.sys_path_dirs()):] == venv.sys_path_dirs()
    assert len(dirs) == len(set(dirs))


def test_cron_times_and_old_lines_still_recognized():
    # 旧版写出的行（.venv/bin 写死）、一行里多个小时
    line = f"40 4,16 * * * PATH=/x {paths.ROOT}/.venv/bin/maapilot daily run --trigger schedule # maapilot-v2 {paths.ROOT}"
    b = sched.CronBackend()
    assert b.mine(line) and b.times_of([line]) == ["04:40", "16:40"]
    assert not b.mine(line.replace(f"# maapilot-v2 {paths.ROOT}", "# maapilot-v2 /elsewhere"))


@needs_cron
def test_doctor_uses_probe_for_crontab():
    from tests.test_tools import FakeProbe
    line = f"40 4 * * * x daily run --trigger schedule # maapilot-v2 {paths.ROOT}\n*/15 * * * * maapilot job tick\n"
    probe = FakeProbe({"crontab -l": (0, line.encode(), b"")})
    assert doctor.schedule_info(probe) == {"installed": True, "times": ["04:40"],
                                           "old_entries": ["*/15 * * * * maapilot job tick"]}
    probe = FakeProbe({"crontab -l": (1, b"", b"crontab: no crontab for x")})
    assert doctor.schedule_info(probe)["installed"] is False
    probe = FakeProbe({"crontab -l": (1, b"", b"permission denied")})
    info = doctor.schedule_info(probe)
    assert info["installed"] is None and "permission denied" in info["error"]
    item = doctor.check_schedule(doctor.Ctx(True, probe))
    assert not item["ok"] and item["required"] is False


# --- 假后端 ---------------------------------------------------------------------------------------------------------

def test_fake_backend(capsys, tmp_path, monkeypatch, cfg):
    store = tmp_path / "sched.json"
    store.write_text(json.dumps([{"times": ["03:00"], "command": "x", "mine": False, "ident": "other", "old": False}]),
                     encoding="utf-8")
    monkeypatch.setenv(sched.SCHEDULER_ENV, f"fake:{store}")
    code, out = run(capsys, "daily", "schedule")
    assert code == 0 and out["data"]["changed"] and out["data"]["times"] == ["04:40", "16:40"]
    code, out = run(capsys, "daily", "status")
    assert out["data"]["schedule"] == {"installed": True, "times": ["04:40", "16:40"], "old_entries": []}
    code, out = run(capsys, "daily", "unschedule")
    assert code == 0 and out["data"]["removed"] == [f"maapilot-daily {paths.ROOT}"]
    assert [e["ident"] for e in json.loads(store.read_text(encoding="utf-8"))] == ["other"]


# --- Windows：还设不了定时 ---------------------------------------------------------------------------------------------

def test_windows_schedule_refused_clearly(capsys, cfg, windows):
    assert sched.backend().available() == (False, sched.WINDOWS_TODO)
    for argv in (("daily", "schedule"), ("daily", "unschedule"), ("daily", "schedule", "--dry-run")):
        code, out = run(capsys, *argv)
        assert code == 5 and out["error"]["code"] == "no_scheduler" and "任务计划程序" in out["error"]["message"]


def test_windows_status_doctor_brief_still_work(capsys, cfg, windows):
    code, out = run(capsys, "daily", "status")
    assert code == 0 and out["data"]["schedule"]["installed"] is False and out["data"]["next_at"] is None
    assert out["data"]["schedule"]["unavailable"] == sched.WINDOWS_TODO
    paths.SETUP.write_text("{}", encoding="utf-8")
    code, out = run(capsys, "doctor", "--brief")
    assert code == 0 and out["data"]["schedule"] == {"installed": False, "times": [], "unavailable": sched.WINDOWS_TODO}
    assert any(l.startswith("定时日常这台电脑上还设不了") for l in out["data"]["lines"])
    item = doctor.check_schedule(doctor.Ctx(True, doctor.PROBE))
    assert not item["ok"] and item["required"] is False and item["unavailable"] is True


# --- 提示 -----------------------------------------------------------------------------------------------------------

def test_hints_per_os(monkeypatch):
    monkeypatch.setattr(hints, "WINDOWS", False)
    monkeypatch.setattr(hints, "MACOS", True)
    assert hints.install_hint("maa").startswith("brew ") and 16384 in hints.common_ports()
    monkeypatch.setattr(hints, "WINDOWS", True)
    monkeypatch.setattr(hints, "MACOS", False)
    assert "winget" in hints.install_hint("maa") and "brew" not in hints.install_hint("adb")
    assert {16384, 5555, 62001} <= set(hints.common_ports()) and "VCRedist" in hints.install_hint("vcredist")
    monkeypatch.setattr(hints, "WINDOWS", False)
    assert "brew" not in hints.install_hint("adb") and hints.install_hint("codex").startswith("npm ")


# --- .claude/skills 链接 ----------------------------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".agents" / "skills" / "maapilot-daily").mkdir(parents=True)
    (root / ".agents" / "skills" / "maapilot-daily" / "SKILL.md").write_text("x", encoding="utf-8")
    (root / ".claude").mkdir()
    return root


def test_skills_link_plain_file_fixed(repo, monkeypatch):
    link = repo / ".claude" / "skills"
    link.write_text("../.agents/skills", encoding="utf-8")         # Windows 上 core.symlinks=false 检出的样子
    assert doctor.skills_link(repo) == "file"
    monkeypatch.setattr(paths, "ROOT", repo)
    item = doctor.check_skills_link(None)
    assert not item["ok"] and item["state"] == "file" and item["fix"] == "maapilot doctor --fix" and item["required"] is False
    assert doctor.fix_skills_link(repo) == ("junction" if WINDOWS else "symlink")
    assert doctor.skills_link(repo) == "ok" and (link / "maapilot-daily" / "SKILL.md").exists()
    if not WINDOWS:                                                       # 目录联接只能写绝对路径
        assert os.readlink(link) == os.path.join("..", ".agents", "skills")    # 相对链接，仓库挪了也还能用
    assert doctor.check_skills_link(None)["ok"]


def test_skills_link_missing_and_broken(repo):
    assert doctor.skills_link(repo) == "missing"
    (repo / ".claude" / "skills").symlink_to(repo / "nowhere")
    assert doctor.skills_link(repo) == "broken"
    doctor.fix_skills_link(repo)
    assert doctor.skills_link(repo) == "ok"



@pytest.mark.skipif(not WINDOWS, reason="目录联接只有 Windows 有")
def test_skills_link_broken_junction_fixed(repo, tmp_path):
    """联接指向的目录没了（仓库挪过）：算 broken，--fix 先删掉旧联接再建。"""
    import _winapi
    gone = tmp_path / "gone"
    gone.mkdir()
    _winapi.CreateJunction(str(gone), str(repo / ".claude" / "skills"))
    gone.rmdir()
    assert doctor.skills_link(repo) == "broken"
    assert doctor.fix_skills_link(repo) == "junction" and doctor.skills_link(repo) == "ok"


def test_real_repo_skills_link_ok():
    assert doctor.skills_link() == "ok"


# --- Claude Code 的信任：按路径比较 ---------------------------------------------------------------------------------

def test_claude_trusted_compares_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    root = tmp_path / "MaaPilot"
    root.mkdir()
    f = tmp_path / ".claude.json"
    f.write_text(json.dumps({"projects": {str(root.resolve()) + "/": {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    assert doctor.claude_trusted(root) is True
    f.write_text(json.dumps({"projects": {str(tmp_path.resolve()): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    assert doctor.claude_trusted(root) is True                                  # 上级目录信任也算
    f.write_text(json.dumps({"projects": {str(root.resolve()): {"hasTrustDialogAccepted": False}}}), encoding="utf-8")
    assert doctor.claude_trusted(root) is False
    assert doctor.claude_trusted(Path(tmp_path / "missing")) is False
