"""maa-cli 的数据放在项目里：第一次用时搬过来，搬不成接着用原来的。"""
import shutil
import subprocess

import pytest

from maapilot.crew import maa


class Run:
    def __init__(self, code=0, out=""):
        self.returncode, self.stdout, self.stderr = code, out, ""


@pytest.fixture
def old(tmp_path, monkeypatch):
    """系统默认目录里已经装好的数据。"""
    monkeypatch.delenv("MAA_DATA_DIR")
    d = tmp_path / "old"
    (d / "lib").mkdir(parents=True)
    (d / "lib" / "MaaCore.dll").write_text("x", encoding="utf-8")
    monkeypatch.setattr(maa.procs, "run_text", lambda argv, **kw: Run(out=f"{d}\n"))
    return d


def test_moves_old_data_in_once(old, monkeypatch):
    env = maa.env()
    new = maa.data_dir()
    assert env["MAA_DATA_DIR"] == str(new) and (new / "lib" / "MaaCore.dll").exists()
    assert (old / "lib" / "MaaCore.dll").exists()                # 原来的不删
    monkeypatch.setattr(maa.procs, "run_text", lambda *a, **kw: pytest.fail("搬过了不再问"))
    assert maa.env()["MAA_DATA_DIR"] == str(new)


def test_empty_folder_when_nothing_installed(old):
    shutil.rmtree(old / "lib")
    assert maa.env()["MAA_DATA_DIR"] and maa.data_dir().is_dir()


@pytest.mark.parametrize("run", [lambda *a, **kw: Run(1), subprocess.TimeoutExpired("maa", 20)])
def test_keeps_old_folder_when_maa_cannot_say(old, monkeypatch, run):
    def fails(*a, **kw):
        raise run
    monkeypatch.setattr(maa.procs, "run_text", run if callable(run) else fails)
    assert "MAA_DATA_DIR" not in maa.env() and not maa.data_dir().exists()


def test_failed_copy_leaves_nothing_half_done(old, monkeypatch):
    def full(*a, **kw):
        raise OSError("磁盘满了")
    monkeypatch.setattr(maa.shutil, "copytree", full)
    assert "MAA_DATA_DIR" not in maa.env() and not maa.data_dir().exists()
    assert not list(maa.data_dir().parent.glob("maa-data*")) if maa.data_dir().parent.exists() else True


def test_own_setting_wins(old, monkeypatch):
    monkeypatch.setenv("MAA_DATA_DIR", "/my/maa")
    assert maa.env()["MAA_DATA_DIR"] == "/my/maa" and not maa.data_dir().exists()
