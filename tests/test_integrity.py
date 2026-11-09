"""Windows 的低完整性标签（DSH 沙箱给工作区加的）：doctor、启动检查、maa 失败时都要说出原因和修法。"""
from __future__ import annotations

import json

import pytest

from maapilot import cli, paths
from maapilot.crew import lock
from maapilot.oscompat import WINDOWS, integrity
from maapilot.tools import doctor
from tests.test_evidence import game, maa_dir  # noqa: F401  复用假的模拟器和 MAA 日志目录
from tests.test_tools import cfg  # noqa: F401

LOW = {"folder": "low", "process": "low", "fix": 'icacls "X" /setintegritylevel "(OI)(CI)M"'}


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_level_names_and_fix_command():
    assert integrity.level_name(0x1000) == "low" and integrity.level_name(0x2000) == "medium"
    assert integrity.level_name(0x1500) == "low" and integrity.level_name(0x3000) == "high"
    cmd = integrity.fix_command(r"C:\Users\x\MaaPilot")
    assert cmd == r'icacls "C:\Users\x\MaaPilot" /setintegritylevel "(OI)(CI)M"'   # 括号带引号，PowerShell 才不当表达式


@pytest.mark.skipif(WINDOWS, reason="别的系统上没有完整性级别")
def test_not_windows_is_none(tmp_path):
    assert integrity.process_level() is None and integrity.folder_label(tmp_path) is None
    assert integrity.low_integrity(tmp_path) is None


@pytest.mark.skipif(not WINDOWS, reason="只有 Windows 读得到")
def test_windows_reads_levels(tmp_path):
    assert integrity.process_level() in ("medium", "high", "low", "system")
    assert integrity.folder_label(tmp_path) is not None


def test_doctor_check_names_dsh(monkeypatch):
    monkeypatch.setattr(integrity, "low_integrity", lambda root: None)
    assert doctor.check_integrity(doctor.Ctx(True, doctor.PROBE))["ok"]
    monkeypatch.setattr(integrity, "low_integrity", lambda root: LOW)
    item = doctor.check_integrity(doctor.Ctx(True, doctor.PROBE))
    assert not item["ok"] and item.get("required", True)                  # MAA 全都跑不了：必需项
    assert "DSH" in item["detail"] and "沙箱" in item["detail"]
    assert LOW["fix"] in item["fix"] and item["command"] == LOW["fix"]


def test_brief_line(capsys, monkeypatch):
    monkeypatch.setattr(integrity, "low_integrity", lambda root: LOW)
    paths.SETUP.parent.mkdir(parents=True, exist_ok=True)
    paths.SETUP.write_text('{"done_at": "x", "version": 1}', encoding="utf-8")
    code, env = run(capsys, "doctor", "--brief")
    d = env["data"]
    assert code == 0 and d["low_integrity"] == LOW
    assert any("DSH" in l and LOW["fix"] in l for l in d["lines"])
    monkeypatch.setattr(integrity, "low_integrity", lambda root: None)
    code, env = run(capsys, "doctor", "--brief")
    assert env["data"]["low_integrity"] is None and not any("低完整性" in l for l in env["data"]["lines"])


def test_maa_failure_reports_low_integrity(cfg, game, maa_dir, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    monkeypatch.setattr(integrity, "low_integrity", lambda root: LOW)
    (cfg / "stdout.txt").write_text("", encoding="utf-8")
    (cfg / "log.txt").write_text("Updating hot update files...\n", encoding="utf-8")
    (cfg / "rc").write_text("1", encoding="utf-8")
    code, env = run(capsys, "maa", "copilot", str(cfg / "stdout.txt"))
    assert code == 5 and env["error"]["code"] == "low_integrity"           # 环境问题，重试没用
    assert "DSH" in env["error"]["message"] and LOW["fix"] in env["error"]["hint"]
    assert env["data"]["low_integrity"] == LOW and "evidence" in env["data"]
