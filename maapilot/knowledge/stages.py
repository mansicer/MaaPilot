"""关卡知识（见 docs/concepts/knowledge.md）：stageId 是主键，StageRef 把"用户怎么写 / 表里怎么存 / MAA 怎么导航"三层对上。
数据来自 data/stage_table.json + zone_table.json（活动名另从 activity_table.json 取，缺表时只认区域名）。
资源本开放日从 zone_table.weeklyAdditionInfo 派生，不再硬编码。只读，不碰模拟器。

导航白名单（nav_kind / maa_value）：
- main：主线 / 别传（MAA 支持全部主线编号；第 9 章起标准/磨难分档时加 -NORMAL / -HARD 后缀）
- resource：CE-6 LS-6 CA-5 AP-5 SK-5；chip：PR-A/B/C/D-1/2
- annihilation：常驻剿灭三图（ANNIHILATION_MAA_VALUES）；当期剿灭在 MAA 里写 "Annihilation"
- ss_current / ss_reopen：活动关只有在给定的 `events` 里当前开放时才可导航（值为完整编号）
- not_navigable：教程 TR-x、突袭 #f# / 六星 #s、简单模式 easy_、保全 LT-x、其他一切
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from maapilot import paths


class UnknownStageError(KeyError):
    """写法不对应任何关卡。str(e) 含原文。"""


class AmbiguousStageError(LookupError):
    """一个编号对应多个关卡（例如每期保全都有 LT-1），且没有当前开放的活动能定夺。消息列出候选。"""


ANNIHILATION_MAA_VALUES: dict[str, str] = {
    "龙门外环": "LungmenOutskirts@Annihilation",
    "龙门市区": "LungmenDowntown@Annihilation",
    "切尔诺伯格": "Chernobog@Annihilation",
}
ANNIHILATION_CURRENT = "Annihilation"

RESOURCE_STAGES = frozenset({"CE-6", "LS-6", "CA-5", "AP-5", "SK-5"})
CHIP_STAGES = frozenset({f"PR-{c}-{n}" for c in "ABCD" for n in (1, 2)})
NAV_KINDS = ("main", "resource", "chip", "annihilation", "ss_current", "ss_reopen", "not_navigable")

# 一个编号有多份时的优先级：先普通难度、再 diffGroup、再无 '#' 后缀
_DIFF_GROUP_RANK = {"NONE": 0, "NORMAL": 1, "ALL": 2, "TOUGH": 3, "EASY": 4}


@dataclass(frozen=True)
class StageRef:
    stage_id: str
    code: str
    name: str
    zone_id: str
    difficulty: str            # NORMAL / FOUR_STAR(突袭) / SIX_STAR
    ap_cost: int
    maa_value: str | None
    nav_kind: str

    @property
    def navigable(self) -> bool:
        return self.nav_kind != "not_navigable"


# --- 表 ----------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _stage_table() -> dict:
    return json.load(open(paths.DATA / "stage_table.json", encoding="utf-8"))["stages"]


@lru_cache(maxsize=1)
def _zone_table() -> dict:
    return json.load(open(paths.DATA / "zone_table.json", encoding="utf-8"))


def _zones() -> dict:
    return _zone_table()["zones"]


@lru_cache(maxsize=1)
def _activity_names() -> dict[str, str]:
    """zoneId -> 活动中文名（activity_table.zoneToActivity + basicInfo）；缺表返回 {}。"""
    p = paths.DATA / "activity_table.json"
    if not p.exists():
        return {}
    at = json.load(open(p, encoding="utf-8"))
    info = at.get("basicInfo", {})
    return {z: info[a]["name"] for z, a in at.get("zoneToActivity", {}).items() if a in info and info[a].get("name")}


def _chapter(code: str) -> str:
    return code.split("-", 1)[0]


@lru_cache(maxsize=1)
def _tough_chapters() -> frozenset[str]:
    """有磨难难度的主线章节号（"10"…）。"""
    return frozenset(_chapter(s.get("code") or "") for s in _stage_table().values()
                     if s.get("stageType") in ("MAIN", "SUB") and s.get("diffGroup") == "TOUGH")


def _is_primary(s: dict) -> bool:
    """同一编号的多份里，"正常"那份：普通难度、非简单/磨难、非突袭/六星变体。"""
    sid = s["stageId"]
    return (s.get("difficulty") == "NORMAL" and "#" not in sid
            and not sid.startswith(("easy_", "tough_")))


def _sort_key(s: dict):
    return (s.get("difficulty") != "NORMAL", _DIFF_GROUP_RANK.get(s.get("diffGroup"), 9), "#" in s["stageId"])


@lru_cache(maxsize=1)
def _by_code() -> dict[str, list[dict]]:
    """编号 -> 候选列表（按优先级排序）。CAMPAIGN（剿灭）按 name 索引，因为它的 code 是国家名。"""
    out: dict[str, list[dict]] = {}
    for s in _stage_table().values():
        key = s.get("name") if s.get("stageType") == "CAMPAIGN" else s.get("code")
        if not key:
            continue
        out.setdefault(key, []).append(s)
    for lst in out.values():
        lst.sort(key=_sort_key)
    return out


@lru_cache(maxsize=1)
def _zone_names() -> dict[str, list[str]]:
    """区域 id -> 可用来称呼它的名字（zoneNameSecond / zoneNameFirst / 活动名）。"""
    out: dict[str, list[str]] = {}
    acts = _activity_names()
    for zid, z in _zones().items():
        names = [n for n in (z.get("zoneNameSecond"), z.get("zoneNameFirst"), acts.get(zid)) if n]
        out[zid] = names
    return out


def zone_label(zone_id: str) -> str:
    """给人看的区域/活动名；实在没有名字时退回区域 id。"""
    names = _zone_names().get(zone_id) or []
    return names[0] if names else zone_id


# --- 导航 --------------------------------------------------------------------------

def _event_hit(s: dict, events, at):
    """`s` 是否属于 events.current(at) 里的某个活动；返回该活动或 None。鸭子类型：只看 .stages / .zone_ids。"""
    if events is None:
        return None
    try:
        current = events.current(at) if at is not None else events.current()
    except TypeError:
        current = events.current()
    for ev in current or []:
        if s["stageId"] in (getattr(ev, "stages", None) or []) or s["zoneId"] in (getattr(ev, "zone_ids", None) or []):
            return ev
    return None


def _nav(s: dict, events=None, at=None) -> tuple[str, str | None]:
    sid, code, typ = s["stageId"], s.get("code") or "", s.get("stageType")
    if typ == "CAMPAIGN":
        v = ANNIHILATION_MAA_VALUES.get(s.get("name", ""))
        return ("annihilation", v) if v else ("not_navigable", None)
    if (s.get("difficulty") != "NORMAL" or "#" in sid or sid.startswith("easy_") or code.startswith("TR-")
            or typ in ("GUIDE", "SPECIAL_STORY", "CLIMB_TOWER")):
        return "not_navigable", None                      # 突袭/六星/简单模式/教程；磨难（tough_）走 -HARD
    if typ in ("MAIN", "SUB"):
        grp = s.get("diffGroup")
        if grp == "NORMAL":                               # MAA 只在有磨难的章节认 -NORMAL（第 9 章只有简单 / 标准，带了会报错）
            return "main", f"{code}-NORMAL" if _chapter(code) in _tough_chapters() else code
        if grp == "TOUGH":
            return "main", f"{code}-HARD"
        return "main", code
    if typ == "DAILY":
        if code in RESOURCE_STAGES:
            return "resource", code
        if code in CHIP_STAGES:
            return "chip", code
        return "not_navigable", None
    if typ == "ACTIVITY":
        ev = _event_hit(s, events, at)
        if ev is None:
            return "not_navigable", None
        reopen = bool(getattr(ev, "reopen", False)) or getattr(ev, "kind", None) == "reopen"
        return ("ss_reopen" if reopen else "ss_current"), code
    return "not_navigable", None


def _ref(s: dict, events=None, at=None) -> StageRef:
    kind, value = _nav(s, events, at)
    return StageRef(stage_id=s["stageId"], code=s.get("code") or "", name=s.get("name") or "",
                    zone_id=s["zoneId"], difficulty=s.get("difficulty") or "NORMAL",
                    ap_cost=int(s.get("apCost") or 0), maa_value=value, nav_kind=kind)


# --- 解析 --------------------------------------------------------------------------

def _candidates_for_code(code: str, zone_filter=None) -> list[dict]:
    lst = _by_code().get(code) or []
    if zone_filter is not None:
        lst = [s for s in lst if zone_filter(s["zoneId"])]
    return lst


def _pick(code: str, cands: list[dict], events=None, at=None) -> dict:
    primary = [s for s in cands if _is_primary(s)] or cands
    zones = {s["zoneId"] for s in primary}
    if len(primary) == 1 or len(zones) == 1:
        return primary[0]
    if events is not None:                                      # 优先当前开放的活动
        by_stage = [s for s in primary if (ev := _event_hit(s, events, at)) is not None
                    and s["stageId"] in (getattr(ev, "stages", None) or [])]
        if len(by_stage) == 1:
            return by_stage[0]
        by_zone = [s for s in primary if _event_hit(s, events, at) is not None]
        if len(by_zone) == 1:
            return by_zone[0]
        if by_zone:
            primary = by_zone
    listing = "、".join(f"{s['stageId']}（{zone_label(s['zoneId'])}）" for s in primary[:12])
    raise AmbiguousStageError(f"{code} 对应多个关卡，请写成 活动名/{code}：{listing}")


def resolve_stage(text: str, events=None, at=None) -> StageRef:
    """接受 stageId（main_01-07、main_01-07#f#）、编号（1-7、AP-5、PR-C-2）、`活动名/编号`、剿灭中文名（龙门外环）。
    编号重名时优先 `events`（Events 对象，鸭子类型）里 `at` 时刻开放的活动，仍有多个抛 AmbiguousStageError。"""
    key = (text or "").strip()
    if not key:
        raise UnknownStageError(text)
    st = _stage_table()
    if key in st:
        return _ref(st[key], events, at)
    if "/" in key:
        prefix, _, code = key.rpartition("/")
        prefix = prefix.strip(); code = code.strip()
        zones = {zid for zid, names in _zone_names().items() if prefix in names}
        cands = _candidates_for_code(code, lambda z: z in zones) if zones else []
        if not cands:
            raise UnknownStageError(key)
        return _ref(_pick(code, cands, events, at), events, at)
    cands = _candidates_for_code(key)
    if not cands:
        raise UnknownStageError(key)
    return _ref(_pick(key, cands, events, at), events, at)


def stage(text: str) -> dict:
    """stage_table 原始条目（编号或 stageId）。"""
    return _stage_table()[resolve_stage(text).stage_id]


def display(stage_id: str) -> str:
    """给人看的编号；编号在多个区域重名时附区域/活动名。"""
    s = _stage_table().get(stage_id)
    if s is None:
        return stage_id
    if s.get("stageType") == "CAMPAIGN":
        return s.get("name") or stage_id
    code = s.get("code") or stage_id
    siblings = [c for c in _by_code().get(code, []) if _is_primary(c)]
    if len({c["zoneId"] for c in siblings}) > 1:
        return f"{code}（{zone_label(s['zoneId'])}）"
    return code


# --- 开放日 / 消耗 / 区域 ------------------------------------------------------------

def sanity_cost(text: str) -> int:
    return resolve_stage(text).ap_cost


def open_weekdays(text: str) -> frozenset[int] | None:
    """ISO 星期（1=周一…7=周日）集合；每天开放（或表里没有周期信息）返回 None。接受 stageId 或编号。"""
    ref = resolve_stage(text)
    info = _zone_table().get("weeklyAdditionInfo", {}).get(ref.zone_id)
    if not info or not info.get("daysOfWeek"):
        return None
    days = frozenset(int(d) for d in info["daysOfWeek"])
    return None if days >= frozenset(range(1, 8)) else days


def is_open_on(text: str, weekday: int) -> bool:
    days = open_weekdays(text)
    return days is None or weekday in days


def owning_zone(text: str) -> dict:
    """zone_table 条目（章节 / 活动 / 周常轮换）。"""
    ref = resolve_stage(text)
    z = _zones().get(ref.zone_id)
    if z is None:
        raise UnknownStageError(ref.zone_id)
    return z


# --- 主线章节（通关记录扫描用，D136） ------------------------------------------------------

_MAIN_ID = re.compile(r"^main_(\d{2})-\d{2}$")
_SCAN_GROUPS = frozenset({None, "NONE", "NORMAL", "ALL"})       # 标准环境看得到的（简单 / 磨难是另一份）


@lru_cache(maxsize=1)
def _episode_zones() -> dict[int, str]:
    """章节号 → 区域 id：main_NN-MM 最多落在哪个区域（第 15 章起的区域 id 不是 main_15，按关卡找）。"""
    votes: dict[int, Counter] = {}
    for sid, s in _stage_table().items():
        m = _MAIN_ID.match(sid)
        if m:
            votes.setdefault(int(m.group(1)), Counter())[s["zoneId"]] += 1
    return {ep: c.most_common(1)[0][0] for ep, c in sorted(votes.items())}


def main_episodes() -> list[int]:
    """数据表里的主线章节号（0 起），升序。"""
    return list(_episode_zones())


def _is_scan_stage(s: dict) -> bool:
    """章节地图上的作战关（标准环境）：主线 / 支线（S2-1 这类）的普通难度；剧情关、教程、突袭 / 险地、简单 / 磨难不算。"""
    sid = s["stageId"]
    return (s.get("stageType") in ("MAIN", "SUB") and s.get("difficulty") == "NORMAL" and "#" not in sid
            and sid.startswith(("main_", "sub_")) and not s.get("isStoryOnly")
            and s.get("diffGroup") in _SCAN_GROUPS)


def episode_stages(ep: int) -> list[StageRef]:
    """第 `ep` 章地图上要读通关记录的作战关，按数据表顺序。没有这一章抛 UnknownStageError。"""
    zone = _episode_zones().get(ep)
    if zone is None:
        raise UnknownStageError(f"没有第 {ep} 章")
    return [_ref(s) for s in _stage_table().values() if s["zoneId"] == zone and _is_scan_stage(s)]


def episode_titles(ep: int, *, story: bool = False) -> dict[str, str]:
    """第 `ep` 章地图上会打开关卡详情的关（作战关、教程、H 关；不含简单 / 磨难的另一份）的 {编号: 关卡名}：核对详情页标题用（D136）。
    剧情关弹的是另一种框，默认不含；`story=True` 连剧情关一起（认地图上的关卡名时要知道全部编号）。"""
    zone = _episode_zones().get(ep)
    if zone is None:
        raise UnknownStageError(f"没有第 {ep} 章")
    out: dict[str, str] = {}
    for s in _stage_table().values():
        sid = s["stageId"]
        if (s["zoneId"] != zone or s.get("difficulty") != "NORMAL" or "#" in sid
                or sid.startswith(("easy_", "tough_")) or not s.get("code") or (s.get("isStoryOnly") and not story)):
            continue
        out.setdefault(s["code"], s.get("name") or "")
    return out


def unlock_prereqs(stage_id: str) -> list[str]:
    """数据表 unlockCondition 里要先通关（PASS / COMPLETE）的关卡 stageId：前置没打过，这关在地图上根本不出现（D136）。"""
    s = _stage_table().get(stage_id) or {}
    return [c["stageId"] for c in s.get("unlockCondition") or [] if c.get("stageId")]
