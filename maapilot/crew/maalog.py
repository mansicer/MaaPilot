"""MAA 自己的日志目录（maa-cli 的 asst.log 与调试截图所在）：找位置、按时间找 MAA 存的截图。

maa-cli 的日志目录用 `maa dir log` 问出来（同 doctor 用 `maa dir resource`，不写死路径），里面
`interface/`、`other/`、`drops/`、`infrast/`、`oper/`… 下的 `YYYY.MM.DD-HH.MM.SS.mmm_raw.png` 是 MAA 出错或识别时存的截图（本地时间）。

本模块只读，不碰模拟器、不拿锁。`screenshots()` 给 maa 任务失败时的取证用。
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

DIR_ENV = "MAAPILOT_MAA_LOG_DIR"           # 测试或特殊安装时指定 maa-cli 的日志目录
SHOT_RE = re.compile(r"^(\d{4})\.(\d\d)\.(\d\d)-(\d\d)\.(\d\d)\.(\d\d)\.(\d+)_raw\.png$")

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
