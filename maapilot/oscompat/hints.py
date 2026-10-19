"""各系统的安装提示、模拟器常见的 adb 端口（doctor 用）。"""
from __future__ import annotations

from maapilot.oscompat import MACOS, WINDOWS

MAA_RELEASES = "https://github.com/MaaAssistantArknights/maa-cli/releases"

_HINTS = {
    "maa": {"*": "maapilot tools install maa-cli（从 GitHub 下载实测过的版本装进项目的 .tools/，没有 MaaCore 顺手 maa install）"},
    "maa_upgrade": {"*": "maapilot tools install maa-cli（config.yaml 的 tools.maa 写了别的路径时改成 maa）"},
    "adb": {"darwin": "maapilot tools install adb（Google 官方 platform-tools，装进项目的 .tools/）",
            "win32": "优先用模拟器自带的 adb.exe（把完整路径写进 emulator.adb）；没有就 maapilot tools install adb",
            "linux": "maapilot tools install adb，或系统包管理器（如 sudo apt install adb）"},
    "uv": {"darwin": "curl -LsSf https://astral.sh/uv/install.sh | sh",
           "win32": "winget install astral-sh.uv",
           "linux": "curl -LsSf https://astral.sh/uv/install.sh | sh"},
    "codex": {"*": "npm install -g @openai/codex"},
    "claude": {"*": "按 Claude Code 官方文档安装（https://docs.claude.com/claude-code）"},
    "vcredist": {"win32": "winget install Microsoft.VCRedist.2015+.x64（MaaCore 要用的 VC++ 运行库）",
                 "*": "不需要"},
}

# 地址没填时顺手探测的常见模拟器端口（只 adb connect 本机，不点任何东西）
COMMON_PORTS: dict[str, tuple[int, ...]] = {
    # MuMu Pro / BlueStacks Air / 别的
    "darwin": (5555, 5557, 7555, 16384, 16416, 62001, 21503),
    # MuMu 12（16384 起每开一个 +32）、MuMu 6（7555）、雷电 / BlueStacks（5555、5557、5565）、夜神（62001、62025、59865）、逍遥（21503）
    "win32": (16384, 16416, 16448, 7555, 5555, 5557, 5565, 62001, 62025, 59865, 21503, 21513),
    # Waydroid、Genymotion、redroid 一般是 5555
    "linux": (5555, 5557),
}


def _os() -> str:
    return "win32" if WINDOWS else "darwin" if MACOS else "linux"


def install_hint(tool: str) -> str:
    """这台电脑上装 tool（maa / maa_upgrade / adb / uv / codex / claude / vcredist）的提示。"""
    h = _HINTS.get(tool) or {}
    return h.get(_os()) or h.get("*") or f"安装 {tool}"


def common_ports() -> tuple[int, ...]:
    return COMMON_PORTS[_os()]

