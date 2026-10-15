"""测试里跨系统的小工具：假程序（claude / codex / maa / crontab）、只在有 cron 的系统上跑的标记。"""
from __future__ import annotations

import stat
import sys
import textwrap
from pathlib import Path

import pytest

WINDOWS = sys.platform == "win32"
# cron 后端只在 macOS / Linux；Windows 的任务计划程序后端还没做（定时相关命令在 Windows 上报 no_scheduler）
needs_cron = pytest.mark.skipif(WINDOWS, reason="cron 只在 macOS / Linux；Windows 任务计划程序后端还没做")


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
