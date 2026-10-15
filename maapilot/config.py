"""读 private/config.yaml（格式见 templates/config.yaml 与 docs/cli.md）。只读；写配置由 setup 里的 agent 用文件工具做。
DEFAULTS 与 templates/config.yaml 保持一致。"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from maapilot import paths
from maapilot.cliutil import ENVIRONMENT, BAD_REQUEST, CliError

DEFAULTS = {
    "emulator": {"address": None, "adb": "adb"},
    "server": "Official",
    "agent": "claude",
    "tools": {"maa": "maa", "claude": "claude", "codex": "codex"},
    "schedule": ["04:40", "16:40"],
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
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
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
    emu = load().get("emulator") or {}
    if not emu.get("address"):
        raise CliError("no_emulator", "config.yaml 里没有模拟器地址（emulator.address）",
                       hint="跑 maapilot doctor 找地址，或重新做 setup", exit=ENVIRONMENT)
    return Connection(address=str(emu["address"]), adb=str(emu.get("adb") or "adb"))


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
    """tools.<name>（maa / claude / codex）：定时任务里 PATH 很短，setup 写绝对路径。"""
    cfg = cfg if cfg is not None else load_or_defaults()
    return str((cfg.get("tools") or {}).get(name) or name)


def accept_terms(cfg: dict | None = None) -> bool | None:
    """game.accept_terms：游戏更新后弹出用户协议 / 隐私政策更新时，agent 能不能直接点同意。
    None（默认）= 还没问过用户，每次都问；True = 可以直接点同意；False = 每次都问。写成别的值当作 None。"""
    cfg = cfg if cfg is not None else load_or_defaults()
    v = (cfg.get("game") or {}).get("accept_terms") if isinstance(cfg.get("game"), dict) else None
    return v if isinstance(v, bool) else None
