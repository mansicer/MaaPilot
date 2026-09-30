"""Which emulator MaaCrew talks to: the adb address and the adb program.

MaaCrew does not decide this itself: the caller (MaaPilot reads `emulator` from private/config.yaml,
see docs/reference/cli-contract.md#模拟器连接) calls `configure()` once before driving the device.
`maapilot.crew.core.Game` connects with `current()`, and every maa-cli call appends `maa_cli_args()` so
maa-cli uses the same device instead of whatever its own profile says.

Pure settings: this module never touches the device and does not import MaaFramework.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Connection:
    address: str            # adb serial: host:port or a serial from `adb devices`
    adb: str = "adb"        # adb program (looked up on PATH unless absolute)


class NotConfigured(RuntimeError):
    pass


_current: Connection | None = None


def configure(conn: Connection | None) -> None:
    """Set (or with None, clear) the connection used by Game and maa-cli calls."""
    global _current
    _current = conn


def current() -> Connection:
    if _current is None:
        raise NotConfigured("没有配置模拟器连接：在 private/config.yaml 填 emulator.address（见 maapilot doctor）")
    return _current


def configure_from_argv(argv: list[str]) -> list[str]:
    """For the modules' standalone debug entry points (`python -m maapilot.crew.signin` …), which have no config.yaml:
    take `--addr <address>` and optional `--adb <program>` out of argv, configure() with them and return
    the remaining arguments. Without --addr nothing is configured (Game() then raises NotConfigured)."""
    rest, opts = [], {}
    it = iter(argv)
    for arg in it:
        if arg in ("--addr", "--adb"):
            val = next(it, None)
            if not val:
                raise NotConfigured(f"{arg} 后面要跟值")
            opts[arg[2:]] = val
        else:
            rest.append(arg)
    if "addr" in opts:
        configure(Connection(address=opts["addr"], adb=opts.get("adb", "adb")))
    elif "adb" in opts:
        raise NotConfigured("--adb 要和 --addr 一起给")
    return rest


def maa_cli_args() -> list[str]:
    """Arguments appended to `maa run` / `maa fight` / `maa startup` so maa-cli connects to the same device.
    待真机核对：maa-cli 0.7.5 的 CommonArgs 里有 "ADB serial number of device or MaaTools address"，
    推断为 `-a/--addr`；本轮不能运行 maa 验证。"""
    return ["--addr", current().address]
