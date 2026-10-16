"""maapilot.oscompat.procs：起进程、结束进程树、查进程表、UTF-8 跑子进程。在 macOS 上用真的子进程验证。"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from maapilot.oscompat import procs

ROOT = Path(__file__).resolve().parents[1]
posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX 进程组")

# 起一个孙进程，把两个 pid 写进文件，然后一直睡
TREE = """\
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(f"{os.getpid()} {child.pid}")
time.sleep(60)
"""


def _gone(pid: int, within: float = 8) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        if procs.cmdline(pid) is None:            # 不在了，或只剩僵尸
            return True
        time.sleep(0.1)
    return False


def _pids(f: Path, within: float = 15) -> tuple[int, int]:
    end = time.monotonic() + within
    while time.monotonic() < end:
        if f.exists() and f.read_text(encoding="utf-8").strip():
            a, b = f.read_text(encoding="utf-8").split()
            return int(a), int(b)
        time.sleep(0.05)
    raise AssertionError("子进程没起来")


@pytest.fixture
def tree(tmp_path):
    f = tmp_path / "pids"
    p = procs.spawn([sys.executable, "-c", TREE, str(f)], stdout=subprocess.DEVNULL)
    me, kid = _pids(f)
    yield p, me, kid
    for pid in (me, kid):
        try:
            os.kill(pid, signal.SIGKILL) if sys.platform != "win32" else procs.kill_pids([pid], 0)
        except OSError:
            pass


def test_stop_signals():
    assert signal.SIGTERM in procs.STOP_SIGNALS and signal.SIGINT in procs.STOP_SIGNALS
    if sys.platform != "win32":
        assert signal.SIGHUP in procs.STOP_SIGNALS


def test_no_sighup_import_smoke():
    """Windows 没有 SIGHUP：删掉它，各模块照样能导入，STOP_SIGNALS 里也没有它。"""
    code = ("import signal\nif hasattr(signal, 'SIGHUP'):\n    del signal.SIGHUP\n"
            "import maapilot.crew.maa, maapilot.crew.maacore, maapilot.crew.maacore_runner, maapilot.flows.daily\n"
            "from maapilot.oscompat import procs\n"
            "assert all(s.name != 'SIGHUP' for s in procs.STOP_SIGNALS), procs.STOP_SIGNALS\n"
            "assert maapilot.crew.maa.STOP_SIGNALS == procs.STOP_SIGNALS\nprint('ok')\n")
    r = procs.run_text([sys.executable, "-c", code], timeout=60, cwd=str(ROOT))
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr


def test_spawn_and_descendants(tree):
    p, me, kid = tree
    # Windows 上 .venv\Scripts\python.exe 是个启动器，真的解释器是它的子进程
    assert p.pid == me or (sys.platform == "win32" and me in procs.descendants(p.pid))
    d = procs.descendants(me)
    assert kid in d and "time.sleep(60)" in d[kid][1]
    if sys.platform != "win32":
        assert d[kid][0] == me                     # 孙进程在子进程的组里（子进程自成一组）
        assert os.getpgid(me) == me != os.getpgrp()
    assert procs.descendants(99999999) == {}


def test_kill_tree_stops_whole_tree(tree):
    p, me, kid = tree
    t0 = time.monotonic()
    code = procs.kill_tree(p, 5)
    took = time.monotonic() - t0
    assert code is not None and _gone(me) and _gone(kid)
    # 先礼：TERM（Windows 上 Ctrl+Break，经 venv 启动器也一样）就走了，用不着等满 grace
    assert took < 5


# --- Windows 的 creationflags 与 Ctrl+Break（组合规则在哪个系统上都查）-------------------------------------------------

@pytest.mark.parametrize("group,console,extra,want", [
    (True, True, 0, procs.CREATE_NEW_PROCESS_GROUP),
    (True, False, 0, procs.CREATE_NEW_PROCESS_GROUP | procs.CREATE_NO_WINDOW),     # pythonw / 任务计划程序：不弹窗
    (False, True, 0, 0),
    (False, False, 0, procs.CREATE_NO_WINDOW),
    (True, False, procs.DETACHED_PROCESS, procs.CREATE_NEW_PROCESS_GROUP | procs.DETACHED_PROCESS),
    (False, False, procs.CREATE_NEW_CONSOLE, procs.CREATE_NEW_CONSOLE),             # 调用方要的就不改
])
def test_spawn_flags(group, console, extra, want):
    assert procs.spawn_flags(group, console, extra) == want


def test_break_route():
    G, NW = procs.CREATE_NEW_PROCESS_GROUP, procs.CREATE_NO_WINDOW
    assert procs.break_route(G, True) == "ours"
    assert procs.break_route(procs.spawn_flags(True, False), False) == "own"     # 没控制台：挂到子进程的控制台上发
    assert procs.break_route(G | NW, True) == "own"
    assert procs.break_route(0, True) is None                                      # 没自成一组：只能结束 Job
    assert procs.break_route(G | procs.DETACHED_PROCESS, False) is None
    assert procs.break_route(G, False) is None


def test_run_text_flags_follow_console(monkeypatch):
    monkeypatch.setattr(procs, "has_console", lambda: False)
    assert procs._creationflags(False) == procs.CREATE_NO_WINDOW
    monkeypatch.setattr(procs, "has_console", lambda: True)
    assert procs._creationflags(False) == 0


@posix
def test_posix_spawn_unchanged(tree):
    p, me, kid = tree
    assert p._maapilot_break is None and p._maapilot_job is None and procs.has_console()
    called = []
    with procs.on_console_stop(lambda: called.append(1)):          # macOS / Linux 上什么都不做
        pass
    assert called == []


win = pytest.mark.skipif(sys.platform != "win32", reason="Windows 的控制台与 Ctrl+Break")
BASE = getattr(sys, "_base_executable", sys.executable)          # 不经 venv 启动器的 python


def _base_env() -> dict:
    """直接用 BASE 跑时也能导入 maapilot 和 venv 里的包。"""
    import site
    return procs.child_env({"PYTHONPATH": os.pathsep.join([str(ROOT), *site.getsitepackages()])})


# 收到 Ctrl+Break 就写一个文件、正常退出（sleep 要切短：Windows 上长 sleep 不被 Ctrl+Break 打断）
POLITE = ("import os, signal, sys, time\n"
          "def h(*a):\n    open(sys.argv[1], 'w').write('break')\n    sys.exit(0)\n"
          "signal.signal(signal.SIGBREAK, h)\n"
          "open(sys.argv[2], 'w').write(f'{os.getpid()} 0')\n"
          "while True:\n    time.sleep(0.1)\n")


@win
def test_ctrl_break_stops_python_without_venv_launcher(tmp_path):
    f = tmp_path / "pids"
    p = procs.spawn([BASE, "-c", "import os, sys, time\nopen(sys.argv[1], 'w').write(f'{os.getpid()} 0')\n"
                                 "time.sleep(60)\n", str(f)])
    me, _ = _pids(f)
    assert me == p.pid and p._maapilot_break == "ours" and p._maapilot_job is not None
    t0 = time.monotonic()
    procs.kill_tree(p, 5)
    assert time.monotonic() - t0 < 5 and p.returncode != 1             # 1 是结束 Job 时给的退出码


@win
def test_stops_with_us_reaches_grandchild_while_main_thread_waits(tmp_path):
    """maapilot 子进程的主线程卡在等 maa 上（Windows 上等进程不被信号打断）：Ctrl+Break 一到，控制台事件的线程先把
    maa 体面地停掉，主线程醒来照常走信号处理退出（128 + SIGBREAK）。"""
    flag, pidf = tmp_path / "break", tmp_path / "pids"
    child = ("import subprocess, sys\nfrom maapilot.crew import maa\nfrom maapilot.oscompat import procs\n"
             f"g = procs.spawn([sys.executable, '-c', {POLITE!r}, sys.argv[1], sys.argv[2]], stdout=subprocess.PIPE)\n"
             "with maa._stops_with_us(g):\n    g.communicate()\n")
    p = procs.spawn([BASE, "-c", child, str(flag), str(pidf)], env=_base_env(), cwd=str(ROOT))
    grandchild, _ = _pids(pidf)
    t0 = time.monotonic()
    code = procs.kill_tree(p, 10)
    assert time.monotonic() - t0 < 10, "Ctrl+Break 没让它在 grace 里走"
    assert code == 128 + signal.SIGBREAK and flag.read_text() == "break" and _gone(grandchild)


NO_CONSOLE = f"""\
import json, os, subprocess, sys, time
from maapilot.oscompat import procs
out = sys.argv[1]
flag, pidf = out + ".break", out + ".pids"
res = {{"console": procs.has_console()}}
p = procs.spawn([sys.executable, "-c", {POLITE!r}, flag, pidf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
res["route"], res["job"] = p._maapilot_break, p._maapilot_job is not None
end = time.monotonic() + 20
while not (os.path.exists(pidf) and open(pidf).read().strip()) and time.monotonic() < end:
    time.sleep(0.05)
t0 = time.monotonic()
res["code"] = procs.kill_tree(p, 10)
res["took"] = time.monotonic() - t0
res["break"] = os.path.exists(flag)
q = procs.spawn([sys.executable, "-c", "print('ok')"], stdout=subprocess.PIPE)   # stdin、stderr 继承：标准句柄放回去了
res["after"] = q.communicate(timeout=30)[0].strip()
open(out, "w").write(json.dumps(res))
"""


@win
def test_no_console_parent_breaks_via_attach(tmp_path):
    """本进程没有控制台（pythonw、任务计划程序）：子进程加 CREATE_NO_WINDOW、照样放进 Job，Ctrl+Break 挂到它的控制台上送到。"""
    out = tmp_path / "res.json"
    r = subprocess.Popen([BASE, "-c", NO_CONSOLE, str(out)], env=_base_env(), cwd=str(ROOT),
                         creationflags=procs.DETACHED_PROCESS | procs.CREATE_NEW_PROCESS_GROUP,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert r.wait(timeout=90) == 0
    import json
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["console"] is False and res["route"] == "own" and res["job"] is True, res
    assert res["break"] and res["code"] == 0 and res["took"] < 10 and res["after"] == "ok", res


@posix
def test_kill_tree_escalates_to_kill(tmp_path):
    """不理 TERM 的进程：等 grace 秒后 KILL。"""
    f = tmp_path / "pids"
    code = ("import os, signal, sys, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "open(sys.argv[1], 'w').write(f'{os.getpid()} 0')\ntime.sleep(60)\n")
    p = procs.spawn([sys.executable, "-c", code, str(f)])
    _pids(f)
    t0 = time.monotonic()
    assert procs.kill_tree(p, 0.5, kill_wait_s=5) == -signal.SIGKILL
    assert time.monotonic() - t0 < 5


@posix
def test_kill_tree_also_kills_other_groups(tmp_path):
    """子孙另起了进程组（codex 那样）：记下来的（also）也要一起结束，主进程先走了也一样。"""
    f = tmp_path / "pids"
    code = ("import os, subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
            "open(sys.argv[1], 'w').write(f'{os.getpid()} {c.pid}')\ntime.sleep(60)\n")
    p = procs.spawn([sys.executable, "-c", code, str(f)])
    me, kid = _pids(f)
    seen = procs.descendants(me)
    assert seen[kid][0] == kid
    try:
        procs.kill_tree(p, 2, also=seen)
        assert _gone(me) and _gone(kid)
    finally:
        try:
            os.kill(kid, signal.SIGKILL)
        except OSError:
            pass


def test_kill_pids(tree):
    p, me, kid = tree
    procs.kill_pids([kid, 99999999], 1)
    assert _gone(kid)
    assert procs.pid_alive(me)
    procs.kill_tree(p, 5)


def test_pid_alive():
    assert procs.pid_alive(os.getpid())
    assert not procs.pid_alive(99999999)


def test_ancestors():
    a = procs.ancestors()
    assert a and a[0] == os.getppid() and os.getpid() not in a
    assert procs.ancestors(99999999) == []


def test_find_by_name(tree):
    p, me, kid = tree
    import psutil
    name = psutil.Process(me).name()                       # python3.13 之类：两个测试进程都叫这个
    name = name[:-4] if name.lower().endswith(".exe") else name
    found = procs.find_by_name(name.upper() + ".exe")       # 不分大小写、不管 .exe
    assert me in found and kid in found and os.getpid() not in found
    assert procs.find_by_name("no-such-program-xyz") == []


def test_cmdline(tree):
    p, me, kid = tree
    assert procs.cmdline(kid) == procs.descendants(me)[kid][1]
    assert procs.cmdline(99999999) is None


def test_run_text_utf8():
    code = "import sys; sys.stdout.buffer.write('理智 合成玉\\n'.encode('utf-8') + b'\\xff\\n'); print(sys.stdin.read())"
    r = procs.run_text([sys.executable, "-c", code], timeout=30)
    assert r.returncode == 0 and r.stdout.startswith("理智 合成玉\n�\n")       # 坏字节替换掉，不抛错
    r = procs.run_text([sys.executable, "-c", "import sys; print(sys.stdin.read()[::-1])"], 30, input="玉成合")
    assert r.stdout.strip() == "合成玉"


def test_run_text_env_and_utf8_mode():
    code = "import os, sys; print(os.environ.get('PYTHONUTF8'), os.environ.get('X_T'), sys.flags.utf8_mode)"
    r = procs.run_text([sys.executable, "-c", code], timeout=30)
    assert r.stdout.split() == ["1", "None", "1"]
    r = procs.run_text([sys.executable, "-c", code], timeout=30, env={**os.environ, "X_T": "y"})
    assert r.stdout.split()[:2] == ["1", "y"]
    assert procs.child_env({"A": "b"})["A"] == "b"


def test_spawn_text_is_utf8(tmp_path):
    p = procs.spawn([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read() + '源石')"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, _ = p.communicate("合成玉", timeout=30)
    assert out == "合成玉源石"


# --- MaaCore 在 Windows 上（P0.4）--------------------------------------------------------------------------------------

@pytest.mark.parametrize("machine,asset", [("AMD64", "MAA-v6.1.0-win-x64.zip"), ("ARM64", "MAA-v6.1.0-win-arm64.zip")])
def test_maacore_windows_asset(monkeypatch, machine, asset):
    from maapilot.crew import maacore
    monkeypatch.setattr(maacore.platform, "system", lambda: "Windows")
    monkeypatch.setattr(maacore.platform, "machine", lambda: machine)
    pat = maacore._asset_pattern()
    assert pat.match(asset) and not pat.match("MAA-v6.1.0-win-x64-OTA.zip") and not pat.match("MAA-v6.1.0-linux-x86_64.tar.gz")
    other = "MAA-v6.1.0-win-arm64.zip" if machine == "AMD64" else "MAA-v6.1.0-win-x64.zip"
    assert not pat.match(other)


def test_maacore_finds_dll(tmp_path, monkeypatch):
    from maapilot.crew import maacore
    monkeypatch.setattr(maacore, "root", lambda: tmp_path)
    (tmp_path / "v6.1.0" / "MAA").mkdir(parents=True)
    (tmp_path / "v6.1.0" / "MAA" / "MaaCore.dll").write_bytes(b"")
    assert maacore.find() == ("v6.1.0", tmp_path / "v6.1.0" / "MAA")
