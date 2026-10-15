"""验收与安全审查之后的修正：对账的 dirty、doctor 钩子、装着的客户端、锁的继承与换 token、
calc 的练度前提、模组任务、state op 的注册、启动脚本没有 Python 时的钩子输出。不碰真机。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from maapilot import cli, cliutil, paths
from maapilot.crew import lock
from maapilot.tools import common

HAS_DATA = (paths.DATA / "character_table.json").exists() and (paths.DATA / "uniequip_table.json").exists()
needs_data = pytest.mark.skipif(not HAS_DATA, reason="没有 data/ 游戏数据表")


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def _no_external_maa(monkeypatch):
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])


@pytest.fixture
def cfg(_tmp_private):
    _tmp_private.mkdir(parents=True, exist_ok=True)
    (_tmp_private / "config.yaml").write_text("emulator:\n  address: 'emu-test:1'\n", encoding="utf-8")
    return _tmp_private


class Probe:
    def __init__(self, table):
        self.table, self.calls = table, []

    def run(self, argv, timeout=15, env=None):
        from maapilot.tools.doctor import Proc
        self.calls.append(argv)
        for key, res in self.table.items():
            if key in " ".join(argv):
                return Proc(*res)
        return Proc(1, b"", b"not faked")

    def which(self, name):
        return f"/usr/bin/{name}"


# --- 对账：dirty ----------------------------------------------------------------------------------

def test_mark_dirty_only_for_spending_commands(cfg):
    from maapilot.tools import guard
    for owner in ("screen shot", "state depot", "maa startup", "maa closedown", "guard check", "daily run"):
        guard.mark_dirty(owner)
    assert guard.dirty() is None
    for owner in ("input tap", "maa fight", "op upgrade", "craft"):
        guard.mark_dirty(owner)
    assert guard.dirty()["commands"] == ["input tap", "maa fight", "op upgrade", "craft"]


def test_device_lock_marks_dirty(cfg, monkeypatch):
    from maapilot.crew import device as dev
    from maapilot.tools import guard
    monkeypatch.setattr(dev, "configure", lambda conn: None)
    with cliutil.device("state depot"):
        pass
    assert guard.dirty() is None
    with cliutil.device("input tap"):
        pass
    assert guard.dirty()["commands"] == ["input tap"]


def test_snapshot_cannot_hide_a_spend(cfg, capsys, monkeypatch):
    """snapshot → 点了会花钱的东西 → 再 snapshot：不能重记起点，要对账并停下。"""
    from maapilot.tools import guard
    now = {"v": {"originite": 12, "orundum": 3200}}
    monkeypatch.setattr(guard, "read_now", lambda: dict(now["v"]))
    code, env = run(capsys, "guard", "snapshot")
    assert code == 0 and env["data"]["orundum"] == 3200 and guard.dirty() is None
    guard.mark_dirty("input tap")                               # agent 点了一下
    now["v"] = {"originite": 12, "orundum": 1400}
    code, env = run(capsys, "guard", "snapshot")
    assert code == 3 and env["error"]["code"] == "currency_decreased"
    assert guard.stopped() is not None and json.loads(guard.snapshot_file().read_text(encoding="utf-8"))["orundum"] == 3200
    code, env = run(capsys, "guard", "snapshot")                # 停机中也不重记
    assert code == 3 and env["error"]["code"] == "guard_stop"
    code, env = run(capsys, "guard", "check")
    assert code == 3                                            # 还是对不上


def test_snapshot_rebases_when_nobody_touched_it(cfg, capsys, monkeypatch):
    """两次日常之间没有 agent 操作：用户自己在游戏里花的不算异常，snapshot 重记起点。"""
    from maapilot.tools import guard
    now = {"v": {"originite": 12, "orundum": 3200}}
    monkeypatch.setattr(guard, "read_now", lambda: dict(now["v"]))
    run(capsys, "guard", "snapshot")
    now["v"] = {"originite": 12, "orundum": 2600}
    code, env = run(capsys, "guard", "snapshot")
    assert code == 0 and env["data"]["orundum"] == 2600 and "checked_instead" not in env["data"]


def test_snapshot_with_dirty_and_no_change_checks_and_clears(cfg, capsys, monkeypatch):
    from maapilot.tools import guard
    monkeypatch.setattr(guard, "read_now", lambda: {"originite": 12, "orundum": 3200})
    run(capsys, "guard", "snapshot")
    guard.mark_dirty("maa fight")
    code, env = run(capsys, "guard", "snapshot")
    assert code == 0 and env["data"]["checked_instead"] is True and guard.dirty() is None


def test_unreadable_check_keeps_dirty(cfg, capsys, monkeypatch):
    from maapilot.cliutil import CliError
    from maapilot.tools import guard
    monkeypatch.setattr(guard, "read_now", lambda: {"originite": 12, "orundum": 3200})
    run(capsys, "guard", "snapshot")
    guard.mark_dirty("input tap")

    def unreadable():
        raise CliError("unreadable", "读不到")
    monkeypatch.setattr(guard, "read_now", unreadable)
    code, env = run(capsys, "guard", "check")
    assert code == 1 and guard.dirty() is not None


# --- 锁 --------------------------------------------------------------------------------------------

def test_rotate_token_locks_out_old_children(cfg, monkeypatch):
    lk = lock.acquire("daily run")
    try:
        old = lk.data["token"]
        monkeypatch.setenv(lock.TOKEN_ENV, old)
        child = lock.acquire("maa fight")                        # 继承
        assert child.nested
        new = lk.rotate_token()
        assert new != old and lock.current()["token"] == new
        child.heartbeat()                                        # 旧的嵌套锁不会把旧 token 写回去
        assert lock.current()["token"] == new
        with monkeypatch.context() as m:
            m.setattr(lock.os, "getpid", lambda: 4242)           # 旧 token 的子进程（别的进程）
            m.setattr(lock, "pid_alive", lambda pid: True)
            with pytest.raises(lock.LockBusy):
                lock.acquire("maa fight")
            m.setenv(lock.TOKEN_ENV, new)
            assert lock.acquire("maa fight").nested
    finally:
        lk.release()
    assert lock.current() is None


def test_lock_status_inherited(cfg, capsys, monkeypatch):
    code, env = run(capsys, "lock", "status")
    assert env["data"]["inherited"] is False
    lk = lock.acquire("daily run")
    try:
        monkeypatch.setenv(lock.TOKEN_ENV, lk.data["token"])
        code, env = run(capsys, "lock", "status")
        assert env["data"]["inherited"] is True
        monkeypatch.setenv(lock.TOKEN_ENV, "wrong")
        code, env = run(capsys, "lock", "status")
        assert env["data"]["inherited"] is False
    finally:
        lk.release()


# --- doctor ----------------------------------------------------------------------------------------

def test_hook_system_message_only_when_needed(capsys, monkeypatch):
    from maapilot.tools import doctor
    assert "systemMessage" in doctor.hook_output({"next": "setup"})
    assert "systemMessage" in doctor.hook_output({"next": "guard"})
    assert "systemMessage" not in doctor.hook_output({"next": None})


def test_game_check_lists_installed_clients(cfg):
    from maapilot.tools import doctor
    ctx = doctor.Ctx(False, Probe({
        "pm list packages": (0, b"package:com.android.chrome\npackage:com.hypergryph.arknights.bilibili\n", b""),
        "dumpsys window": (0, b"  mCurrentFocus=Window{1 u0 com.android.launcher3/x}\n", b"")}))
    ctx.cfg, ctx.adb, ctx.online = {"emulator": {"address": "emu:1"}, "server": "Official"}, "/usr/bin/adb", True
    item = doctor.check_game(ctx)
    assert item["installed"] == [{"package": "com.hypergryph.arknights.bilibili", "server": "Bilibili"}]
    assert not item["ok"] and "Bilibili" in item["detail"]
    ctx.probe = Probe({"pm list packages": (0, b"package:com.android.chrome\n", b""),
                       "dumpsys window": (0, b"", b"")})
    item = doctor.check_game(ctx)
    assert item["installed"] == [] and "没装明日方舟" in item["detail"]


# 启动钩子是 sh 脚本。macOS / Linux 用 /bin/sh 跑；Windows 上 Claude Code 用 Git Bash 跑，命令是
# `"$CLAUDE_PROJECT_DIR"/bin/session-start`（Windows 路径接 /bin/…），这里照样用 Git Bash 这么跑
def _git_bash() -> str | None:
    git = shutil.which("git")
    if not git:
        return None
    for up in (Path(git).resolve().parent.parent, Path(git).resolve().parent.parent.parent):    # Git\cmd、Git\mingw64\bin
        if (up / "bin" / "bash.exe").exists():
            return str(up / "bin" / "bash.exe")
    return None


def _run_hook(root: Path, envfile: Path) -> subprocess.CompletedProcess:
    if sys.platform == "win32":
        bash = _git_bash()
        if bash is None:
            pytest.skip("没有 Git Bash")
        argv, env = [bash, f"{root}/bin/session-start"], {**os.environ, "CLAUDE_ENV_FILE": str(envfile)}
    else:
        argv, env = ["/bin/sh", str(root / "bin" / "session-start")], {"PATH": "/usr/bin:/bin",
                                                                        "CLAUDE_ENV_FILE": str(envfile)}
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env=env)


sh_hook = pytest.mark.skipif(sys.platform == "win32", reason="假的 maapilot.exe 是 sh 脚本，Windows 上执行不了")


# --- 启动脚本 --------------------------------------------------------------------------------------

def test_session_start_without_venv(tmp_path):
    """还没建 .venv：启动钩子照样把 .venv/bin（Windows 上 .venv/Scripts）写进 CLAUDE_ENV_FILE，并按钩子的格式说 no_env。"""
    root = tmp_path / "repo"
    (root / "bin").mkdir(parents=True)
    shutil.copy(paths.ROOT / "bin" / "session-start", root / "bin" / "session-start")
    envfile = tmp_path / "env.sh"
    p = _run_hook(root, envfile)
    out = json.loads(p.stdout)
    assert p.returncode == 0 and out["systemMessage"] and "no_env" in out["hookSpecificOutput"]["additionalContext"]
    text = envfile.read_text(encoding="utf-8")
    if sys.platform == "win32":                       # Git Bash 里 pwd 是 /c/Users/… 的写法
        assert text.startswith('export PATH="/') and "/repo/.venv/Scripts:$PATH" in text, text
    else:
        assert f'{root.resolve()}/.venv/bin' in text or f'{root}/.venv/bin' in text


@sh_hook
def test_session_start_windows_venv_layout(tmp_path):
    """Windows 的 .venv（Scripts/maapilot.exe，Claude Code 经 Git Bash 跑钩子）：PATH 写 Scripts，并执行它做启动检查。"""
    root = tmp_path / "repo"
    (root / "bin").mkdir(parents=True)
    shutil.copy(paths.ROOT / "bin" / "session-start", root / "bin" / "session-start")
    exe = root / ".venv" / "Scripts" / "maapilot.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text('#!/bin/sh\necho "{\\"ran\\": \\"$*\\"}"\n', encoding="utf-8")
    exe.chmod(0o755)
    envfile = tmp_path / "env.sh"
    p = _run_hook(root, envfile)
    assert p.returncode == 0 and json.loads(p.stdout) == {"ran": "doctor --hook"}, p.stderr
    assert "/.venv/Scripts:$PATH" in envfile.read_text(encoding="utf-8") and "/.venv/bin" not in envfile.read_text(encoding="utf-8")


def test_gitattributes_keeps_hook_lf():
    p = subprocess.run(["git", "check-attr", "eol", "text", "bin/session-start"], cwd=paths.ROOT, capture_output=True,
                       text=True)
    assert "eol: lf" in p.stdout
    assert b"\r\n" not in (paths.ROOT / "bin" / "session-start").read_bytes()

# --- calc / data -----------------------------------------------------------------------------------

@needs_data
def test_calc_need_adds_prerequisites(capsys):
    common.write_json(paths.STATE / "box.json", {"at": common.iso(), "operators": {
        "泥岩": {"elite": 0, "level": 1, "rarity": 6}, "遥": {"elite": 0, "level": 1, "rarity": 6}}})
    code, env = run(capsys, "calc", "need", "干员 泥岩 → 模组X 1级")
    assert code == 0, env
    d = env["data"]
    assert d["need"]["exp"] > 0 and any("模组X 要先精2" in a for a in d["assumed"])
    code, env = run(capsys, "calc", "need", "干员 遥 → 技能 7级")
    d = env["data"]
    assert d["need"]["lmd"] > 0 and any("技能 7 级要先精1" in a for a in d["assumed"])
    code, env = run(capsys, "calc", "need", "干员 遥 → 技能 4级")          # 4 级不用精1
    assert not any("要先精" in a for a in env["data"]["assumed"])
    common.write_json(paths.STATE / "box.json", {"at": common.iso(), "operators": {
        "泥岩": {"elite": 2, "level": 70, "rarity": 6}}})
    code, env = run(capsys, "calc", "need", "干员 泥岩 → 模组X 1级")
    assert env["data"]["need"]["exp"] == 0 and not any("要先精" in a for a in env["data"]["assumed"])


@needs_data
def test_data_operator_module_missions(capsys):
    code, env = run(capsys, "data", "operator", "麦哲伦")
    assert code == 0
    m = env["data"]["modules"][0]
    assert m["unlock"] == {"elite": 2, "level": 60}
    assert len(m["missions"]) == 2 and all(x["desc"] for x in m["missions"])
    assert any(x.get("stage") == "2-5" for x in m["missions"])


def test_state_op_is_registered(capsys, cfg):
    code, env = run(capsys, "state", "op", "不存在的人")
    assert code in (2, 5) and env["command"] == "state op"
