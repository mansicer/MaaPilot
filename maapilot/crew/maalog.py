"""MAA 自己的详细日志（maa-cli 的 asst.log）与调试截图：找位置、按时间段读、给 agent 摘要。

maa-cli 的日志目录用 `maa dir log` 问出来（同 doctor 用 `maa dir resource`，不写死路径），里面有：
- `asst.log`（满了轮换成 `asst.bak.log`）：每行 `[YYYY-MM-DD HH:MM:SS.mmm][级别][Px..][Tx..] 内容`，本地时间；
  文件很大（几十 MB），按时间二分定位，只读要的那一段，读的字节数有上限；
- `interface/`、`other/`、`drops/`、`infrast/`、`oper/`… 下的 `YYYY.MM.DD-HH.MM.SS.mmm_raw.png`：MAA 出错或识别时存的截图。

本模块只读，不碰模拟器、不拿锁。`summarize()` 给 `maapilot maa why` 用，`screenshots()` 也给失败时的取证用。
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


def _parse_ts(s: str) -> datetime | None:
    try:
        return datetime.strptime(s if "." in s else s + ".0", TS_FMT)
    except ValueError:
        return None


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


# --- 摘要 ---------------------------------------------------------------------------------------------------------

CALLBACK_RE = re.compile(r"Assistant::append_callback \| (\w+) (\{.*)$", re.S)
OCR_RE = re.compile(r"text: ([^,\]]*), rect")
# 标题页（被登出）：MAA 的 StartToWakeUp 系列，或 OCR 到标题页上的字
TITLE_TASK_RE = re.compile(r"StartToWakeUp|StartUpConnectingFlag|AccountManager|LoginOther")
TITLE_TEXT_RE = re.compile(r"开始唤醒|扫码登录|账号管理|查看公告|^玩家")
OTHER_DEVICE_RE = re.compile(r"其他设备|其它设备|另一台设备|别的设备|异地登录|在别处登录|顶号|被踢")
OFFLINE_TEXT_RE = re.compile(r"网络|连接失败|连接超时|重新连接|断开|登录失效|登录已过期|过期|会话|重新登录|服务器|维护")
OFFLINE_TASK_RE = re.compile(r"OfflineConfirm|GameOffline|Reconnect|Disconnect")
LOADING_TEXTS = ("正在提交", "反馈至神经", "正在加载", "加载中")
SCREENCAP_FAIL_RE = re.compile(r"Killing child .*screencap|screencap.* ret (?!0 )|Screencap(?:Failed|Error)|"
                               r"screencap failed|reconnect", re.I)
NOISE_ERR = ("skill has no recognition result", "Unknown task:", "not found", "| nc ")  # nc：连接时试截图方式


def _readable(t: str) -> bool:
    """OCR 出来的字里有汉字，或至少两个字母数字（"Z_"、"_" 这类噪声不要）。"""
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


def _matched(det: dict) -> str | None:
    r = det.get("result") or {}
    return r.get("text") or r.get("template")


def summarize(rows: list[tuple[str, str, str, str]], *, steps_n: int = 30, errors_n: int = 8,
              ocr_n: int = 20, ocr_tail_s: int = 120) -> dict:
    """把一段 asst.log 摘成 agent 能读的东西：做了哪些步、点了 / 认出了什么、最后的错误、看到的字、要紧的事件、推断。"""
    steps: list[dict] = []
    errors: list[dict] = []
    ocr: list[dict] = []
    events: dict[str, dict] = {}
    loading: list[dict] = []
    tried: list[dict] = []                  # cur_task / first（MAA 在找哪一页）
    last_match = None
    title_at = None
    other_device = None
    chains: list[dict] = []
    err_lines: dict[str, dict] = {}

    def event(kind: str, ts: str, detail: str) -> None:
        e = events.get(kind)
        if e is None:
            events[kind] = {"kind": kind, "first": _hm(ts), "last": _hm(ts), "count": 1, "detail": detail[:160]}
        else:
            e["last"], e["count"] = _hm(ts), e["count"] + 1

    for ts, level, _px, msg in rows:
        cb = CALLBACK_RE.match(msg)
        if cb:
            kind, js = cb.group(1), _jload(cb.group(2))
            det = js.get("details") or {}
            if kind == "SubTaskStart" and js.get("class") == "asst::ProcessTask":
                task, hit = det.get("task"), _matched(det)
                algo = det.get("algorithm")
                if hit and algo != "JustReturn":
                    last_match = {"at": _hm(ts), "task": task, "matched": hit}
                if hit in LOADING_TEXTS or task in ("LoadingText",):
                    if loading and loading[-1]["text"] == hit and loading[-1]["_open"]:
                        loading[-1]["last"], loading[-1]["count"] = _hm(ts), loading[-1]["count"] + 1
                    else:
                        loading.append({"text": hit or task, "first": _hm(ts), "last": _hm(ts), "count": 1,
                                        "_open": True})
                elif loading:
                    loading[-1]["_open"] = False
                if task and TITLE_TASK_RE.search(task):
                    title_at = title_at or _hm(ts)
                    event("title_screen", ts, f"MAA 认出标题页（{task}）")
                if task and OFFLINE_TASK_RE.search(task):
                    event("offline_dialog", ts, f"MAA 认出掉线弹窗（{task}）")
                if hit and OTHER_DEVICE_RE.search(str(hit)):
                    other_device = other_device or _hm(ts)
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
            elif kind in ("TaskChainStart", "TaskChainError", "TaskChainCompleted", "TaskChainStopped"):
                chains.append({"at": _hm(ts), "event": kind, "taskchain": js.get("taskchain")})
            elif kind == "ConnectionInfo":
                what = js.get("what")
                if what not in ("ScreencapCost", "EmulatorFPS", "ResolutionGot", "ResolutionInfo", "UuidGot",
                                "Connected", "FastestWayToScreencap", "ConnectionInfo"):
                    event(f"connection:{what}", ts, json.dumps(det, ensure_ascii=False))
            continue
        if msg.startswith('{"class"') and '"cur_task"' in msg:
            js = _jload(msg)
            cur = js.get("cur_task") or None
            first = js.get("first") or []
            key = (cur, tuple(first))
            if not tried or (tried[-1]["cur_task"], tuple(tried[-1]["first"])) != key:
                tried.append({"at": _hm(ts), "cur_task": cur, "first": first})
            continue
        if "text: " in msg and ("by OCR" in msg or msg.startswith("Proceed")):
            texts = [t.strip() for t in OCR_RE.findall(msg)]
            texts = [t for t in texts if _readable(t)]
            if texts:
                ocr.append({"at": _hm(ts), "_ts": ts, "texts": texts})
                joined = " ".join(texts)
                if any(TITLE_TEXT_RE.search(t) for t in texts):
                    title_at = title_at or _hm(ts)
                    event("title_screen", ts, f"OCR 看到标题页的字：{joined}")
                if OTHER_DEVICE_RE.search(joined):
                    other_device = other_device or _hm(ts)
                    event("other_device_text", ts, f"OCR：{joined}")
                elif OFFLINE_TEXT_RE.search(joined):
                    event("offline_text", ts, f"OCR：{joined}")
            continue
        if "GameOffline" in msg:
            event("offline_dialog", ts, msg[:160])
        if SCREENCAP_FAIL_RE.search(msg) and level in ("ERR", "WRN") and "| nc " not in msg:
            event("screencap_failed", ts, msg[:160])
        if level == "ERR" and not any(n in msg for n in NOISE_ERR):
            k = re.sub(r"\d+", "#", msg[:120])
            e = err_lines.get(k)
            if e is None:
                err_lines[k] = {"at": _hm(ts), "message": msg[:200], "count": 1}
            else:
                e["count"] += 1

    # OCR：只留最后 ocr_tail_s 秒；同样的字合成一条（第一次、最后一次、几次），按最后一次排
    if ocr:
        end_t = _parse_ts(ocr[-1]["_ts"])
        groups: dict[str, dict] = {}
        for o in ocr:
            t = _parse_ts(o["_ts"])
            if end_t is not None and t is not None and t < end_t - timedelta(seconds=ocr_tail_s):
                continue
            k = " | ".join(o["texts"])
            g = groups.get(k)
            if g is None:
                groups[k] = {"text": k, "first": o["at"], "last": o["at"], "count": 1}
            else:
                g["last"], g["count"] = o["at"], g["count"] + 1
        ocr = sorted(groups.values(), key=lambda g: g["last"])[-ocr_n:]
    for l in loading:
        l.pop("_open", None)
        l["seconds"] = _span_s(l["first"], l["last"])
    return {
        "lines": len(rows),
        "start": _hm(rows[0][0]) if rows else None, "end": _hm(rows[-1][0]) if rows else None,
        "taskchains": chains,
        "steps": steps[-steps_n:],
        "tried": tried[-15:],
        "errors": errors[-errors_n:],
        "error_count": len(errors),
        "ocr_tail": ocr,
        "loading": [l for l in loading if l["count"] >= 3][-5:],
        "events": list(events.values()),
        "error_lines": sorted(err_lines.values(), key=lambda e: e["at"])[-8:],
        "last_match": last_match,
        "_title_at": title_at, "_other_device": other_device,
    }


def _span_s(a: str, b: str) -> int:
    def s(x: str) -> int:
        h, m, sec = (int(v) for v in x.split(":"))
        return h * 3600 + m * 60 + sec
    return max(0, s(b) - s(a))


def likely_cause(summary: dict, shots: list[dict]) -> dict:
    """几条简单规则推一个原因 + 证据。看不出就是 unknown，不猜。"""
    ev = {e["kind"]: e for e in summary.get("events") or []}
    evidence: list[str] = []
    title_at, other_at = summary.get("_title_at"), summary.get("_other_device")
    last = summary.get("last_match")
    if last:
        evidence.append(f"MAA 最后一次认出东西：{last['at']}（{last['task']}：{last['matched']}）")
    stuck = [l for l in summary.get("loading") or [] if l["seconds"] >= 15]
    for l in stuck:
        evidence.append(f"{l['first']}–{l['last']} 一直是「{l['text']}」（{l['seconds']} 秒，等服务器回应）")
    if shots:
        evidence.append(f"MAA 在这段时间存了 {len(shots)} 张截图，最后一张 {shots[-1]['at'][11:19]}")
    if other_at:
        return {"kind": "other_device", "summary": f"{other_at} 看到别的设备登录的提示：账号在别处登录，被挤下线",
                "evidence": evidence + [ev.get("other_device_text", {}).get("detail", "")]}
    if title_at:
        why = "游戏已经回到标题页（开始唤醒）：被登出了"
        if stuck:
            why += f"；之前「{stuck[-1]['text']}」卡了 {stuck[-1]['seconds']} 秒，像是网络或服务器断了连接"
        why += "。日志里没有别的设备登录的提示（提示框可能一闪而过没被记下，看截图）"
        return {"kind": "logged_out", "summary": f"最晚 {title_at} {why}",
                "evidence": evidence + [ev["title_screen"]["detail"]]}
    if "offline_dialog" in ev or "offline_text" in ev:
        e = ev.get("offline_dialog") or ev.get("offline_text")
        return {"kind": "offline", "summary": f"{e['first']} 出现掉线 / 网络提示：{e['detail']}", "evidence": evidence}
    if "screencap_failed" in ev and ev["screencap_failed"]["count"] >= 3:
        e = ev["screencap_failed"]
        return {"kind": "emulator", "summary": f"截图失败 {e['count']} 次（{e['first']}–{e['last']}）：模拟器连接有问题",
                "evidence": evidence}
    if stuck:
        return {"kind": "stuck_loading", "summary": f"「{stuck[-1]['text']}」卡了 {stuck[-1]['seconds']} 秒后 MAA 认不出页面了；"
                "原因不确定，看截图", "evidence": evidence}
    errs = summary.get("errors") or []
    if errs and not last:
        firsts = sorted({f for e in errs for f in (e.get("first") or [])})
        return {"kind": "entry_failed",
                "summary": f"MAA 从头到尾没认出任何页面（找不到 {', '.join(firsts[:3])}）：开始时游戏就不在它认得的页面"
                           "（标题页、弹窗或别的页面），看截图",
                "evidence": evidence}
    if errs:
        firsts = errs[-1].get("first") or []
        evidence.append(f"最后的错误：{errs[-1]['at']} 找不到 {', '.join(firsts[:3])}")
    return {"kind": "unknown", "summary": "日志里看不出原因，看截图判断停在哪一页", "evidence": evidence}


def why(start: datetime, end: datetime, *, root: Path | None = None, max_bytes: int = MAX_READ_BYTES,
        shot_grace_s: int = 30) -> dict:
    """时间段 → {window, log, summary…, screenshots, likely_cause}。日志目录找不到时 log.dir 为 null。"""
    root = root or debug_dir()
    rows, info = read_window(start, end, root, max_bytes=max_bytes)
    s = summarize(rows)
    shots = screenshots(start, end + timedelta(seconds=shot_grace_s), root)
    cause = likely_cause(s, shots) if rows else {
        "kind": "unknown", "summary": "这段时间 MAA 的详细日志里没有内容" if root else "找不到 MAA 的日志目录", "evidence": []}
    s.pop("_title_at", None)
    s.pop("_other_device", None)
    return {"log": info, **s, "screenshots": shots, "likely_cause": cause}
