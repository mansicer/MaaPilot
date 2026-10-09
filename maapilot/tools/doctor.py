"""`maapilot doctor [--brief] [--offline] [--fix]`：逐项检查环境，给修复建议。契约见 docs/cli.md「doctor」。

只读：只调 adb 的查询命令（version / devices / connect / get-state / exec-out screencap / shell）、maa-cli 的
version / dir 和 `crontab -l`；不点击、不拿锁、不 import MaaFramework。所有外部访问都经 `Probe`，测试换成假的。
每项 `{name, ok, detail, fix}`；`required: false` 的项（游戏在不在前台、定时任务、setup）不影响 ok_all。

`--brief` 给启动钩子：不碰模拟器、不联网、不跑子进程以外的慢操作，只读 private/ 下的几个文件和 crontab。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from maapilot import cliutil, paths
from maapilot.cliutil import ENVIRONMENT, CliError
from maapilot.tools import common

OCR_FILES = ("det.onnx", "rec.onnx", "keys.txt")
# 地址没填时顺手探测的常见模拟器端口（只 adb connect 本机，不点任何东西）
COMMON_PORTS = (5555, 5557, 7555, 16384, 16416, 62001, 21503)
MIN_W, MIN_H = 1280, 720
OLD_CRON = ("maapilot job tick", "bin/daily.sh")
DATA_STALE_DAYS = 30


# --- 外部访问 --------------------------------------------------------------------------

@dataclass
class Proc:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", "replace")

    @property
    def err(self) -> str:
        return self.stderr.decode("utf-8", "replace").strip()


class Probe:
    """doctor 的全部外部访问：子进程与程序查找。测试换成假的。"""

    def run(self, argv: list[str], timeout: float = 15, env: dict | None = None) -> Proc:
        try:
            p = subprocess.run(argv, capture_output=True, timeout=timeout, env=env)
        except FileNotFoundError as e:
            return Proc(127, b"", str(e).encode())
        except subprocess.TimeoutExpired:
            return Proc(124, b"", f"超时（{timeout}s）".encode())
        except OSError as e:
            return Proc(126, b"", str(e).encode())
        return Proc(p.returncode, p.stdout or b"", p.stderr or b"")

    def which(self, name: str) -> str | None:
        return shutil.which(name)


PROBE: Probe = Probe()


def _item(name: str, ok: bool, detail: str, fix: str | None = None, **extra) -> dict:
    return {"name": name, "ok": ok, "detail": detail, "fix": None if ok else fix, **extra}


# --- 各项检查 ---------------------------------------------------------------------------

class Ctx:
    def __init__(self, offline: bool, probe: Probe):
        self.offline = offline
        self.probe = probe
        self.cfg: dict | None = None
        self.adb: str | None = None          # adb 的可执行路径（adb 检查通过时）
        self.online = False                  # 模拟器连上了

    @property
    def address(self) -> str | None:
        return ((self.cfg or {}).get("emulator") or {}).get("address") or None


def check_config(ctx: Ctx) -> dict:
    from maapilot import config
    if not config.exists():
        return _item("config", False, "还没有 private/config.yaml", "做 setup（从 templates/config.yaml 复制并填好）")
    try:
        ctx.cfg = config.load()
    except CliError as e:
        return _item("config", False, e.message, "改正 private/config.yaml")
    if not ctx.address:
        return _item("config", False, "config.yaml 里 emulator.address 还没填", "做 setup：找出模拟器的 adb 地址写进去")
    return _item("config", True, f"模拟器 {ctx.address}，服务器 {ctx.cfg.get('server')}")


def check_adb(ctx: Ctx) -> dict:
    adb = str(((ctx.cfg or {}).get("emulator") or {}).get("adb") or "adb")
    path = ctx.probe.which(adb)
    if path is None:
        return _item("adb", False, f"找不到 adb（{adb}）",
                     "brew install --cask android-platform-tools，或在 emulator.adb 写 adb 的绝对路径")
    r = ctx.probe.run([path, "version"])
    if r.returncode != 0:
        return _item("adb", False, f"adb 跑不起来（退出码 {r.returncode}）", "重新安装 Android platform-tools")
    ctx.adb = path
    first = r.text.strip().splitlines()[0] if r.text.strip() else path
    return _item("adb", True, first, path=path)


def adb_devices(ctx: Ctx) -> list[str]:
    r = ctx.probe.run([ctx.adb, "devices"])
    out = []
    for line in r.text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def candidates(ctx: Ctx) -> list[str]:
    """在线的候选地址：`adb devices` 里的，加上常见端口探测连得上的。"""
    found = adb_devices(ctx)
    for port in COMMON_PORTS:
        addr = f"127.0.0.1:{port}"
        if addr in found:
            continue
        r = ctx.probe.run([ctx.adb, "connect", addr], timeout=4)
        if r.returncode == 0 and re.search(r"\bconnected to\b", r.text) and "cannot" not in r.text:
            found.append(addr)
    return list(dict.fromkeys(found))


def check_emulator(ctx: Ctx) -> dict:
    if ctx.adb is None:
        return _item("emulator", False, "adb 不可用，没法检查", "先解决 adb")
    addr = ctx.address
    if not addr:
        cands = candidates(ctx)
        return _item("emulator", False, "还没填模拟器地址" + (f"；在线的：{'、'.join(cands)}" if cands else "；没找到在线的模拟器"),
                     "打开模拟器、在模拟器设置里打开 ADB 调试，把地址填进 config.yaml 的 emulator.address",
                     candidates=cands)
    if re.match(r"^[^\s:]+:\d+$", addr):
        ctx.probe.run([ctx.adb, "connect", addr], timeout=8)
    r = ctx.probe.run([ctx.adb, "-s", addr, "get-state"], timeout=8)
    if r.returncode == 0 and r.text.strip() == "device":
        ctx.online = True
        return _item("emulator", True, f"{addr} 已连接")
    cands = [c for c in candidates(ctx) if c != addr]
    why = r.err.splitlines()[-1] if r.err else (r.text.strip() or "状态未知")
    return _item("emulator", False, f"{addr} 连不上：{why}",
                 "开模拟器、打开 ADB 调试；地址不对就改 config.yaml 的 emulator.address", candidates=cands)


def check_screen(ctx: Ctx) -> dict:
    from maapilot.crew.screen import check_aspect, png_size
    if not ctx.online:
        return _item("screen", False, "模拟器没连上，没法截图", "先解决 emulator")
    t0 = time.monotonic()
    r = ctx.probe.run([ctx.adb, "-s", ctx.address, "exec-out", "screencap", "-p"], timeout=30)
    took = time.monotonic() - t0
    try:
        if r.returncode != 0:
            raise ValueError(r.err or f"退出码 {r.returncode}")
        w, h = png_size(r.stdout)
    except ValueError as e:
        return _item("screen", False, f"截图失败：{e}", "重启模拟器后再试")
    if h > w:
        w, h = h, w
    try:
        check_aspect(w, h)
    except RuntimeError:
        return _item("screen", False, f"分辨率 {w}×{h} 不是 16:9",
                     "在模拟器设置里把分辨率改成 16:9，如 1280×720 或 1920×1080", resolution=[w, h])
    if w < MIN_W or h < MIN_H:
        return _item("screen", False, f"分辨率 {w}×{h} 太小", "至少 1280×720（16:9）", resolution=[w, h])
    return _item("screen", True, f"{w}×{h}，截图 {took:.1f} 秒", resolution=[w, h])


def check_input(ctx: Ctx) -> dict:
    """只看模拟器里有没有 input 命令（点击靠它或 MaaTouch），不真的点。"""
    if not ctx.online:
        return _item("input", False, "模拟器没连上", "先解决 emulator")
    r = ctx.probe.run([ctx.adb, "-s", ctx.address, "shell", "ls", "/system/bin/input"], timeout=8)
    if r.returncode == 0 and "input" in r.text:
        return _item("input", True, "能发点击（adb input / MaaTouch）")
    return _item("input", False, "模拟器里没有 input 命令", "换一个模拟器或重装模拟器")


def check_game(ctx: Ctx) -> dict:
    from maapilot import config
    pkg = config.package(ctx.cfg)
    if not ctx.online:
        return _item("game", False, "模拟器没连上", "先解决 emulator", required=False)
    r = ctx.probe.run([ctx.adb, "-s", ctx.address, "shell", "dumpsys", "window"], timeout=10)
    focus = [l.strip() for l in r.text.splitlines() if "mCurrentFocus" in l or "mFocusedApp" in l]
    fg = None                                            # 前台应用的包名（setup 据此推服务器）
    for l in focus:
        if m := re.search(r"([A-Za-z][\w.]+)/[\w.$]+", l):
            fg = m.group(1)
            break
    inst = installed_games(ctx)
    if any(re.search(rf"\b{re.escape(pkg)}/", l) for l in focus):
        return _item("game", True, "游戏在前台", required=False, package=fg, installed=inst)
    other = next((srv for srv, p in config.PACKAGES.items() if p.lower() == (fg or "").lower()), None)
    if other:
        return _item("game", False, f"前台是明日方舟（{other}，{fg}），和配置的服务器 {config.server(ctx.cfg)} 不一样",
                     f"把 config.yaml 的 server 改成 {other}", required=False, package=fg, installed=inst)
    if inst and not any(g["package"].lower() == pkg.lower() for g in inst):
        names = "、".join(f"{g['server']}（{g['package']}）" for g in inst)
        return _item("game", False, f"模拟器里装的明日方舟是 {names}，和配置的服务器 {config.server(ctx.cfg)} 不一样",
                     "把 config.yaml 的 server 改成装着的那个", required=False, package=fg, installed=inst)
    if not inst and inst is not None:
        return _item("game", False, "模拟器里没装明日方舟", "在模拟器的应用商店里装好明日方舟，并登录一次账号",
                     required=False, package=fg, installed=inst)
    return _item("game", False, "游戏不在前台" + (f"（前台是 {fg}）" if fg else ""),
                 "maapilot maa startup（启动游戏、回首页）", required=False, package=fg, installed=inst)


def installed_games(ctx: Ctx) -> list[dict] | None:
    """模拟器里装着的明日方舟客户端 [{package, server}]（`pm list packages`，只读）；查不了 → None。"""
    from maapilot import config
    r = ctx.probe.run([ctx.adb, "-s", ctx.address, "shell", "pm", "list", "packages"], timeout=15)
    if r.returncode != 0:
        return None
    have = {l.strip().removeprefix("package:").strip().lower(): l.strip().removeprefix("package:").strip()
            for l in r.text.splitlines() if l.strip()}
    return [{"package": have[p.lower()], "server": srv} for srv, p in config.PACKAGES.items() if p.lower() in have]


def maa_version(ctx: Ctx) -> tuple[str | None, str | None]:
    from maapilot import config
    r = ctx.probe.run([config.tool("maa", ctx.cfg), "version"])
    cli = core = None
    for line in r.text.splitlines():
        if m := re.search(r"maa-?cli\s+v?(\S+)", line, re.I):
            cli = m.group(1)
        elif m := re.search(r"maa-?core\s+v?(\S+)", line, re.I):
            core = m.group(1)
    return cli, core


def maa_dir(ctx: Ctx, which: str) -> Path | None:
    from maapilot import config
    r = ctx.probe.run([config.tool("maa", ctx.cfg), "dir", which])
    lines = [l.strip() for l in r.text.splitlines() if l.strip()]
    return Path(lines[-1]) if r.returncode == 0 and lines else None


def check_maa_cli(ctx: Ctx) -> dict:
    from maapilot import config
    maa = config.tool("maa", ctx.cfg)
    path = ctx.probe.which(maa)
    if path is None:
        return _item("maa_cli", False, f"找不到 maa-cli（{maa}）", "brew install MaaAssistantArknights/tap/maa-cli")
    cli, core = maa_version(ctx)
    if not cli:
        return _item("maa_cli", False, "maa-cli 跑不起来", "重新安装 maa-cli")
    if not core:
        return _item("maa_cli", False, f"maa-cli {cli}，但没装 MaaCore", "maa install", version=cli)
    res = maa_dir(ctx, "resource")
    if res is None or not (res / "PaddleOCR").exists():
        return _item("maa_cli", False, f"maa-cli {cli}、MaaCore {core}，但资源不全", "maa update",
                     version=cli, core=core)
    return _item("maa_cli", True, f"maa-cli {cli}、MaaCore {core}", version=cli, core=core, resource=str(res),
                 path=path)


def check_claude(ctx: Ctx) -> dict:
    """定时日常要叫无头 claude：找得到它、给出绝对路径（setup 写进 tools.claude）。只是提示，不影响 ok_all。"""
    from maapilot import config
    claude = config.tool("claude", ctx.cfg)
    path = ctx.probe.which(claude)
    if path is None:
        return _item("claude", False, f"找不到 claude（{claude}）", "在 config.yaml 的 tools.claude 写 claude 的绝对路径",
                     required=False)
    trusted = claude_trusted(paths.ROOT)
    if trusted is False:
        return _item("claude", False, f"{path}；这个文件夹还没被 Claude Code 信任，定时日常叫起的 agent 用不了放行的命令",
                     "在这个文件夹里交互地启动一次 claude，接受\"信任这个文件夹\"的提示", required=False, path=path,
                     trusted=False)
    return _item("claude", True, path, required=False, path=path, trusted=trusted)


def claude_trusted(root: Path) -> bool | None:
    """Claude Code 是否信任过这个文件夹（~/.claude.json 的 projects.<路径>.hasTrustDialogAccepted；上级目录信任也算）。
    没信任时无头 `claude -p` 会忽略项目的放行规则。读不到 → None。只读。"""
    import json
    import os
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    f = Path(cfg_dir) / ".claude.json" if cfg_dir else Path.home() / ".claude.json"
    try:
        projects = json.loads(f.read_text(encoding="utf-8")).get("projects") or {}
    except (OSError, ValueError, AttributeError):
        return None
    p = Path(root).resolve()
    for d in (p, *p.parents):
        if (projects.get(str(d)) or {}).get("hasTrustDialogAccepted"):
            return True
    return False


def check_maa_config(ctx: Ctx) -> dict:
    from maapilot.crew import maa
    missing = maa.config_missing()
    if missing:
        return _item("maa_config", False, f"maa-cli 配置目录缺 {'、'.join(missing)}", "maapilot doctor --fix",
                     dir=str(maa.config_dir()))
    return _item("maa_config", True, str(maa.config_dir()))


def check_ocr_model(ctx: Ctx) -> dict:
    missing = [f for f in OCR_FILES if not (paths.OCR_MODEL / f).exists()]
    if missing:
        return _item("ocr_model", False, f"OCR 模型缺 {'、'.join(missing)}", "maapilot doctor --fix（从 maa-cli 资源复制）")
    return _item("ocr_model", True, "齐全")


def check_game_data(ctx: Ctx) -> dict:
    from maapilot.knowledge import datasync
    missing = datasync.missing_tables()
    if missing:
        return _item("game_data", False, f"数据表缺 {len(missing)} 张", "maapilot doctor --fix 或 maapilot data sync")
    stamps = [common.parse_iso(v.get("fetched_at")) for v in datasync.read_manifest().values() if isinstance(v, dict)]
    stamps = [s for s in stamps if s]
    if stamps:
        days = (common.now() - max(stamps)).days
        if days > DATA_STALE_DAYS:
            return _item("game_data", False, f"{days} 天没同步了", "maapilot data sync", age_days=days)
        return _item("game_data", True, f"齐全，{days} 天前同步", age_days=days)
    return _item("game_data", True, "齐全")


def crontab_lines(probe: Probe) -> list[str] | None:
    r = probe.run(["crontab", "-l"], timeout=5)
    if r.returncode != 0:
        return [] if "no crontab" in r.err.lower() else None
    return [l for l in r.text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def schedule_info(probe: Probe) -> dict:
    lines = crontab_lines(probe)
    if lines is None:
        return {"installed": False, "times": [], "old_entries": [], "readable": False}
    root = str(paths.ROOT)
    ours = [l for l in lines if root in l and "daily run" in l]
    times = []
    for l in ours:
        f = l.split()
        if len(f) >= 2 and f[0].isdigit():
            times += [f"{int(h):02d}:{int(f[0]):02d}" for h in f[1].split(",") if h.isdigit()]
    old = [l for l in lines if any(k in l for k in OLD_CRON) and l not in ours]
    return {"installed": bool(ours), "times": sorted(times), "old_entries": old, "readable": True}


def check_schedule(ctx: Ctx) -> dict:
    info = schedule_info(ctx.probe)
    extra = {"old_entries": info["old_entries"]} if info["old_entries"] else {}
    if not info["installed"]:
        detail = "还没设定时日常" + ("；有旧版 MaaPilot 的定时任务" if info["old_entries"] else "")
        return _item("schedule", False, detail, "maapilot daily schedule", required=False, **extra)
    return _item("schedule", True, f"定时日常：{'、'.join(info['times'])}" +
                 ("；还有旧版 MaaPilot 的定时任务" if info["old_entries"] else ""), required=False, **extra)


def check_setup(ctx: Ctx) -> dict:
    if paths.SETUP.exists():
        return _item("setup", True, "setup 已完成", required=False)
    return _item("setup", False, "还没做 setup", "按 maapilot-setup 做", required=False)


DEVICE_CHECKS = ("emulator", "screen", "input", "game")
CHECKS = [("config", check_config), ("adb", check_adb), ("emulator", check_emulator), ("screen", check_screen),
          ("input", check_input), ("maa_cli", check_maa_cli), ("claude", check_claude), ("maa_config", check_maa_config),
          ("ocr_model", check_ocr_model), ("game_data", check_game_data), ("game", check_game),
          ("schedule", check_schedule), ("setup", check_setup)]


def run_checks(offline: bool = False, probe: Probe | None = None) -> list[dict]:
    ctx = Ctx(offline, probe or PROBE)
    out = []
    for name, fn in CHECKS:
        if offline and name in DEVICE_CHECKS:
            continue
        try:
            out.append(fn(ctx))
        except Exception as e:
            out.append(_item(name, False, f"检查出错：{type(e).__name__}: {e}", "这是 MaaPilot 的问题，带上这段输出反馈"))
    return out


# --- --fix ---------------------------------------------------------------------------------

def fix(probe: Probe | None = None) -> list[dict]:
    """不需要问用户的本地修复：maa-cli 配置、OCR 模型、数据表。"""
    from maapilot import config
    from maapilot.crew import maa
    probe = probe or PROBE
    done = []
    copied = maa.install_config()
    done.append({"name": "maa_config", "done": copied, "detail": f"复制了 {'、'.join(copied)}" if copied else "已经有了"})
    missing = [f for f in OCR_FILES if not (paths.OCR_MODEL / f).exists()]
    if missing:
        ctx = Ctx(False, probe)
        ctx.cfg = config.load_or_defaults()
        res = maa_dir(ctx, "resource")
        src = None
        if res is not None:
            srv = config.server(ctx.cfg)
            for d in (res / "global" / srv / "resource" / "PaddleOCR", res / "PaddleOCR"):
                if (d / "rec" / "inference.onnx").exists():
                    src = d
                    break
        if src is None:
            done.append({"name": "ocr_model", "done": [], "detail": "maa-cli 资源里没找到 PaddleOCR（先 maa install）"})
        else:
            paths.OCR_MODEL.mkdir(parents=True, exist_ok=True)
            pairs = {"det.onnx": src / "det" / "inference.onnx", "rec.onnx": src / "rec" / "inference.onnx",
                     "keys.txt": src / "rec" / "keys.txt"}
            copied = []
            for dest, s in pairs.items():
                if s.exists():
                    shutil.copyfile(s, paths.OCR_MODEL / dest)
                    copied.append(dest)
            done.append({"name": "ocr_model", "done": copied, "detail": f"从 {src} 复制"})
    from maapilot.knowledge import datasync
    if datasync.missing_tables():
        try:
            r = datasync.sync(server=config.penguin_server())
            done.append({"name": "game_data", "done": r.get("updated", []), "detail": "同步了数据表"})
        except Exception as e:
            done.append({"name": "game_data", "done": [], "detail": f"同步失败：{type(e).__name__}: {e}"})
    return done


# --- --brief ---------------------------------------------------------------------------------

def goals_active() -> int | None:
    try:
        from maapilot.flows.goals import active_count
        return active_count()
    except Exception:
        pass
    try:
        text = paths.GOALS.read_text(encoding="utf-8")
    except OSError:
        return 0
    return len(re.findall(r"^[ \t]*[-*][ \t]*状态[ \t]*[：:][ \t]*进行中[ \t]*$", text, re.M))


def last_daily() -> dict | None:
    try:
        reports = sorted(r for r in paths.REPORTS.glob("*.json")       # 只看运行的汇报（seen.json 不是）
                         if re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}", r.stem))
    except OSError:
        return None
    if not reports:
        return None
    r = reports[-1]
    d = common.read_json(r) or {}
    md = r.with_suffix(".md")
    return {"at": d.get("finished_at") or d.get("at") or d.get("started_at"), "ok": d.get("ok"),
            "report": common.rel(md if md.exists() else r)}


def brief(probe: Probe | None = None) -> dict:
    from maapilot.tools import guard
    setup_done = paths.SETUP.exists()
    stop = guard.stopped()
    sched = schedule_info(probe or PROBE)
    data = {"setup_done": setup_done, "goals_active": goals_active(), "guard_stop": stop,
            "last_daily": last_daily(), "schedule": {"installed": sched["installed"], "times": sched["times"]},
            "next": "setup" if not setup_done else ("guard" if stop else None)}
    try:
        from maapilot.flows import report
        data["unseen_problems"] = [{k: p.get(k) for k in ("run_id", "started_at", "status", "lines", "report")}
                                   for p in report.unseen_problems()]
    except Exception:                                   # noqa: BLE001 - 汇报读不了不影响启动检查
        data["unseen_problems"] = []
    data["goals"] = goals_brief()
    data["news"] = news_brief()
    data["lines"] = brief_lines(data)
    return data


def goals_brief() -> list[dict] | None:
    """进行中的目标（标题、对象、期限）：每次会话一开始 agent 就知道有哪些目标，做别的事时别挪用它们要的材料。"""
    try:
        from maapilot.flows.goals import active_goals
        return [{k: g.get(k) for k in ("title", "object", "deadline", "days_left")} for g in active_goals()]
    except Exception:                                   # noqa: BLE001 - 目标读不了不影响启动检查（goals_active 那行还在）
        return None


def news_brief() -> dict | None:
    """缓存里的官方日程（不联网；只有官服）：正在维护、3 天内的维护和活动、到点还没告诉用户的。"""
    try:
        from maapilot import config
        from maapilot.knowledge import news
        if config.server(config.load_or_defaults()) != "Official":
            return None
        d = news.load()
        if not d.get("items"):
            return None
        return {"at": d.get("at"), "lines": news.lines(d), "due": [e["key"] for e in news.due(d)]}
    except Exception:                                   # noqa: BLE001 - 日程读不了不影响启动检查
        return None


def brief_lines(d: dict) -> list[str]:
    if not d["setup_done"]:
        return ["MaaPilot 还没设置：用户一开口（说什么都行）就进入 maapilot-setup（先跟用户打招呼，说明要做几步）。"]
    lines = []
    if d["guard_stop"]:
        u = d["guard_stop"].get("unexplained") or {}
        what = "、".join(f"{'源石' if k == 'originite' else '合成玉'}少了 {-v}" for k, v in u.items())
        lines.append(f"对账停机中：{what}，没经用户同意。先告诉用户、问清楚。")
    goals = d.get("goals")
    if goals:
        lines.append(f"进行中的目标 {len(goals)} 个（做别的事前想想会不会用掉它们要的材料）：" + "；".join(
            f"{g['title']}（{g.get('object') or '?'}" + (f"，还剩 {g['days_left']} 天" if g.get("days_left") is not None else "")
            + "）" for g in goals))
    else:
        lines.append(f"进行中的目标：{d['goals_active'] or 0} 个。")
    ld = d["last_daily"]
    if ld:
        lines.append(f"上次日常：{ld.get('at') or '时间不明'}，{'成功' if ld.get('ok') else '有失败'}。")
    else:
        lines.append("还没跑过日常。")
    s = d["schedule"]
    lines.append(f"定时日常：{'、'.join(s['times'])}。" if s["installed"] else "定时日常没设。")
    n = d.get("news") or {}
    for l in n.get("lines") or []:
        lines.append(f"官方日程：{l}")
    if n.get("due"):
        lines.append("维护结束 / 活动开始了、还没跟用户说（maapilot-events）：" + "、".join(n["due"]))
    for p in d.get("unseen_problems") or []:
        lines.append("还没告诉用户的问题：" + " ".join(p.get("lines") or [p.get("run_id") or "?"])
                     + f"（运行 {p.get('run_id')}；告诉用户后 report seen {p.get('run_id')}）")
    return lines


# --- 命令 ------------------------------------------------------------------------------------

SETUP_GREETING = "MaaPilot 还没设置好。随便说一句（比如\"开始\"），我就带你一步步设置。"
GUARD_NOTICE = "MaaPilot：合成玉或源石有一笔没经你同意的减少，日常先停下了。说一句话，我告诉你详情。"


def hook_output(data: dict) -> dict:
    """给 SessionStart 钩子：Claude Code 只把 hookSpecificOutput.additionalContext 放进会话（直接输出的 JSON 会被当成钩子的控制字段）。
    钩子不会让 agent 自己先开口：要用户先说一句。systemMessage 是直接显示给用户的那一行，告诉用户说一句就开始。"""
    import json
    env = {"ok": True, "command": "doctor --brief", "data": data, "error": None}
    text = "MaaPilot 启动检查（maapilot doctor --brief 的输出）：\n" + json.dumps(env, ensure_ascii=False, indent=1, default=str)
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    msg = {"setup": SETUP_GREETING, "guard": GUARD_NOTICE}.get(data.get("next"))
    if msg:
        out["systemMessage"] = msg
    return out


def cmd(args):
    if args.hook:
        data = brief()
        raise cliutil.RawOutput(hook_output(data))
    if args.brief:
        return brief()
    fixed = fix() if args.fix else None
    checks = run_checks(offline=args.offline)
    ok_all = all(c["ok"] for c in checks if c.get("required", True))
    data = {"ok_all": ok_all, "setup_done": paths.SETUP.exists(), "offline": bool(args.offline), "checks": checks}
    if fixed is not None:
        data["fixed"] = fixed
    if not ok_all:
        bad = [c["name"] for c in checks if not c["ok"] and c.get("required", True)]
        raise CliError("checks_failed", f"没通过：{'、'.join(bad)}", exit=ENVIRONMENT, data=data,
                       hint="按每项的 fix 处理；能自动修的先 maapilot doctor --fix")
    return data


def register(sub) -> None:
    p = sub.add_parser("doctor", help="检查环境并给修复建议")
    p.add_argument("--brief", action="store_true", help="给启动钩子：只看 setup、目标、对账、上次日常（不碰模拟器）")
    p.add_argument("--offline", action="store_true", help="不碰模拟器、不联网")
    p.add_argument("--fix", action="store_true", help="顺手做不用问用户的本地修复")
    p.add_argument("--hook", action="store_true", help="给启动钩子：同 --brief，但按 Claude Code 钩子的格式输出（不是信封）")
    p.set_defaults(func=cmd, command="doctor")
