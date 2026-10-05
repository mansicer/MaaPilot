"""MAA 自己的详细日志（maa-cli 的 asst.log）与调试截图：找位置、按时间段读、整理成事实给 agent。

maa-cli 的日志目录用 `maa dir log` 问出来（同 doctor 用 `maa dir resource`，不写死路径），里面有：
- `asst.log`（满了轮换成 `asst.bak.log`）：每行 `[YYYY-MM-DD HH:MM:SS.mmm][级别][Px..][Tx..] 内容`，本地时间；
  文件很大（几十 MB），按时间二分定位，只读要的那一段，读的字节数有上限；
- `interface/`、`other/`、`drops/`、`infrast/`、`oper/`… 下的 `YYYY.MM.DD-HH.MM.SS.mmm_raw.png`：MAA 出错或识别时存的截图。

本模块只读，不碰模拟器、不拿锁。`summarize()` 给 `maapilot maa why` 用，`screenshots()` 也给失败时的取证用。
这里只摆事实（做了什么、看到什么、什么时候），不判断原因：怎么读这些事实写在 skill maapilot-recovery 里，由 agent 判断。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

DIR_ENV = "MAAPILOT_MAA_LOG_DIR"           # 测试或特殊安装时指定 maa-cli 的日志目录
LOG_FILES = ("asst.bak.log", "asst.log")    # 先旧后新
MAX_READ_BYTES = 48 * 1024 * 1024           # 一个时间段最多读这么多（约 20 分钟的日志）
SHOT_RE = re.compile(r"^(\d{4})\.(\d\d)\.(\d\d)-(\d\d)\.(\d\d)\.(\d\d)\.(\d+)_raw\.png$")
LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)\]\[(\w+)\]\[Px(\d+)\]\[Tx\d+\] ?(.*)$")
TS_FMT = "%Y-%m-%d %H:%M:%S.%f"

_dir_cache: dict = {}


# --- 位置 ---------------------------------------------------------------------------------------------------------

def debug_dir(cfg: dict | None = None) -> Path | None:
    """maa-cli 的日志目录（asst.log、调试截图所在）。`$MAAPILOT_MAA_LOG_DIR` 优先，否则 `maa dir log`。找不到 None。"""
    env = os.environ.get(DIR_ENV)
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    if "dir" in _dir_cache:
        return _dir_cache["dir"]
    found = None
    try:
        from maapilot import config
        from maapilot.crew import maa
        maa_bin = config.tool("maa", cfg)
        r = subprocess.run([maa_bin, "dir", "log"], capture_output=True, text=True, timeout=15, env=maa.env())
        lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
        if r.returncode == 0 and lines and Path(lines[-1]).is_dir():
            found = Path(lines[-1])
    except Exception:                      # noqa: BLE001 - 没装 maa-cli、没配置：当作找不到
        found = None
    _dir_cache["dir"] = found
    return found


# --- 时间 ---------------------------------------------------------------------------------------------------------

def local_naive(t: datetime) -> datetime:
    """带时区的时间 → 本机本地时间（不带时区），asst.log 与截图文件名都是本地时间。"""
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def _fmt(t: datetime) -> str:
    return t.strftime(TS_FMT)[:23]


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.astimezone().isoformat(timespec="seconds")


# --- 调试截图 -----------------------------------------------------------------------------------------------------

def shot_time(name: str) -> datetime | None:
    m = SHOT_RE.match(name)
    if not m:
        return None
    y, mo, d, h, mi, s, ms = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), min(int(ms), 999) * 1000)
    except ValueError:
        return None


def screenshots(start: datetime, end: datetime, root: Path | None = None, limit: int = 12) -> list[dict]:
    """MAA 存在日志目录各子目录里的截图，时间落在 [start, end] 的（本地时间），按时间排，最多 limit 张（取最后的）。"""
    root = root or debug_dir()
    if root is None:
        return []
    start, end = local_naive(start), local_naive(end)
    out = []
    try:
        subdirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return []
    for sub in subdirs:
        try:
            names = os.listdir(sub)
        except OSError:
            continue
        for n in names:
            t = shot_time(n)
            if t is not None and start <= t <= end:
                out.append({"path": str((sub / n).resolve()), "kind": sub.name, "at": _iso(t), "_t": t})
    out.sort(key=lambda x: x["_t"])
    return [{k: v for k, v in x.items() if k != "_t"} for x in out[-limit:]]


def import_shots(shots: list[dict], label: str, dest: Path | None = None) -> list[dict]:
    """把 MAA 的截图复制进项目的 private/shots/（`<YYYYmmdd-HHMMSS>-maa-<label>-<毫秒>.png`）：MAA 的日志目录在项目外，
    无头 agent 读不了。返回的 `path` 是项目里的副本，`source` 是原来的位置；已经复制过的（同名同大小）直接用。
    复制不了的那张 `path` 为 null、带 `error`。"""
    import re
    import shutil
    from maapilot import paths
    dest = dest or paths.SHOTS
    safe = re.sub(r"[^\w.-]+", "_", label or "maa").strip("_") or "maa"
    out = []
    for s in shots:
        src = Path(s["path"])
        t = shot_time(src.name)
        stamp = f"{t:%Y%m%d-%H%M%S}-maa-{safe}-{t.microsecond // 1000:03d}" if t else f"maa-{safe}-{src.stem}"
        target = dest / f"{stamp}.png"
        item = {**s, "source": str(src)}
        try:
            if not (target.exists() and target.stat().st_size == src.stat().st_size):
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
            item["path"] = str(target.resolve())
        except OSError as e:
            item.update(path=None, error=f"复制不了：{e}")
        out.append(item)
    return out


# --- 按时间段读 asst.log --------------------------------------------------------------------------------------------

def _next_ts(f, off: int) -> str | None:
    """从 off 往后第一行带时间戳的行的时间戳（字符串）。"""
    f.seek(off)
    if off:
        f.readline()                       # 丢掉半行
    for _ in range(500):
        line = f.readline()
        if not line:
            return None
        if line.startswith(b"[") and len(line) > 25:
            m = LINE_RE.match(line[:80].decode("utf-8", "replace"))
            if m:
                return m.group(1)
    return None


def _seek_start(f, size: int, target: str) -> int:
    """二分找一个偏移：它之后的行时间 ≥ target 之前不会漏行（返回的位置在目标之前不远）。"""
    lo, hi = 0, size
    while hi - lo > 256 * 1024:
        mid = (lo + hi) // 2
        ts = _next_ts(f, mid)
        if ts is None or ts >= target:
            hi = mid
        else:
            lo = mid
    return lo


def read_window(start: datetime, end: datetime, root: Path | None = None,
                max_bytes: int = MAX_READ_BYTES) -> tuple[list[tuple[str, str, str, str]], dict]:
    """asst.log（含轮换的 asst.bak.log）里 [start, end] 的行 → ([(时间, 级别, 进程, 内容)], 读取信息)。
    不带时间戳的续行接在上一行后面。读到 max_bytes 就停（info.truncated）。"""
    root = root or debug_dir()
    info = {"dir": str(root) if root else None, "files": [], "bytes_read": 0, "truncated": False}
    if root is None:
        return [], info
    lo_s, hi_s = _fmt(local_naive(start)), _fmt(local_naive(end))
    rows: list[list[str]] = []
    budget = max_bytes
    for name in LOG_FILES:
        p = root / name
        try:
            size = p.stat().st_size
        except OSError:
            continue
        with open(p, "rb") as f:
            last = _last_ts(f, size)
            if last is not None and last < lo_s:
                continue                   # 整个文件都在时间段之前
            off = _seek_start(f, size, lo_s)
            f.seek(off)
            if off:
                f.readline()
            info["files"].append(str(p))
            inside = False
            while budget > 0:
                raw = f.readline()
                if not raw:
                    break
                budget -= len(raw)
                info["bytes_read"] += len(raw)
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                m = LINE_RE.match(line) if line.startswith("[") else None
                if m:
                    ts = m.group(1)
                    if ts < lo_s:
                        continue
                    if ts > hi_s:
                        break
                    inside = True
                    rows.append([ts, m.group(2), m.group(3), m.group(4)])
                elif inside and rows and line.strip():
                    rows[-1][3] += "\n" + line
            if budget <= 0:
                info["truncated"] = True
                break
    return [tuple(r) for r in rows], info


def _last_ts(f, size: int) -> str | None:
    for back in (64 * 1024, 1024 * 1024):
        ts = None
        f.seek(max(0, size - back))
        if size > back:
            f.readline()
        for line in f.read().splitlines():
            m = LINE_RE.match(line[:80].decode("utf-8", "replace")) if line.startswith(b"[") else None
            if m:
                ts = m.group(1)
        if ts:
            return ts
    return None


# --- 整理（只摆事实，不下结论） -----------------------------------------------------------------------------------

CALLBACK_RE = re.compile(r"Assistant::append_callback \| (\w+) (\{.*)$", re.S)
OCR_RE = re.compile(r"text: ([^,\]]*), rect")
SCREENCAP_RE = re.compile(r"screencap", re.I)
SCREENCAP_RET_RE = re.compile(r" ret (-?\d+)")
ROUTINE_CONNECTION = ("ScreencapCost", "EmulatorFPS")    # 每隔几秒就有一条的连接信息，不列


def _readable(t: str) -> bool:
    """OCR 出来的字里有汉字，或至少两个字母数字（"Z_"、"_" 这类空白处的噪声不要）。"""
    return bool(re.search(r"[\u4e00-\u9fff]", t)) or len(re.findall(r"[A-Za-z0-9]", t)) >= 2


def _jload(s: str) -> dict:
    try:
        return json.loads(s)
    except ValueError:
        try:                                # 续行接上的多余内容
            return json.loads(s.split("\n", 1)[0])
        except ValueError:
            return {}


def _hm(ts: str) -> str:
    return ts[11:19]


def _span_s(a: str, b: str) -> int:
    def s(x: str) -> int:
        h, m, sec = (int(v) for v in x.split(":"))
        return h * 3600 + m * 60 + sec
    return max(0, s(b) - s(a))


def _matched(det: dict) -> str | None:
    r = det.get("result") or {}
    return r.get("text") or r.get("template")


def _group(store: dict, key: str, ts: str, **extra) -> None:
    g = store.get(key)
    if g is None:
        store[key] = {**extra, "first": _hm(ts), "last": _hm(ts), "count": 1}
    else:
        g["last"], g["count"] = _hm(ts), g["count"] + 1


def summarize(rows: list[tuple[str, str, str, str]], *, steps_n: int = 40, tried_n: int = 20, errors_n: int = 10,
              ocr_n: int = 80, err_lines_n: int = 15) -> dict:
    """把一段 asst.log 整理成 agent 能读的事实：MAA 一步步做了什么（认出 / 点了什么）、在找哪些页面、SubTaskError、
    看到过的所有字（去重、带时间）、"Loading" 一类等待的时长、截图失败、连接信息、ERR 行。不推断原因。"""
    steps: list[dict] = []
    tried: list[dict] = []
    errors: list[dict] = []
    chains: list[dict] = []
    ocr: dict[str, dict] = {}
    waits: list[dict] = []
    screencap_fail: dict[str, dict] = {}
    connection: dict[str, dict] = {}
    err_lines: dict[str, dict] = {}
    last_match = None

    for ts, level, _px, msg in rows:
        cb = CALLBACK_RE.match(msg)
        if cb:
            kind, js = cb.group(1), _jload(cb.group(2))
            det = js.get("details") or {}
            if kind == "SubTaskStart" and js.get("class") == "asst::ProcessTask":
                task, hit = det.get("task"), _matched(det)
                if hit and det.get("algorithm") != "JustReturn":
                    last_match = {"at": _hm(ts), "task": task, "matched": hit}
                # MAA 自己的 Loading* 任务（等加载、等服务器回应）：连续的合成一段，记时长
                if task and "Loading" in task:
                    if waits and waits[-1]["task"] == task and waits[-1]["matched"] == hit and waits[-1]["_open"]:
                        waits[-1]["last"], waits[-1]["count"] = _hm(ts), waits[-1]["count"] + 1
                    else:
                        waits.append({"task": task, "matched": hit, "first": _hm(ts), "last": _hm(ts), "count": 1,
                                      "_open": True})
                elif waits:
                    waits[-1]["_open"] = False
                step = {"at": _hm(ts), "task": task, "action": det.get("action"), "matched": hit}
                if steps and all(steps[-1].get(k) == step[k] for k in ("task", "action", "matched")):
                    steps[-1]["repeat"] = steps[-1].get("repeat", 1) + 1
                    steps[-1]["last"] = _hm(ts)
                else:
                    steps.append(step)
            elif kind == "SubTaskError":
                errors.append({"at": _hm(ts), "class": (js.get("class") or "").removeprefix("asst::"),
                               "first": js.get("first"), "pre_task": js.get("pre_task") or None,
                               **({"details": det} if det else {})})
            elif kind.startswith("TaskChain"):
                chains.append({"at": _hm(ts), "event": kind, "taskchain": js.get("taskchain")})
            elif kind == "ConnectionInfo" and js.get("what") not in ROUTINE_CONNECTION:
                _group(connection, f"{js.get('what')} {json.dumps(det, ensure_ascii=False)}"[:200], ts,
                       what=js.get("what"), details=det)
            continue
        if msg.startswith('{"class"') and '"cur_task"' in msg:
            js = _jload(msg)
            key = (js.get("cur_task") or None, tuple(js.get("first") or []))
            if not tried or (tried[-1]["cur_task"], tuple(tried[-1]["first"])) != key:
                tried.append({"at": _hm(ts), "cur_task": key[0], "first": list(key[1])})
            continue
        if "text: " in msg and ("by OCR" in msg or msg.startswith("Proceed")):
            texts = [t.strip() for t in OCR_RE.findall(msg)]
            texts = [t for t in texts if _readable(t)]
            if texts:
                _group(ocr, " | ".join(texts), ts, text=" | ".join(texts))
            continue
        if SCREENCAP_RE.search(msg) and "| nc " not in msg:      # nc：连接时试截图方式，不算
            m = SCREENCAP_RET_RE.search(msg)
            if (m and m.group(1) != "0") or (level == "ERR"):
                _group(screencap_fail, re.sub(r"\d+", "#", msg[:100]), ts, message=msg[:200])
        if level == "ERR":
            _group(err_lines, re.sub(r"\d+", "#", msg[:120]), ts, message=msg[:200])

    for w in waits:
        w.pop("_open", None)
        w["seconds"] = _span_s(w["first"], w["last"])
    # 字太多时留最后还看得到的 ocr_n 种（按最后一次出现），再按第一次出现的时间排
    ocr_list = sorted(sorted(ocr.values(), key=lambda g: g["last"])[-ocr_n:], key=lambda g: (g["first"], g["last"]))
    return {
        "lines": len(rows),
        "start": _hm(rows[0][0]) if rows else None, "end": _hm(rows[-1][0]) if rows else None,
        "taskchains": chains,
        "steps": steps[-steps_n:], "steps_total": len(steps),
        "tried": tried[-tried_n:],
        "errors": errors[-errors_n:], "error_count": len(errors),
        "last_match": last_match,
        "ocr": ocr_list, "ocr_total": len(ocr),
        "waits": waits[-10:],
        "screencap_failures": list(screencap_fail.values()),
        "connection": list(connection.values()),
        "error_lines": sorted(err_lines.values(), key=lambda e: e["first"])[-err_lines_n:],
    }


def why(start: datetime, end: datetime, *, root: Path | None = None, max_bytes: int = MAX_READ_BYTES,
        shot_grace_s: int = 30) -> dict:
    """时间段 → {log, 整理出的事实…, screenshots}。日志目录找不到时 log.dir 为 null。"""
    root = root or debug_dir()
    rows, info = read_window(start, end, root, max_bytes=max_bytes)
    return {"log": info, **summarize(rows), "screenshots": screenshots(start, end + timedelta(seconds=shot_grace_s), root)}
