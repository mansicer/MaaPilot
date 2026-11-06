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
    monkeypatch.setattr(maa, "_copy_failed", {})
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


def test_failed_copy_is_not_repeated_and_doctor_says_so(old, monkeypatch):
    calls = []

    def full(*a, **kw):
        calls.append(1)
        raise OSError("磁盘满了")
    monkeypatch.setattr(maa.shutil, "copytree", full)
    assert "MAA_DATA_DIR" not in maa.env() and "MAA_DATA_DIR" not in maa.env()
    assert calls == [1] and "磁盘满了" in maa.data_problem()
    assert not maa.data_dir().exists()


def test_old_data_seen_later_still_moves_in(old):
    shutil.move(old / "lib", old / "aside")
    assert maa.env()["MAA_DATA_DIR"] and maa.data_dir().is_dir()     # 空目录
    shutil.move(old / "aside", old / "lib")
    maa.env()
    assert (maa.data_dir() / "lib" / "MaaCore.dll").exists()


def test_update_deleting_lib_does_not_trigger_another_move(old, monkeypatch):
    new = maa.env()["MAA_DATA_DIR"]
    shutil.rmtree(maa.data_dir() / "lib")                            # maa update 先删后装
    monkeypatch.setattr(maa.procs, "run_text", lambda *a, **kw: pytest.fail("搬过了不再问"))
    assert maa.env()["MAA_DATA_DIR"] == new and not (maa.data_dir() / "lib").exists()


def test_loser_of_a_race_uses_the_new_folder(old, monkeypatch):
    def lost(*a, **kw):
        (maa.data_dir() / "lib").mkdir(parents=True)
        raise OSError("别的进程先搬好了")
    monkeypatch.setattr(maa, "_copy_data_in", lost)
    assert maa.env()["MAA_DATA_DIR"] == str(maa.data_dir()) and maa.data_problem() is None


def test_dead_temp_folders_are_swept(old, monkeypatch):
    new = maa.data_dir()
    new.parent.mkdir(parents=True, exist_ok=True)
    dead, alive = new.with_name(f"{new.name}.tmp999999"), new.with_name(f"{new.name}.tmp1")
    dead.mkdir()
    alive.mkdir()
    monkeypatch.setattr(maa.procs, "pid_alive", lambda pid: pid == 1)
    maa.env()
    assert not dead.exists() and alive.exists()


def test_own_setting_wins(old, monkeypatch):
    monkeypatch.setenv("MAA_DATA_DIR", "/my/maa")
    assert maa.env()["MAA_DATA_DIR"] == "/my/maa" and not maa.data_dir().exists()


def test_doctor_looks_in_the_project_folder(old, monkeypatch):
    from maapilot.tools import doctor

    class Probe:
        def __init__(self):
            self.envs = []

        def run(self, argv, timeout=15, env=None):
            self.envs.append(env)
            return doctor.Proc(0, b"/x\n", b"")
    probe = Probe()
    ctx = doctor.Ctx(True, probe)
    doctor.maa_version(ctx)
    doctor.maa_dir(ctx, "resource")
    assert [e["MAA_DATA_DIR"] for e in probe.envs] == [str(maa.data_dir())] * 2
