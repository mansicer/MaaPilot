"""项目 .venv 里的程序与系统 PATH：macOS / Linux 是 .venv/bin/maapilot，Windows 是 .venv\\Scripts\\maapilot.exe。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from maapilot import paths
from maapilot.oscompat import WINDOWS


def venv_bin() -> Path:
    """项目 .venv 的程序目录。当前解释器就在项目 .venv 里时用它所在的目录（最准）。"""
    here = Path(sys.executable).parent
    venv = paths.ROOT / ".venv"
    try:
        if here.resolve().is_relative_to(venv.resolve()):
            return here
    except OSError:
        pass
    return venv / ("Scripts" if WINDOWS else "bin")


def venv_exe(name: str) -> Path:
    """.venv 里的一个程序（Windows 补 .exe）。"""
    return venv_bin() / (name + ".exe" if WINDOWS and not name.lower().endswith(".exe") else name)


def maapilot_cmd() -> str:
    """给用户 / agent 看的、在仓库根目录下能跑的 maapilot 写法。"""
    return r".venv\Scripts\maapilot.exe" if WINDOWS else ".venv/bin/maapilot"


def sys_path_dirs() -> list[str]:
    """定时任务里也要有的系统目录。"""
    if WINDOWS:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return [os.path.join(root, "System32"), root, os.path.join(root, "System32", "WindowsPowerShell", "v1.0")]
    return ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


def join_path(dirs) -> str:
    """去重、保序地拼成 PATH。"""
    seen, out = set(), []
    for d in dirs:
        if not d:
            continue
        key = os.path.normcase(os.path.normpath(str(d)))
        if key not in seen:
            seen.add(key)
            out.append(str(d))
    return os.pathsep.join(out)
