"""`maapilot tools`：项目 `.tools/` 里的 maa-cli 和 adb（见 maapilot.toolchain）。

- status：装了什么、什么版本、实际会用哪个程序（config.yaml 写了别的路径时照它）。只读。
- install maa-cli|adb：从官方发布下载装好（maa-cli 默认装实测过的版本）；装 maa-cli 后 MaaCore 还没装就顺手 `maa install`。
  要联网、会换掉定时日常执行的程序：agent 先问过用户。拿模拟器锁（日常、MAA 在跑时不换）。
"""
from __future__ import annotations

import re
import subprocess

from maapilot import config
from maapilot.cliutil import FAILED, CliError
from maapilot.tools import common

NAMES = {"maa-cli": "maa", "maa": "maa", "adb": "adb"}
MAA_INSTALL_TIMEOUT_S = 1800


def _configured(name: str, cfg: dict) -> str | None:
    if name == "maa":
        return (cfg.get("tools") or {}).get("maa")
    return (cfg.get("emulator") or {}).get("adb")


def _entry(name: str, cfg: dict) -> dict:
    from maapilot import toolchain
    m = toolchain.managed(name)
    using = config.tool("maa", cfg) if name == "maa" else config.adb_path(cfg)
    configured = _configured(name, cfg)
    out = {"installed": str(m) if m else None, "version": toolchain.installed_version(name) if m else None,
           "using": using, "configured": configured}
    if m and str(m) != using:
        key = "tools.maa" if name == "maa" else "emulator.adb"
        out["note"] = f"config.yaml 的 {key} 写的是 {configured}，用的是它；要改用装好的，把 {key} 改成 {name}"
    return out


def status() -> dict:
    from maapilot import toolchain
    cfg = config.load_or_defaults()
    return {"dir": common.rel(toolchain.tools_dir()), "maa_cli_tested": list(toolchain.MAA_CLI_TESTED),
            "maa-cli": _entry("maa", cfg), "adb": _entry("adb", cfg)}


def _maa_core(maa: str) -> str | None:
    """maa version 里的 MaaCore 版本；没装 → None。"""
    from maapilot.crew import maa as crew_maa
    from maapilot.oscompat import procs
    try:
        p = procs.run_text([maa, "version"], timeout=60, env=crew_maa.env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"maa-?core\s+v?(\S+)", p.stdout or "", re.I)
    return m.group(1) if m else None


def install(what: str, version: str | None = None) -> dict:
    from maapilot import toolchain
    from maapilot.crew import lock
    name = NAMES[what]
    with lock.hold(f"tools install {what}", job="tools install"):
        r = toolchain.install(name, version)
        if name == "maa":
            core = _maa_core(r["path"])
            if core is None:                           # 第一次装：MaaCore 和资源由 maa-cli 下载（几分钟）
                from maapilot.crew import maa as crew_maa
                from maapilot.oscompat import procs
                crew_maa.install_config()
                p = procs.run_text([r["path"], "install"], timeout=MAA_INSTALL_TIMEOUT_S, env=crew_maa.env())
                if p.returncode != 0:
                    raise CliError("maa_install_failed", f"maa-cli 装好了，但 maa install 没成功（退出码 {p.returncode}）："
                                   f"{(p.stderr or p.stdout or '').strip()[-300:]}", exit=FAILED, data=r,
                                   hint="网络能上 GitHub 吗？再跑一次 maapilot tools install maa-cli")
                core = _maa_core(r["path"])
            r["core"] = core
    cfg = config.load_or_defaults()
    r.update({k: v for k, v in _entry(name, cfg).items() if k in ("using", "note")})
    return r


def cmd_status(args) -> dict:
    return status()


@common.logged
def cmd_install(args) -> dict:
    return install(args.what, args.version)


def register(sub) -> None:
    g = sub.add_parser("tools", help="项目 .tools/ 里的 maa-cli 和 adb：看装了什么、下载安装（不用 Homebrew）")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("status", help="装了什么、什么版本、实际用哪个程序")
    p.set_defaults(func=cmd_status, command="tools status")
    p = gs.add_parser("install", help="从官方发布下载装好（maa-cli 默认装实测过的版本；装好后没有 MaaCore 就 maa install）")
    p.add_argument("what", choices=("maa-cli", "adb"))
    p.add_argument("--version", help="maa-cli 的版本（默认实测过的最新版）")
    p.set_defaults(func=cmd_install, command="tools install")
