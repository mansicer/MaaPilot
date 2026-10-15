"""调 maa-cli：写任务文件、运行、解析输出（Summary 块与 --log-file 时间线）。

maa-cli 的配置目录用本项目自己的 `private/maa-config/`（经环境变量 MAA_CONFIG_DIR 传给 maa-cli），不碰用户
原有的 maa-cli 配置：`cli.toml`、`profiles/default.toml` 由 `install_config()`（`doctor --fix`）从
templates/maa-config/ 复制过来；任务文件每次调用时生成（`tasks/maapilot-<名>.toml`）。
MaaCore 与资源仍装在 maa-cli 的数据目录（`maa install`），与配置目录无关。

这里不拿锁、不做参数白名单与源石的检查：那些在 maapilot.tools.maa_cmd。本模块只管"跑一次 maa、给回结构化结果"。
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from maapilot import paths
from maapilot.oscompat import procs

TASK_PREFIX = "maapilot-"
KILL_GRACE_S = 10
CONFIG_FILES = ("cli.toml", "profiles/default.toml")

TASK_RE = re.compile(r"^\[(?P<name>[^\]]+)\] (?:(?P<start>\d\d:\d\d:\d\d) - )?(?:(?P<end>\d\d:\d\d:\d\d) "
                     r"\((?P<dur>[^)]*)\) )?(?P<status>Completed|Error|Unfinished|Unstarted)$")
FIGHT_RE = re.compile(r"^Fight (?P<stage>\S+) (?P<n>\d+) times")
SANITY_RE = re.compile(r"Current sanity: (\d+)/(\d+)")
MISSION_RE = re.compile(r"Mission started")
DROPS_RE = re.compile(r"\] Drops: (.*)")
DROP_PART_RE = re.compile(r"\s*(.+?) × (\d+)")
MEDICINE_RE = re.compile(r"\] Use (\d+) (?:expiring )?medicine\b")
OFFLINE_RE = re.compile(r"\bGameOffline\b")


# --- 位置 ---------------------------------------------------------------------------

def config_dir() -> Path:
    """maa-cli 的配置目录：`$MAA_CONFIG_DIR` 优先，否则 private/maa-config/。"""
    env = os.environ.get("MAA_CONFIG_DIR")
    return Path(env).expanduser() if env else paths.PRIVATE / "maa-config"


def log_dir() -> Path:
    return paths.LOGS / "maa"


def maa_bin() -> str:
    """tools.maa；找得到就给实际的文件（Windows 上写成 C:\\…\\maa 时补上 .exe / .cmd，CreateProcess 只会补 .exe）。"""
    from maapilot import config
    t = config.tool("maa")
    return shutil.which(t) or t


def env() -> dict:
    return {**os.environ, "MAA_CONFIG_DIR": str(config_dir())}


def config_missing() -> list[str]:
    return [f for f in CONFIG_FILES if not (config_dir() / f).exists()]


def install_config(force: bool = False) -> list[str]:
    """把 templates/maa-config/ 的 cli.toml、profiles/default.toml 复制进配置目录；已有的不覆盖（setup 可能改过）。
    再把 profile 的 adb_path 对上 config.yaml 的 emulator.adb。返回复制了的文件。"""
    src = paths.TEMPLATES / "maa-config"
    done = []
    for f in CONFIG_FILES:
        dest = config_dir() / f
        if dest.exists() and not force:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / f, dest)
        done.append(f)
    (config_dir() / "tasks").mkdir(parents=True, exist_ok=True)
    sync_adb_path()
    return done


def _resolved_adb() -> str | None:
    """config.yaml 的 emulator.adb 解析成绝对路径（和 maapilot 自己用的同一个 adb）；找不到 → None（让 maa-cli 用它的默认）。"""
    try:
        from maapilot import config
        adb = str((config.load_or_defaults().get("emulator") or {}).get("adb") or "adb")
    except Exception:                                    # noqa: BLE001 - 配置读不了：不动 profile
        return None
    found = shutil.which(adb)
    return str(Path(found).absolute()) if found else None


def sync_adb_path() -> bool:
    """把 profiles/default.toml 的 `[connection] adb_path` 写成 maapilot 用的 adb，免得 maa-cli 另起一个 adb server
    跟 maapilot 的抢（Windows 上模拟器自带的 adb 和 PATH 上的版本不同时会互相杀掉）。改了返回 True。"""
    prof = config_dir() / "profiles" / "default.toml"
    adb = _resolved_adb()
    if adb is None or not prof.exists():
        return False
    text = prof.read_text(encoding="utf-8")
    line = f"adb_path = {_toml_value(adb)}"
    lines = text.splitlines()
    sec = next((i for i, l in enumerate(lines) if l.strip() == "[connection]"), None)
    if sec is None:
        lines += ["", "[connection]", line]
    else:
        end = next((i for i in range(sec + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        old = next((i for i in range(sec + 1, end) if re.match(r"\s*adb_path\s*=", lines[i])), None)
        if old is not None:
            if lines[old].strip() == line:
                return False
            lines[old] = line
        else:
            lines.insert(sec + 1, line)
    prof.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


# --- 任务文件 -------------------------------------------------------------------------

def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_toml_key(k)} = {_toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"TOML 不支持的值：{v!r}")


def _toml_key(k) -> str:
    k = str(k)
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _toml_value(k)


def to_toml(tasks: list[dict]) -> str:
    """[{name, type, params}] → maa-cli 任务文件（TOML）。"""
    out = []
    for t in tasks:
        out += ["[[tasks]]", f"name = {_toml_value(t.get('name') or t['type'])}", f"type = {_toml_value(t['type'])}",
                f"params = {_toml_value(t.get('params') or {})}", ""]
    return "\n".join(out)


def write_task_file(name: str, tasks: list[dict]) -> Path:
    p = config_dir() / "tasks" / f"{TASK_PREFIX}{name}.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_toml(tasks), encoding="utf-8")
    return p


# --- 运行 ---------------------------------------------------------------------------

@dataclass
class MaaRun:
    argv: list[str]
    returncode: int | None
    stdout: str
    log: Path
    duration_s: float
    timed_out: bool = False
    error: str | None = None                      # maa 没能启动
    parsed: dict = field(default_factory=dict)    # parse_output() 的结果

    @property
    def ok(self) -> bool:
        tasks = self.parsed.get("tasks") or []
        return (self.error is None and not self.timed_out and self.returncode == 0
                and all(t["status"] == "Completed" for t in tasks))

    @property
    def offline(self) -> bool:
        return bool(self.parsed.get("offline_seen"))


def new_log(label: str) -> Path:
    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{datetime.now():%Y%m%d-%H%M%S}-{re.sub(r'[^A-Za-z0-9_.-]', '_', label)}.log"


STOP_SIGNALS = procs.STOP_SIGNALS    # SIGTERM、SIGINT，再加 SIGHUP（macOS / Linux）或 SIGBREAK（Windows）
STOP_GRACE_S = 5          # 被叫停时给 maa 多久自己退；日常杀 maapilot 时 TERM 之后 15 秒才 KILL，要赶在那之前


@contextmanager
def _stops_with_us(proc: subprocess.Popen):
    """maa-cli 自成进程组（procs.spawn，超时时能整组结束）。所以 maapilot 自己被叫停（STOP_SIGNALS，
    例如日常超时杀掉 `maapilot maa …` 的进程组）时，maa 不会跟着停：这里在被叫停时先结束 maa 的进程树再退出；
    正常或异常退出时 maa 还在也结束掉（atexit 兜底；Windows 上本进程被硬杀时 atexit 不跑，靠 Job Object 带走）。"""
    def stop(*_):
        if proc.poll() is None:
            procs.kill_tree(proc, STOP_GRACE_S)

    def on_signal(signum, _frame):
        stop()
        raise SystemExit(128 + signum)

    old = {}
    if threading.current_thread() is threading.main_thread():
        for sig in STOP_SIGNALS:
            try:
                old[sig] = signal.signal(sig, on_signal)
            except (ValueError, OSError):
                pass
    atexit.register(stop)
    try:
        yield
    finally:
        stop()
        atexit.unregister(stop)
        for sig, h in old.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass


def _addr_args() -> list[str]:
    from maapilot.crew import device
    return ["--addr", device.current().address]


def execute(argv_tail: list[str], *, label: str, timeout_s: float) -> MaaRun:
    """`maa <argv_tail> --batch -v --log-file=<日志> --addr <地址>`，带超时（超时终止整个进程组）。"""
    log = new_log(label)
    argv = [maa_bin(), *argv_tail, "--batch", "-v", f"--log-file={log}", *_addr_args()]
    t0 = time.monotonic()
    try:
        proc = procs.spawn(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env())
    except OSError as e:
        return MaaRun(argv, None, "", log, 0.0, error=f"maa 启动失败：{e}")
    timed_out = False
    with _stops_with_us(proc):
        try:
            stdout, _ = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            procs.kill_tree(proc, KILL_GRACE_S)
            try:
                stdout, _ = proc.communicate(timeout=KILL_GRACE_S)
            except (subprocess.TimeoutExpired, ValueError):
                stdout = ""
    run = MaaRun(argv, proc.returncode, stdout or "", log, round(time.monotonic() - t0, 1), timed_out=timed_out)
    run.parsed = parse_output(run.stdout, log)
    return run


def run_tasks(name: str, tasks: list[dict], *, timeout_s: float = 3600) -> MaaRun:
    """写 tasks/maapilot-<name>.toml 并 `maa run maapilot-<name>`。"""
    write_task_file(name, tasks)
    return execute(["run", f"{TASK_PREFIX}{name}"], label=name, timeout_s=timeout_s)


# --- 解析 ---------------------------------------------------------------------------

def parse_summary(lines: list[str]) -> list[dict]:
    """一次 maa run 的 Summary：`[任务] hh:mm:ss - hh:mm:ss (时长) 状态` 与其下的明细行。"""
    tasks = []
    for ln in lines:
        m = TASK_RE.match(ln.strip())
        if m:
            tasks.append({**m.groupdict(), "detail": []})
        elif tasks and ln.strip() and not ln.startswith("---") and ln.strip() != "Summary":
            tasks[-1]["detail"].append(ln.rstrip())
    return tasks


def timeline(path: Path) -> dict:
    """日志里的理智（首、末）、开始行动次数、掉落合计（{中文名: 数量}）、用掉的理智液、有没有掉线、代理失败几次。
    代理失败（代理指挥没打赢）时游戏把理智全退回，MAA 把这一场记成掉落"理智 × N"：不算掉落，记进 proxy_failed / proxy_refund。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    sanity, fights, drops, medicine = [], 0, {}, 0
    proxy_failed, refund = 0, 0
    for ln in text.splitlines():
        if m := SANITY_RE.search(ln):
            sanity.append([int(m.group(1)), int(m.group(2))])
        if m := MEDICINE_RE.search(ln):
            medicine += int(m.group(1))
        if MISSION_RE.search(ln):
            fights += 1
        if m := DROPS_RE.search(ln):
            parts = [mm for part in m.group(1).split(",") if (mm := DROP_PART_RE.match(part))]
            if parts and all(mm.group(1).strip() == "理智" for mm in parts):
                proxy_failed += 1
                refund += sum(int(mm.group(2)) for mm in parts)
                continue
            for mm in parts:
                drops[mm.group(1).strip()] = drops.get(mm.group(1).strip(), 0) + int(mm.group(2))
    return {"sanity_first": sanity[0] if sanity else None, "sanity_last": sanity[-1] if sanity else None,
            "fights": fights, "drops": drops, "medicine": medicine,
            "proxy_failed": proxy_failed, "proxy_refund": refund,
            "offline_seen": bool(OFFLINE_RE.search(text))}


def parse_output(stdout: str, log: Path) -> dict:
    tasks = []
    fight_times = None
    for t in parse_summary(stdout.splitlines()):
        for ln in t["detail"]:
            if m := FIGHT_RE.match(ln.strip()):
                fight_times = (fight_times or 0) + int(m.group("n"))
        tasks.append({"name": t["name"], "status": t["status"], "duration": t.get("dur"), "detail": t["detail"][:20]})
    tl = timeline(log)
    return {"tasks": tasks, "fight_times": fight_times, **tl,
            "offline_seen": bool(tl.get("offline_seen") or OFFLINE_RE.search(stdout))}


def error_lines(run: MaaRun, n: int = 5) -> list[str]:
    try:
        lines = run.log.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        lines = []
    errs = [l.strip() for l in lines if "ERROR" in l or "Error" in l]
    if not errs:
        errs = [l.strip() for l in run.stdout.splitlines() if l.strip()]
    return errs[-n:]


def _extract_json(log_text: str, tag: str) -> dict:
    idx = log_text.rfind(f"{tag}: ")
    if idx < 0:
        raise RuntimeError(f"maa 日志里没有 {tag} 的结果")
    blk = log_text[idx + len(tag) + 2:]
    depth = 0
    for i, ch in enumerate(blk):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(blk[: i + 1])
    raise RuntimeError("maa 日志里的 JSON 不完整")


# --- 识别（state、guard、快捷操作用） ---------------------------------------------------

def recognize(kind: str, timeout_s: float = 900) -> tuple[dict, MaaRun]:
    """跑 MAA 的 Depot / OperBox 识别，返回（日志里的结果 JSON, MaaRun）。失败抛 RuntimeError（消息带日志位置）。"""
    run = run_tasks(kind.lower(), [{"name": kind, "type": kind, "params": {}}], timeout_s=timeout_s)
    if not run.ok:
        detail = run.error or " | ".join(error_lines(run))
        raise RuntimeError(f"MAA {kind} 识别失败（日志 {run.log}）" + (f"：{detail}" if detail else ""))
    text = run.log.read_text(encoding="utf-8", errors="ignore") if run.log.exists() else ""
    try:
        return _extract_json(text, kind), run
    except RuntimeError:
        return _extract_json(run.stdout, kind), run


def operbox() -> dict:
    """{名: {id, elite, level, rarity, potential}}，只含已拥有的干员。"""
    d = recognize("OperBox")[0]["details"]
    if not d.get("own_opers"):          # 一名都没有 = 没进到干员列表
        raise RuntimeError("MAA OperBox 没识别到任何干员（多半没进到干员列表页）")
    return {o["name"]: {"id": o.get("id"), **{k: o.get(k) for k in ("elite", "level", "rarity", "potential")}}
            for o in d["own_opers"]}


def depot_ids() -> dict:
    """{物品 id: 数量}：MAA Depot 的结果本来就按 item_table 的 id。"""
    raw = json.loads(recognize("Depot")[0]["details"]["data"])
    return {str(k): int(v) for k, v in raw.items()}


def depot() -> dict:
    """{中文名: 数量}（旧的快捷操作用）。"""
    from maapilot.knowledge.items import display_name
    return {display_name(k): v for k, v in depot_ids().items()}
