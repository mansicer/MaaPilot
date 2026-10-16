"""找外部程序（claude、codex）：PATH → 常见安装位置；Windows 上优先用原生的 .exe，不用 npm 的 .cmd / .ps1 包装。

npm 在 Windows 上装的 claude.cmd / codex.cmd 要经 cmd.exe 转一道（多行参数被截断、% ^ & 被改写），结束进程树时
也多一层 node。所以找到的是包装时，再看看有没有原生程序：同目录的同名 .exe、Claude Code 原生安装器装的
%USERPROFILE%\\.local\\bin\\claude.exe、Codex 的 npm 包里自带的 codex.exe（包里的目录结构没核实过，找不到就照旧用包装）。
MSIX 打包的程序（应用商店的 Python / Node 装的东西）在 %LOCALAPPDATA%\\Packages\\…\\LocalCache\\ 下，只有它自己看得见，
任务计划程序起的进程找不到：in_msix_cache 认出来，doctor 提醒。
"""
from __future__ import annotations

import os
import platform
import re
import shutil
from pathlib import Path

from maapilot.oscompat import WINDOWS

SHIM_EXTS = (".cmd", ".bat", ".ps1")
# Codex 的 npm 包自带的原生程序（vendor/<目标平台>/codex/codex.exe；新版可能拆到 @openai/codex-<平台> 包里）。没核实过，尽力而为
CODEX_VENDORED = ("node_modules/@openai/codex/vendor/*/codex/codex.exe",
                  "node_modules/@openai/codex/node_modules/@openai/codex-*/vendor/*/codex/codex.exe",
                  "node_modules/@openai/codex-*/vendor/*/codex/codex.exe")


def is_shim(path) -> bool:
    """Windows 上 path 是不是 .cmd / .bat / .ps1 这种包装（不是原生程序）。macOS / Linux 一律 False。"""
    return WINDOWS and str(path).lower().endswith(SHIM_EXTS)


def in_msix_cache(path) -> bool:
    """path 在不在 MSIX 的隔离目录（…\\Packages\\<包>\\LocalCache\\…）里：那里的文件只有打包的程序自己看得见。"""
    s = str(path).replace("/", "\\").lower()
    return "\\packages\\" in s and "\\localcache\\" in s


_VAR = re.compile(r"%([^%]+)%")


def expand(template: str, environ=None) -> str | None:
    """%VAR% 和 ~ 展开成路径（不靠 os.path.expandvars：它在 macOS 上不认 %VAR%，测试要能测 Windows 的写法）。
    用到的环境变量没有 → None（这个位置不用找）。"""
    e = os.environ if environ is None else environ
    missing = []

    def sub(m):
        v = e.get(m.group(1)) or e.get(m.group(1).upper())
        if not v:
            missing.append(m.group(1))
            return ""
        return v
    s = _VAR.sub(sub, template)
    if missing:
        return None
    return os.path.normpath(os.path.expanduser(s))


def _arch_first(paths: list[Path]) -> list[Path]:
    """本机架构的排前面（x86_64 / aarch64 的 vendor 目录都可能有）。"""
    arm = platform.machine().lower() in ("arm64", "aarch64")
    want = "aarch64" if arm else "x86_64"
    return sorted(paths, key=lambda p: (want not in str(p).lower(), str(p)))


def native_for(name: str, shim, environ=None) -> str | None:
    """shim 是包装（is_shim）时，对应的原生程序：同目录的 <name>.exe → claude 的原生安装（%USERPROFILE%\\.local\\bin）
    → codex 的 npm 包里自带的。没有 → None。"""
    if not is_shim(shim):
        return None
    d = Path(shim).parent
    cands = [d / f"{name}.exe"]
    e = os.environ if environ is None else environ
    if name == "claude" and e.get("USERPROFILE"):
        cands.append(Path(e["USERPROFILE"]) / ".local" / "bin" / "claude.exe")
    if name == "codex":
        found = []
        for pat in CODEX_VENDORED:
            found += [p for p in d.glob(pat) if p.is_file()]
        cands += _arch_first(found)
    return next((str(p) for p in cands if p.is_file()), None)


def resolve(name: str, candidates, *, which=shutil.which, environ=None) -> str | None:
    """按顺序找 candidates（程序名或路径，可以写 %VAR%、~；用到的环境变量没有的跳过），第一个找得到的给绝对路径；
    找到的是 Windows 的包装、又有原生程序 → 用原生的。都找不到 → None。which 换成假的就能测。"""
    for c in candidates:
        c = expand(c, environ)
        found = which(c) if c else None
        if found:
            found = os.path.abspath(found)
            return native_for(name, found, environ) or found
    return None
