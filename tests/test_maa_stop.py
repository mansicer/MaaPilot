"""maapilot 被叫停时 maa-cli 跟着停：maa-cli 自成进程组（start_new_session），以前日常超时杀掉 `maapilot maa` 时
maa 还在后台接着刷图。这里用一个假的、会再起子进程的长时间 maa，真的发信号验证。"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from maapilot.crew import lock
from maapilot.flows import daily

FAKE_MAA = """\
#!{py}
import os, subprocess, sys, time, pathlib
d = pathlib.Path({d!r})
child = subprocess.Popen(["sleep", "300"])
(d / "maa.pid").write_text(str(os.getpid()))
(d / "child.pid").write_text(str(child.pid))
time.sleep(300)
"""

RUNNER = """\
import sys
from maapilot.crew import device, maa
device.configure(device.Connection(address="emu:1"))
maa.execute(["run", "maapilot-fight"], label="fight", timeout_s=600)
"""


def _gone(pid: int, within: float = 8) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        # 僵尸进程（父进程还没收）也算停了
        st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if not st or st.startswith("Z"):
            return True
        time.sleep(0.2)
    return False


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_killing_maapilot_stops_maa_group(tmp_path, sig):
    priv = tmp_path / "private"
    priv.mkdir()
    fake = tmp_path / "maa"
    fake.write_text(FAKE_MAA.format(py=sys.executable, d=str(tmp_path)))
    fake.chmod(0o755)
    (priv / "config.yaml").write_text(f"emulator: {{address: 'emu:1'}}\ntools: {{maa: {str(fake)!r}}}\n")
    env = {**os.environ, "MAAPILOT_PRIVATE": str(priv), "MAA_CONFIG_DIR": str(tmp_path / "cfg")}
    p = subprocess.Popen([sys.executable, "-c", RUNNER], env=env, start_new_session=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        end = time.monotonic() + 15
        while not (tmp_path / "child.pid").exists() and time.monotonic() < end:
            time.sleep(0.1)
        assert (tmp_path / "child.pid").exists(), "假的 maa 没起来"
        maa_pid = int((tmp_path / "maa.pid").read_text())
        child_pid = int((tmp_path / "child.pid").read_text())
        p.send_signal(sig)
        assert p.wait(timeout=15) == 128 + sig
        assert _gone(maa_pid) and _gone(child_pid)
    finally:
        for f in ("maa.pid", "child.pid"):
            try:
                os.killpg(int((tmp_path / f).read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        if p.poll() is None:
            p.kill()


def test_daily_sweeps_leftover_maa_after_a_task_timeout(monkeypatch):
    """`maapilot maa` 超时被杀（maapilot_call 给 timeout）后，还有 maa 进程 → 日常补一刀，记进告警。"""
    killed = []
    monkeypatch.setattr(daily, "TIMEOUT_LEFTOVER_WAIT_S", 0)
    monkeypatch.setattr(daily, "_leftover_maa", lambda: [4242] if not killed else [])
    monkeypatch.setattr(daily.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(daily, "maapilot_call", lambda args, env, timeout_s=None: (
        1, {"ok": False, "data": None, "error": {"code": "timeout", "message": "超过 90 分钟没结束，已停下"}}))
    monkeypatch.setattr(daily, "_set_step", lambda s: None)

    class Lk:
        def heartbeat(self, **kw):
            pass
    r = daily._Run("20261005-0440", "schedule")
    monkeypatch.setattr(r, "save", lambda: None)
    row = daily._maa_task(r, {}, "farm", ["fight", "1-7"], False, {}, Lk())
    assert row["code"] == "timeout" and killed == [(4242, signal.SIGTERM)]
    assert any("maa 进程" in a for a in r.data["alerts"])
