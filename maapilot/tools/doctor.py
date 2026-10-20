"""`maapilot doctor [--brief] [--offline] [--fix]`：逐项检查环境，给修复建议。契约见 docs/cli.md「doctor」。

只读：只调 adb 的查询命令（version / devices / connect / get-state / exec-out screencap / shell）、maa-cli 的
version / dir 和 `crontab -l`（Windows：`schtasks /Query`，看 v0.1 留下的系统定时）；不点击、不拿锁、不 import MaaFramework。
所有外部访问都经 `Probe`，测试换成假的。
每项 `{name, ok, detail, fix}`；`required: false` 的项（游戏在不在前台、旧的定时、setup）不影响 ok_all。

`--brief` 给启动钩子：不碰模拟器、不联网、不跑子进程以外的慢操作，只读 private/ 下的几个文件和旧的系统定时（crontab / 任务计划程序）。
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
from maapilot.oscompat import fs, hints
from maapilot.tools import common

OCR_FILES = ("det.onnx", "rec.onnx", "keys.txt")
# 地址没填时顺手探测的常见模拟器端口（各系统不同，见 oscompat.hints；只 adb connect 本机，不点任何东西）
COMMON_PORTS = hints.common_ports()
MIN_W, MIN_H = 1280, 720
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
    from maapilot import config
    adb = config.adb_path(ctx.cfg or {})
    path = ctx.probe.which(adb)
    if path is None:
        return _item("adb", False, f"找不到 adb（{adb}）",
                     f"{hints.install_hint('adb')}，或在 emulator.adb 写 adb 的绝对路径")
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
    if r.returncode == 0 and r.text.strip() and not cli:     # 跑起来了、有输出，却认不出版本号：输出格式变了
        from maapilot.knowledge import sources
        sources.bad("maa_cli", sources.UpstreamFormat("maa_cli", "maa version 输出里的 maa-cli 版本号",
                                                      effect="doctor 认不出 maa-cli 装好了没有"))
    return cli, core


def maa_dir(ctx: Ctx, which: str) -> Path | None:
    from maapilot import config
    r = ctx.probe.run([config.tool("maa", ctx.cfg), "dir", which])
    lines = [l.strip() for l in r.text.splitlines() if l.strip()]
    return Path(lines[-1]) if r.returncode == 0 and lines else None


def _ver(v: str) -> tuple[int, ...]:
    """"0.7.7" / "v0.8.0-nightly.1+sha.x" → (0, 7, 7) / (0, 8, 0)。"""
    m = re.match(r"v?(\d+(?:\.\d+)*)", str(v).strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def maa_cli_range(cli: str) -> str | None:
    """不在实测范围：低于下限 → "old"，高于上限 → "new"；在范围里或认不出 → None。"""
    from maapilot.toolchain import MAA_CLI_TESTED
    v, lo, hi = _ver(cli), _ver(MAA_CLI_TESTED[0]), _ver(MAA_CLI_TESTED[1])
    if not v:
        return None
    return "old" if v < lo else "new" if v > hi else None


def check_maa_cli(ctx: Ctx) -> dict:
    from maapilot import config
    maa = config.tool("maa", ctx.cfg)
    path = ctx.probe.which(maa)
    if path is None:
        return _item("maa_cli", False, f"找不到 maa-cli（{maa}）", hints.install_hint("maa"))
    cli, core = maa_version(ctx)
    if not cli:
        return _item("maa_cli", False, "maa-cli 跑不起来", "重新安装 maa-cli")
    if not core:
        return _item("maa_cli", False, f"maa-cli {cli}，但没装 MaaCore", "maa install", version=cli)
    res = maa_dir(ctx, "resource")
    if res is None or not (res / "PaddleOCR").exists():
        return _item("maa_cli", False, f"maa-cli {cli}、MaaCore {core}，但资源不全", "maa update",
                     version=cli, core=core)
    from maapilot.toolchain import MAA_CLI_TESTED
    lo, hi = MAA_CLI_TESTED
    rng = maa_cli_range(cli)
    if rng == "old":
        return _item("maa_cli", False, f"maa-cli {cli} 太旧（实测过的是 {lo}–{hi}）", hints.install_hint("maa_upgrade"),
                     version=cli, core=core, tested=list(MAA_CLI_TESTED))
    extra = {"warnings": [f"maa-cli {cli} 比实测过的 {hi} 新：出了怪问题先告诉开发者"]} if rng == "new" else {}
    return _item("maa_cli", True, f"maa-cli {cli}、MaaCore {core}", version=cli, core=core, resource=str(res),
                 path=path, tested=list(MAA_CLI_TESTED), **extra)


def check_agent_config(ctx: Ctx) -> dict:
    """config.yaml 里还写着 v0.1 的 `agent`（定时日常叫哪个无头 agent）：已经不用了，写了也忽略，提示可以删。"""
    from maapilot import config
    raw = config.raw()
    if raw is None:
        return _item("agent_config", True, "没有旧的设置", required=False)
    old = [k for k in config.LEGACY_KEYS if k in raw] + [f"daily.{k}" for k in config.LEGACY_DAILY_KEYS
                                                          if k in (raw.get("daily") or {})]
    if old:
        return _item("agent_config", False, f"config.yaml 里的 {'、'.join(old)} 是旧版（定时叫起无头 agent）用的，现在不用了",
                     "可以从 config.yaml 里删掉（用户同意后），不删也不影响", required=False, legacy=old)
    return _item("agent_config", True, "没有旧的设置", required=False)


def codex_trusted(root: Path) -> bool | None:
    """Codex 是否信任这个文件夹（$CODEX_HOME/config.toml 的 [projects."<路径>"] trust_level = "trusted"；上级目录也算）。
    读不到 → None。只读。"""
    from maapilot.tools import host_cmd
    return host_cmd.codex_trusted(root)


def claude_trusted(root: Path) -> bool | None:
    """Claude Code 是否信任过这个文件夹（~/.claude.json 的 projects.<路径>.hasTrustDialogAccepted；上级目录信任也算）。
    读不到 → None。只读。"""
    import json
    import os
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    f = Path(cfg_dir) / ".claude.json" if cfg_dir else Path.home() / ".claude.json"
    try:
        projects = json.loads(f.read_text(encoding="utf-8")).get("projects") or {}
    except (OSError, ValueError, AttributeError):
        return None
    p = Path(root).resolve()
    trusted = [k for k, v in projects.items() if isinstance(v, dict) and v.get("hasTrustDialogAccepted")]
    for d in (p, *p.parents):              # 键的写法（大小写、分隔符）各系统不同，按路径比较
        if any(fs.same_path(k, d) for k in trusted):
            return True
    return False


def check_maa_config(ctx: Ctx) -> dict:
    from maapilot.crew import maa
    missing = maa.config_missing()
    if missing:
        return _item("maa_config", False, f"maa-cli 配置目录缺 {'、'.join(missing)}", "maapilot doctor --fix",
                     dir=str(maa.config_dir()))
    return _item("maa_config", True, str(maa.config_dir()))


def check_maa_core(ctx: Ctx) -> dict:
    """单独装的 MaaCore（`op progress` 用；maa-cli 还跑不了干员培养）。没有不影响别的命令。"""
    from maapilot.crew import maacore
    from maapilot.flows.op import PROGRESS_MIN_CORE
    found = maacore.find()
    if found is None:
        return _item("maa_core", False, "没单独装 MaaCore（干员培养 op progress 要用）", "maapilot doctor --fix（从 MAA 的 GitHub 下载）",
                     required=False)
    if not maacore.at_least(found[0], PROGRESS_MIN_CORE):
        return _item("maa_core", False, f"单独装的 MaaCore 是 {found[0]}，干员培养要 {PROGRESS_MIN_CORE} 以上",
                     "maapilot doctor --fix", required=False)
    return _item("maa_core", True, f"{found[0]}（{found[1]}）", required=False)


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


def legacy_schedule(probe: Probe | None = None) -> dict:
    """v0.1 留下的系统定时（flows/daily/cron.py 的 legacy_schedule，经 Probe 跑 crontab / schtasks）：
    {installed, times, entries, old_entries, backend}，可能带 error（读不了）、unavailable（这台电脑上没有调度）。"""
    from maapilot.flows.daily import cron
    probe = probe or PROBE

    def run(argv, input, timeout):
        r = probe.run(list(argv), timeout=min(timeout, 10))
        return r.returncode, r.stdout, r.stderr
    return cron.legacy_schedule(run)


def check_legacy_schedule(ctx: Ctx) -> dict:
    """系统里还留着 v0.1 的定时日常（crontab 行 / 任务计划程序的任务）：现在定时由 harness 在会话里叫醒 agent，
    旧的留着会到点自己跑 daily run，要删掉。"""
    info = legacy_schedule(ctx.probe)
    extra = {"old_entries": info["old_entries"]} if info["old_entries"] else {}
    if info.get("unavailable"):
        return _item("legacy_schedule", True, "这台电脑上没有系统定时", required=False)
    if info["installed"] is None:
        return _item("legacy_schedule", False, f"看不了有没有旧的定时：{info.get('error') or '读不了'}",
                     "打开「任务计划程序」看看 MaaPilot 文件夹" if info.get("backend") == "schtasks"
                     else "看看 crontab 能不能用（crontab -l）", required=False)
    if info["installed"]:
        return _item("legacy_schedule", False, f"系统里还留着旧版的定时日常（{'、'.join(info['times']) or '时间不明'}），"
                     "到点会自己跑", "用户同意后 maapilot daily unschedule，再用 harness 的定时在会话里定好", required=False,
                     times=info["times"], entries=info["entries"], **extra)
    return _item("legacy_schedule", True, "没有旧版的定时日常" + ("；还有更旧的 MaaPilot 的定时（没动）" if extra else ""),
                 required=False, **extra)


def skills_link(root: Path | None = None) -> str:
    """.claude/skills（Claude Code 找 skill 的地方）指向 .agents/skills 没有："ok" / "file"（Windows 上 git 没开符号链接，
    检出成一个写着路径的文本文件）/ "missing" / "broken"（链接指向的地方不在）。"""
    link = Path(root or paths.ROOT) / ".claude" / "skills"
    if link.is_dir():
        return "ok"
    if fs.is_link(link):                               # 包括 Windows 的目录联接
        return "broken"
    return "file" if link.is_file() else "missing"


def fix_skills_link(root: Path | None = None) -> str:
    """把 .claude/skills 换成指向 .agents/skills 的目录链接（Windows 上是目录联接，不要管理员权限）。→ 做法。"""
    root = Path(root or paths.ROOT)
    return fs.make_dir_link(root / ".claude" / "skills", root / ".agents" / "skills")


def check_skills_link(ctx: Ctx) -> dict:
    state = skills_link()
    if state == "ok":
        return _item("skills_link", True, ".claude/skills → .agents/skills", required=False)
    what = {"file": "是个普通文件（git 检出时没建符号链接）", "missing": "不存在", "broken": "指向的目录不在"}[state]
    return _item("skills_link", False, f".claude/skills {what}，Claude Code 找不到 MaaPilot 的 skill", "maapilot doctor --fix",
                 required=False, state=state)


def check_setup(ctx: Ctx) -> dict:
    if paths.SETUP.exists():
        return _item("setup", True, "setup 已完成", required=False)
    return _item("setup", False, "还没做 setup", "按 maapilot-setup 做", required=False)


DEVICE_CHECKS = ("emulator", "screen", "input", "game")
CHECKS = [("config", check_config), ("adb", check_adb), ("emulator", check_emulator), ("screen", check_screen),
          ("input", check_input), ("maa_cli", check_maa_cli), ("agent_config", check_agent_config), ("maa_config", check_maa_config),
          ("maa_core", check_maa_core), ("ocr_model", check_ocr_model), ("game_data", check_game_data), ("game", check_game),
          ("legacy_schedule", check_legacy_schedule), ("skills_link", check_skills_link), ("setup", check_setup)]


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
    """不需要问用户的本地修复：.claude/skills 链接、maa-cli 配置、单独的 MaaCore、OCR 模型、数据表。"""
    from maapilot import config
    from maapilot.crew import maa
    probe = probe or PROBE
    done = []
    if skills_link() != "ok" and (paths.ROOT / ".agents" / "skills").is_dir():
        try:
            how = fix_skills_link()
            done.append({"name": "skills_link", "done": [how], "detail": ".claude/skills → .agents/skills"})
        except OSError as e:
            done.append({"name": "skills_link", "done": [], "detail": f"建不了链接：{type(e).__name__}: {e}"})
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
    from maapilot.crew import maacore
    from maapilot.flows.op import PROGRESS_MIN_CORE
    found = maacore.find()
    if found is None or not maacore.at_least(found[0], PROGRESS_MIN_CORE):
        try:
            r = maacore.install()
            done.append({"name": "maa_core", "done": [r["version"]] if r["installed"] else [], "detail": r["dir"]})
        except Exception as e:
            done.append({"name": "maa_core", "done": [], "detail": f"下载失败：{type(e).__name__}: {e}"})
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
        text = fs.read_text_utf8(paths.GOALS)
    except OSError:
        return 0
    return len(re.findall(r"^[ \t]*[-*][ \t]*状态[ \t]*[：:][ \t]*进行中[ \t]*$", text, re.M))


def last_daily() -> dict | None:
    try:
        reports = sorted(r for r in paths.REPORTS.glob("*.json")       # 只看运行的汇报（<YYYY-MM-DD-HHMM>.json）
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
    data = {"setup_done": setup_done, "goals_active": goals_active(), "guard_stop": stop,
            "last_daily": last_daily(), "daily_open": daily_open_brief(),
            "legacy_schedule": legacy_schedule_brief(probe or PROBE),
            "next": "setup" if not setup_done else ("guard" if stop else None)}
    from maapilot import version
    data["version"] = version.info()
    data["memory"] = memory_brief() if setup_done else None
    data["goals"] = goals_brief()
    data["news"] = news_brief()
    data["sources"] = sources_brief()
    data["live_suspects"] = live_suspects_brief()
    data["lines"] = brief_lines(data)
    return data


def daily_open_brief() -> dict | None:
    """没收尾的日常：{run_id, trigger, started_at, age_h, stale}（在跑时另有 running、step）；没有 → None。"""
    try:
        from maapilot.flows import daily
        st = daily.status()
        op = st.get("open")
        if op:
            op["running"] = bool(st.get("running"))
        return op
    except Exception:                                   # noqa: BLE001 - 读不了不影响启动检查
        return None


def legacy_schedule_brief(probe: Probe) -> dict | None:
    """还留着 v0.1 的系统定时：{times, backend}；没有（或看不了）→ None。"""
    try:
        info = legacy_schedule(probe)
    except Exception:                                   # noqa: BLE001
        return None
    return {"times": info["times"], "backend": info["backend"]} if info.get("installed") else None


def memory_brief() -> dict | None:
    """用户偏好记录（private/memory/MEMORY.md 的目录行）。第一次会把 Claude Code 自动记忆里的搬过来（imported）。"""
    try:
        from maapilot.tools import memory
        imported = memory.import_claude()
        return {"dir": "private/memory", "index": memory.index_lines(), "imported": imported or None}
    except Exception:                                   # noqa: BLE001 - 读不了不影响启动检查
        return None


def live_suspects_brief() -> list[dict]:
    """现场读到的练度，一天后的快照还没追上（可能读错了，已经不再采用）。"""
    try:
        from maapilot.tools import live_box
        return live_box.suspects()
    except Exception:                                   # noqa: BLE001 - 读不了不影响启动检查
        return []


def sources_brief() -> list[dict]:
    """格式出了问题的外部数据源（private/state/sources.json；没问题的不列）：[{source, name, at, message, effect}]。"""
    try:
        from maapilot.knowledge import sources
        return [{k: s.get(k) for k in ("source", "name", "at", "message", "effect")} for s in sources.unhealthy()]
    except Exception:                                   # noqa: BLE001 - 读不了不影响启动检查
        return []


def goals_brief() -> list[dict] | None:
    """进行中的目标（标题、对象、期限）：每次会话一开始 agent 就知道有哪些目标，做别的事时别挪用它们要的材料。"""
    try:
        from maapilot.flows.goals import active_goals
        return [{k: g.get(k) for k in ("title", "object", "deadline", "days_left")} for g in active_goals()]
    except Exception:                                   # noqa: BLE001 - 目标读不了不影响启动检查（goals_active 那行还在）
        return None


def news_brief() -> dict | None:
    """缓存里的官方日程（不联网；只有国服：官服和 B 服共用一个日程）：正在维护、3 天内的维护和活动、到点还没告诉用户的。"""
    try:
        from maapilot import config
        from maapilot.knowledge import news
        if config.server(config.load_or_defaults()) not in news.CN_SERVERS:
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
    o = d.get("daily_open")
    if o:
        when = str(o.get("started_at") or "")[5:16].replace("T", " ")
        if o.get("running"):
            lines.append(f"日常 {o['run_id']}（{when} 开始）正在跑" + (f"（{o['step']}）" if o.get("step") else "") + "。")
        elif o.get("stale"):
            lines.append(f"日常 {o['run_id']}（{when} 开始）超过 6 小时没收尾：下次 daily run 会自动收尾；"
                         "要补上就先 maapilot daily done --summary … 收尾，再告诉用户。")
        else:
            lines.append(f"日常 {o['run_id']}（{when} 开始）还没收尾：做完目标部分、领奖励、对账后 maapilot daily done 收尾。")
    ls = d.get("legacy_schedule")
    if ls:
        lines.append(f"系统里还留着旧版的定时日常（{'、'.join(ls.get('times') or []) or '时间不明'}），到点会自己跑："
                     "告诉用户，同意后 maapilot daily unschedule 删掉，再用 harness 的定时在会话里定好（maapilot-setup）。")
    n = d.get("news") or {}
    for l in n.get("lines") or []:
        lines.append(f"官方日程：{l}")
    if n.get("due"):
        lines.append("官方日程有消息还没跟用户说（新公告 / 维护结束 / 活动开始，maapilot-events）：" + "、".join(n["due"]))
    for x in d.get("sources") or []:
        lines.append(f"外部数据：{x['message']}" + (f"，{x['effect']}" if x.get("effect") else "")
                     + "（要开发者修；用户问起或受影响时用玩家的话说明，见 maapilot-recovery）")
    for x in d.get("live_suspects") or []:
        lines.append(f"练度可能读错了：{x['name']} 在 {str(x.get('at') or '')[5:16].replace('T', ' ')} 由 {x.get('source')} 读到的练度，"
                     "一天后森空岛还比它低，已经不再采用；在游戏里看一眼是哪边对，告诉用户（要开发者查的话也说）")
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
    from maapilot import version
    data = {"ok_all": ok_all, "setup_done": paths.SETUP.exists(), "offline": bool(args.offline),
            "version": version.info(), "checks": checks}
    if fixed is not None:
        data["fixed"] = fixed
    if not ok_all:
        bad = [c["name"] for c in checks if not c["ok"] and c.get("required", True)]
        raise CliError("checks_failed", f"没通过：{'、'.join(bad)}", exit=ENVIRONMENT, data=data,
                       hint="按每项的 fix 处理；能自动修的先 maapilot doctor --fix")
    return data


def register(sub) -> None:
    p = sub.add_parser("doctor", help="检查环境并给修复建议")
    p.add_argument("--brief", action="store_true", help="给启动钩子：只看 setup、目标、对账、上次日常、没收尾的日常（不碰模拟器）")
    p.add_argument("--offline", action="store_true", help="不碰模拟器、不联网")
    p.add_argument("--fix", action="store_true", help="顺手做不用问用户的本地修复")
    p.add_argument("--hook", action="store_true", help="给启动钩子：同 --brief，但按 Claude Code 钩子的格式输出（不是信封）")
    p.set_defaults(func=cmd, command="doctor")
