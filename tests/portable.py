"""测试里跨系统的小工具：假程序（claude / codex / maa / crontab）、只在有 cron 的系统上跑的标记。"""
from __future__ import annotations

import stat
import sys
import textwrap
from pathlib import Path

import pytest

WINDOWS = sys.platform == "win32"
# cron 后端只在 macOS / Linux（Windows 用任务计划程序，见 test_oscompat_sched）
needs_cron = pytest.mark.skipif(WINDOWS, reason="cron 只在 macOS / Linux；Windows 用任务计划程序")


def fake_program(path: Path, body: str) -> Path:
    """写一个用本 Python 跑 body 的假程序，返回能直接执行的路径。
    macOS / Linux：path 本身（#! 脚本，加可执行位）。Windows：path.py + path.cmd（同 npm 装的 claude / codex 的样子），返回 .cmd。"""
    path = Path(path)
    body = textwrap.dedent(body)
    if not WINDOWS:
        path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path
    py = path.with_name(path.name + ".py")
    py.write_text(body, encoding="utf-8")
    cmd = path.with_name(path.name + ".cmd")
    cmd.write_text(f'@"{sys.executable}" "%~dp0{py.name}" %*\r\n', encoding="utf-8")
    return cmd


@pytest.fixture
def sched_ready(tmp_path, monkeypatch):
    """定时日常要执行的程序都找得到（daily schedule 装之前要查）：假的 maa / claude / codex / adb 放进 PATH，
    .venv 的 python 指到跑测试的这个解释器（worktree 里可能没有自己的 .venv）。返回假程序的目录。"""
    import os
    from maapilot.oscompat import venv
    d = tmp_path / "sched-bin"
    d.mkdir(exist_ok=True)
    for n in ("maa", "claude", "codex", "adb"):
        fake_program(d / n, "pass\n")
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(venv, "venv_bin", lambda: Path(sys.executable).parent)
    return d
