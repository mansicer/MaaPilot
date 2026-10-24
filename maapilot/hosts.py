"""agent 宿主（Claude Code、Codex）的程序在哪：`host status / install`、doctor 用。

v0.2.0 起项目不再无头叫起 agent、也不往会话里推消息：定时日常由 harness 的会话内定时在用户的会话里叫醒 agent。
这里只剩找程序：`tools.<宿主>` 写了就只认它，没写按 PATH → 常见安装位置找。Codex 要信任这个文件夹才加载项目的
`.codex/rules/`（`maapilot host install codex`，见 tools/host_cmd.py）。config.yaml 里旧的 `agent` 不再用（写了就忽略，doctor 提示可以删）。
"""
from __future__ import annotations

import shutil

from maapilot.oscompat import exe

HOSTS = ("claude", "codex")


# PATH 上没有时再到这些常见位置找。%VAR% 没有的位置跳过，
# 所以 Windows 的写法放在这里也不影响 macOS / Linux。Windows 上 %LOCALAPPDATA%\Programs\… 是猜的安装位置（没核实）
CODEX_FALLBACKS = ("~/.local/bin/codex", "/opt/homebrew/bin/codex", "/usr/local/bin/codex",
                   r"%APPDATA%\npm\codex.cmd", r"%LOCALAPPDATA%\pnpm\codex.cmd", r"%USERPROFILE%\scoop\shims\codex.exe",
                   r"%USERPROFILE%\.local\bin\codex.exe", r"%LOCALAPPDATA%\Programs\OpenAI\Codex\codex.exe")
# Claude Code：原生安装器装在 ~/.local/bin（Windows 上是 %USERPROFILE%\.local\bin\claude.exe），npm 装的在 npm 的目录
CLAUDE_FALLBACKS = ("~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
                    r"%USERPROFILE%\.local\bin\claude.exe", r"%APPDATA%\npm\claude.cmd", r"%LOCALAPPDATA%\pnpm\claude.cmd")


def fallbacks(host: str) -> tuple[str, ...]:
    return CODEX_FALLBACKS if host == "codex" else CLAUDE_FALLBACKS


def resolve(cfg: dict, host: str, *, which=shutil.which, environ=None, fallback: bool = False) -> str | None:
    """宿主程序的绝对路径；Windows 上找到 npm 的包装时优先原生的 .exe（见 oscompat.exe）。找不到 → None。
    写了 tools.<宿主> 就只认它（写错了要报出来，不悄悄换成别的程序；fallback=True 时再往下找）；
    没写：PATH → 常见安装位置。doctor、host status / install 用同一个规则，找到的是同一个程序。"""
    configured = (cfg.get("tools") or {}).get(host)
    cands = [str(configured)] if configured else []
    if fallback or not configured:
        cands += [host, *fallbacks(host)]
    return exe.resolve(host, cands, which=which, environ=environ)


def binary(cfg: dict, host: str) -> str:
    """宿主程序（找得到就给绝对路径，找不到给 tools.<宿主> 原样的写法）。"""
    return resolve(cfg, host) or str((cfg.get("tools") or {}).get(host) or host)


def codex_path(cfg: dict) -> str | None:
    """codex 的绝对路径：tools.codex 找不到时也往下找（PATH、常见安装位置）。找不到 → None。"""
    return resolve(cfg, "codex", fallback=True)
