"""MAA 认得的活动关（SideStory / 复刻）：读 maa-cli 缓存的 MAA 活动列表 StageActivityV2.json。只读，不碰模拟器。

MAA 每期活动开始后一两天，在资源热更新里加上这期活动：StageActivityV2.json 的 `<服务器>.sideStoryStage.<前缀>`
（活动名、开始 / 结束时间、推荐关卡 Stages），同时在 resource/tasks/tasks.json 里给这些关卡加导航任务（`YW-8`、`YW-8@SideStoryStage`……）。
MAA 只能导航有任务的关：一般就是 Stages 列出的那几关（例如昨日海只有 YW-8/7/6/4，YW-3 没有）。所以：
- 列表里有、正在开放、关卡列出来了（或 tasks.json 里有它的导航任务）→ 可导航（ss_current / 复刻 ss_reopen）；
- 其余活动关 → 不可导航，why_not() 给出原因（MAA 还没加这期活动 / 只支持哪几关 / 活动没开始或已结束）。

时间：文件里的 "UtcStartTime" 其实是 TimeZone 时区（官服 8）的本地时间，MAA 自己也是减去 TimeZone 小时换成 UTC。
文件位置：MAAPILOT_MAA_ACTIVITY_FILE（测试用）；否则 maa-cli 的缓存目录（macOS ~/Library/Caches/com.loong.maa，
其他 $XDG_CACHE_HOME/maa）。导航任务表在同目录 resource/tasks/tasks.json（MAAPILOT_MAA_TASKS_FILE）。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

ACTIVITY_FILE_ENV = "MAAPILOT_MAA_ACTIVITY_FILE"
TASKS_FILE_ENV = "MAAPILOT_MAA_TASKS_FILE"
REOPEN_PREFIX = "SSReopen"


def _cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "com.loong.maa"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "maa"


def activity_file() -> Path:
    v = os.environ.get(ACTIVITY_FILE_ENV)
    return Path(v) if v else _cache_dir() / "StageActivityV2.json"


def tasks_file() -> Path:
    v = os.environ.get(TASKS_FILE_ENV)
    if v:
        return Path(v)
    return activity_file().parent / "resource" / "tasks" / "tasks.json"


def _server() -> str:
    try:
        from maapilot import config
        return config.server()
    except Exception:                                    # noqa: BLE001 - 没配置：按官服
        return "Official"


@dataclass(frozen=True)
class Activity:
    key: str                     # 文件里的键：YW、SSReopen……
    name: str                    # 活动名（昨日海）
    tip: str
    start: datetime
    end: datetime
    listed: tuple[str, ...]      # MAA 列出的关（给 MAA 的值：YW-8……；复刻还有 SSReopen-AT 这种整段代理）
    reopen: bool
    min_maa: str | None
    stages: tuple[str, ...] = ()     # 能导航的关在 stage_table 里的 stageId（map_stages 之后才有）
    zone_ids: tuple[str, ...] = ()   # 故意留空：同一活动里没列出的关不能导航

    @property
    def prefixes(self) -> frozenset[str]:
        """关卡编号前缀（YW）。复刻的键是 SSReopen，前缀从列出的关里取。"""
        out = {v.split("-", 1)[0] for v in self.listed if not v.startswith(REOPEN_PREFIX)}
        if not self.reopen:
            out.add(self.key)
        return frozenset(out)

    def open_at(self, at: datetime) -> bool:
        return self.start <= at < self.end

    def to_dict(self) -> dict:
        return {"prefix": sorted(self.prefixes)[0] if self.prefixes else self.key, "key": self.key,
                "name": self.name, "start": self.start.isoformat(timespec="minutes"),
                "end": self.end.isoformat(timespec="minutes"), "listed": list(self.listed),
                "reopen": self.reopen, "min_maa": self.min_maa}


def _time(s: str, tz_h: float) -> datetime:
    return datetime.strptime(s, "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=tz_h)))


def parse(doc: dict, server: str = "Official") -> list[Activity]:
    """StageActivityV2.json → 这个服务器的活动关（sideStoryStage）。写法不对的条目跳过。"""
    out = []
    for key, a in ((doc.get(server) or {}).get("sideStoryStage") or {}).items():
        try:
            act = a.get("Activity") or {}
            tz_h = float(act.get("TimeZone", 8))
            start, end = _time(act["UtcStartTime"], tz_h), _time(act["UtcExpireTime"], tz_h)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        listed = tuple(str(s.get("Value")) for s in a.get("Stages") or [] if isinstance(s, dict) and s.get("Value"))
        tip = str(act.get("Tip") or "")
        out.append(Activity(key=key, name=str(act.get("StageName") or key), tip=tip, start=start, end=end,
                            listed=listed, reopen=key.startswith(REOPEN_PREFIX) or "复刻" in tip,
                            min_maa=a.get("MinimumRequired")))
    return out


def _mtime(p: Path) -> float | None:
    try:
        return p.stat().st_mtime
    except OSError:
        return None


@lru_cache(maxsize=4)
def _load(path: str, mtime: float, server: str) -> tuple[Activity, ...]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(parse(doc if isinstance(doc, dict) else {}, server))


@lru_cache(maxsize=4)
def _task_names(path: str, mtime: float) -> frozenset[str]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return frozenset(doc) if isinstance(doc, dict) else frozenset()
    except (OSError, ValueError):
        return frozenset()


def available() -> bool:
    return _mtime(activity_file()) is not None


def activities(server: str | None = None) -> list[Activity]:
    """文件里这个服务器的全部活动关（不管开没开），已对上 stage_table。没有文件 → []。"""
    p = activity_file()
    m = _mtime(p)
    if m is None:
        return []
    return [map_stages(a) for a in _load(str(p), m, server or _server())]


def _now() -> datetime:
    from maapilot.knowledge.calendar import GAME_TZ
    return datetime.now(GAME_TZ)


def _at(at) -> datetime:
    if at is None:
        return _now()
    if at.tzinfo is None:
        from maapilot.knowledge.calendar import GAME_TZ
        return at.replace(tzinfo=GAME_TZ)
    return at


def supported_values(a: Activity) -> frozenset[str]:
    """MAA 能导航的关：列出的 + tasks.json 里有 `<编号>@SideStoryStage` 导航任务的同前缀关。"""
    out = set(a.listed)
    tp = tasks_file()
    m = _mtime(tp)
    if m is not None:
        for name in _task_names(str(tp), m):
            code, _, tail = name.partition("@")
            if tail == "SideStoryStage" and code.split("-", 1)[0] in a.prefixes:
                out.add(code)
    return frozenset(out)


def _stage_rows() -> dict:
    from maapilot.knowledge import stages
    return stages._stage_table()


def _zone_activity() -> dict[str, str]:
    """zoneId → 活动名（activity_table）。"""
    from maapilot.knowledge import stages
    return stages._activity_names()


def _matches(a: Activity, s: dict) -> bool:
    """stage_table 的一条活动关是不是这期活动的：编号前缀一样，或所在区域的活动名一样。"""
    code = s.get("code") or ""
    return code.split("-", 1)[0] in a.prefixes or _zone_activity().get(s.get("zoneId")) == a.name


def map_stages(a: Activity) -> Activity:
    """填上 stages：这期活动里 MAA 能导航的关在 stage_table 里的 stageId（只要普通难度的那份）。"""
    try:
        rows = _stage_rows()
    except Exception:                                    # noqa: BLE001 - 没有数据表
        return a
    vals = supported_values(a)
    sids = tuple(sorted(sid for sid, s in rows.items()
                        if s.get("stageType") == "ACTIVITY" and s.get("difficulty") == "NORMAL" and "#" not in sid
                        and (s.get("code") or "") in vals and _matches(a, s)))
    return Activity(**{**a.__dict__, "stages": sids})


def current(at=None, server: str | None = None) -> list[Activity]:
    at = _at(at)
    return [a for a in activities(server) if a.open_at(at)]


def for_stage(s: dict, server: str | None = None) -> Activity | None:
    """一条 stage_table 活动关属于文件里的哪期活动（不管开没开；有多个取最近开始的）。"""
    hits = [a for a in activities(server) if _matches(a, s)]
    return max(hits, key=lambda a: a.start) if hits else None


def _short(t: datetime) -> str:
    return f"{t.month}月{t.day}日 {t:%H:%M}"


def why_not(s: dict, at=None, server: str | None = None) -> str:
    """活动关不能导航的原因（给人看）。"""
    at = _at(at)
    code = s.get("code") or s.get("stageId")
    name = _zone_activity().get(s.get("zoneId"))
    a = for_stage(s, server)
    if a is None:
        return f"MAA 还不认这个活动的关卡（MAA 的活动列表里还没有「{name or code.split('-', 1)[0]}」）"
    if at < a.start:
        return f"「{a.name}」还没开始（{_short(a.start)} 开始）"
    if at >= a.end:
        return f"「{a.name}」已经结束（{_short(a.end)}），MAA 不再导航"
    vals = sorted(v for v in supported_values(a) if not v.startswith(REOPEN_PREFIX))
    return f"MAA 只支持「{a.name}」的 {'、'.join(vals) or '（没有列出关卡）'}，{code} 不在里面"


class MaaEvents:
    """给 stages._nav / _event_hit 用的 events（鸭子类型）：current(at) → 带 .stages / .zone_ids / .reopen 的活动。"""

    def __init__(self, server: str | None = None):
        self.server = server

    def current(self, at=None) -> list[Activity]:
        return current(at, self.server)

    def why_not(self, s: dict, at=None) -> str:
        return why_not(s, at, self.server)


def events() -> MaaEvents | None:
    """有 MAA 活动列表文件 → MaaEvents；没有 → None。"""
    return MaaEvents() if available() else None
