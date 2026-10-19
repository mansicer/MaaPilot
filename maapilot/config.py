"""读 private/config.yaml（格式见 templates/config.yaml 与 docs/cli.md）。只读；写配置由 setup 里的 agent 用文件工具做。
DEFAULTS 与 templates/config.yaml 保持一致。"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from maapilot import paths
from maapilot.cliutil import ENVIRONMENT, BAD_REQUEST, CliError
from maapilot.oscompat import fs

DEFAULTS = {
    "emulator": {"address": None, "adb": "adb"},
    "server": "Official",
    "agent": "claude",
    "tools": {"maa": "maa", "claude": "claude", "codex": "codex"},
    "schedule": ["04:40", "16:40"],
    "schedule_options": {"run_if_missed": True, "wake_computer": False},
    "daily": {"tasks": ["startup", "recruit", "infrast", "mall", "annihilation", "award"],
              "default_stage": "1-7", "params": {}, "agent_timeout_min": 90, "recovery_timeout_min": 30,
              "task_timeout_min": 90},
    "game": {"accept_terms": None},
}


def exists() -> bool:
    return paths.CONFIG.exists()


def load(path: Path | None = None) -> dict:
    """config.yaml 合并默认值后的内容。文件不存在 → CliError(ENVIRONMENT)；YAML 写错 → CliError(BAD_REQUEST)。"""
    p = path or paths.CONFIG
    if not p.exists():
        raise CliError("no_config", "还没有配置文件 private/config.yaml", hint="先完成 setup", exit=ENVIRONMENT)
    try:
        raw = yaml.safe_load(fs.read_text_utf8(p)) or {}
    except fs.BadEncoding as e:                     # Windows 记事本"ANSI"存的（GBK 等）
        raise CliError("invalid_config", str(e), hint="用编辑器另存为 UTF-8",
                       exit=BAD_REQUEST) from None
    except yaml.YAMLError as e:
        raise CliError("invalid_config", f"config.yaml 写错了：{e}", exit=BAD_REQUEST) from None
    if not isinstance(raw, dict):
        raise CliError("invalid_config", "config.yaml 顶层应是映射", exit=BAD_REQUEST)
    return _merge(DEFAULTS, raw)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)                       # 不让调用方改到 DEFAULTS
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(base.get(k), dict) and isinstance(v, dict) else v
    return out


def connection():
    """配置里的模拟器连接（maapilot.crew.device.Connection）；没填地址 → CliError(ENVIRONMENT)。"""
    from maapilot.crew.device import Connection
    cfg = load()
    emu = cfg.get("emulator") or {}
    if not emu.get("address"):
        raise CliError("no_emulator", "config.yaml 里没有模拟器地址（emulator.address）",
                       hint="跑 maapilot doctor 找地址，或重新做 setup", exit=ENVIRONMENT)
    return Connection(address=str(emu["address"]), adb=adb_path(cfg))


# --- 由 server 推出的值 -------------------------------------------------------------

# 游戏包名（doctor 查前台用）
PACKAGES = {
    "Official": "com.hypergryph.arknights",
    "Bilibili": "com.hypergryph.arknights.bilibili",
    "YoStarEN": "com.YoStarEN.Arknights",
    "YoStarJP": "com.YoStarJP.Arknights",
    "YoStarKR": "com.YoStarKR.Arknights",
    "txwy": "tw.txwy.and.arknights",
}
# 企鹅物流的服务器代码
PENGUIN = {"Official": "CN", "Bilibili": "CN", "txwy": "CN", "YoStarEN": "US", "YoStarJP": "JP", "YoStarKR": "KR"}


def load_or_defaults() -> dict:
    """能读就读 config.yaml，读不了（没有、写错）就用默认值。给不碰模拟器的查询用（数据、计算）。"""
    try:
        return load()
    except CliError:
        return _merge(DEFAULTS, {})


def server(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else load_or_defaults()
    return str(cfg.get("server") or "Official")


def penguin_server(cfg: dict | None = None) -> str:
    return PENGUIN.get(server(cfg), "CN")


def package(cfg: dict | None = None) -> str:
    return PACKAGES.get(server(cfg), PACKAGES["Official"])


def tool(name: str, cfg: dict | None = None) -> str:
    """tools.<name>（maa / claude / codex）：定时任务里 PATH 很短，setup 写绝对路径。
    maa 写的是程序名（模板默认）时，项目 .tools 里装了（maapilot tools install）就用装好的（见 maapilot.toolchain）。"""
    from maapilot import toolchain
    cfg = cfg if cfg is not None else load_or_defaults()
    configured = str((cfg.get("tools") or {}).get(name) or name)
    return toolchain.resolve(configured, name) if name in toolchain.DIRS else configured


def adb_path(cfg: dict | None = None) -> str:
    """emulator.adb：写了路径就用它；写的是 adb（模板默认）或没写 → 项目 .tools 里装了就用装好的，没有再在 PATH 上找。"""
    from maapilot import toolchain
    cfg = cfg if cfg is not None else load_or_defaults()
    return toolchain.resolve((cfg.get("emulator") or {}).get("adb"), "adb")


def schedule_options(cfg: dict | None = None) -> dict:
    """schedule_options：到点时电脑关着或睡着，开机 / 醒来后补跑（run_if_missed，默认是）；把睡着的电脑叫醒来跑
    （wake_computer，默认否）。只有 Windows 的任务计划程序用得上，cron 不补跑也不唤醒。不是 true / false → invalid_config。"""
    cfg = cfg if cfg is not None else load_or_defaults()
    raw = cfg.get("schedule_options")
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for k, default in DEFAULTS["schedule_options"].items():
        v = raw.get(k, default)
        if v is None:
            v = default
        if not isinstance(v, bool):
            raise CliError("invalid_config", f"config.yaml 的 schedule_options.{k} 要写 true 或 false，现在是 {v!r}",
                           exit=BAD_REQUEST)
        out[k] = v
    return out


def accept_terms(cfg: dict | None = None) -> bool | None:
    """game.accept_terms：游戏更新后弹出用户协议 / 隐私政策更新时，agent 能不能直接点同意。
    None（默认）= 还没问过用户，每次都问；True = 可以直接点同意；False = 每次都问。写成别的值当作 None。"""
    cfg = cfg if cfg is not None else load_or_defaults()
    v = (cfg.get("game") or {}).get("accept_terms") if isinstance(cfg.get("game"), dict) else None
    return v if isinstance(v, bool) else None
