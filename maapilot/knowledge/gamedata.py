"""Lazily-loaded, read-only character/item game data: lookups by Chinese name, and the upgrade-cost
math (elite/level/skill/mastery/module, plus 作战记录→exp conversion). Mirrors the logic `maapilot.crew.cost`
and `maapilot.crew.mastery` already use to read `character_table.json` / `gamedata_const.json`, factored out
here so both the CLI's dry-run estimates and `goals/progress.py` (M1, another module) can share it.
Never touches the emulator.
"""
from __future__ import annotations

import json
from functools import lru_cache

from maapilot import paths


class UnknownNameError(KeyError):
    """A Chinese character/item name that isn't in the game data tables. str(e) includes the name."""


class ModuleDataUnavailable(LookupError):
    """Module (模组) unlock costs live in data/uniequip_table.json; raised when that table is absent
    (run `maapilot data sync`)."""


# 作战记录 -> exp，按 item id；EXP_ITEMS 保留中文名键给旧调用方
EXP_ITEM_IDS = {"2001": 200, "2002": 400, "2003": 1000, "2004": 2000}
EXP_ITEMS = {"基础作战记录": 200, "初级作战记录": 400, "中级作战记录": 1000, "高级作战记录": 2000}
LMD_ID = "4001"


@lru_cache(maxsize=1)
def _character_table() -> dict:
    return json.load(open(paths.DATA / "character_table.json", encoding="utf-8"))


@lru_cache(maxsize=1)
def _items() -> dict:
    return json.load(open(paths.DATA / "item_table.json", encoding="utf-8"))["items"]


@lru_cache(maxsize=1)
def _const() -> dict:
    return json.load(open(paths.DATA / "gamedata_const.json", encoding="utf-8"))


@lru_cache(maxsize=1)
def _uniequip() -> dict | None:
    p = paths.DATA / "uniequip_table.json"
    if not p.exists():
        return None
    return json.load(open(p, encoding="utf-8"))


@lru_cache(maxsize=1)
def skill_names() -> dict[str, str]:
    """{skill_id: 技能名}（data/skill_table.json；没有这张表就是空的）。"""
    p = paths.DATA / "skill_table.json"
    if not p.exists():
        return {}
    table = json.load(open(p, encoding="utf-8"))
    return {sid: s["levels"][0]["name"] for sid, s in table.items() if s.get("levels")}


@lru_cache(maxsize=1)
def _by_name() -> dict[str, dict]:
    return {c["name"]: c for c in _character_table().values()}


@lru_cache(maxsize=1)
def _items_by_name() -> dict[str, dict]:
    return {i["name"]: i for i in _items().values()}


@lru_cache(maxsize=1)
def operators() -> dict[str, dict]:
    """Chinese name -> {id, rarity, profession, max_levels, skills} for every operator (no tokens/traps);
    a name shared by several ids keeps the last one in the table, like `character`."""
    return {c["name"]: {"id": cid, "rarity": _rarity_index(c) + 1, "profession": c["profession"],
                        "max_levels": [p["maxLevel"] for p in c["phases"]], "skills": len(c["skills"])}
            for cid, c in _character_table().items()
            if cid.startswith("char_") and c["profession"] not in ("TOKEN", "TRAP")}


def character(name: str) -> dict:
    """character_table entry for `name` (Chinese). Raises UnknownNameError(name) if not found."""
    c = _by_name().get(name)
    if c is None:
        raise UnknownNameError(name)
    return c


def item(name: str) -> dict:
    """item_table entry for `name` (Chinese). Raises UnknownNameError(name) if not found."""
    i = _items_by_name().get(name)
    if i is None:
        raise UnknownNameError(name)
    return i


def item_name(item_id: str) -> str:
    """item_table id -> Chinese name; ids item_table doesn't know (e.g. 龙门币ID edge cases) pass through."""
    items = _items()
    return items[item_id]["name"] if item_id in items else item_id


def _add_costs(items: dict[str, int], costs: list | None) -> None:
    """Fold a character_table cost list [{id, count}] into an id-keyed {id: n} dict."""
    for m in costs or []:
        iid = str(m["id"])
        items[iid] = items.get(iid, 0) + int(m["count"])


def _rarity_index(c: dict) -> int:
    return int(c["rarity"][-1]) - 1                    # "TIER_4" -> 3


def level_cost(name: str, elite: int, from_level: int, to_level: int) -> dict:
    """exp/lmd to level from (elite, from_level) to (elite, to_level) within one elite phase."""
    expmap, goldmap = _const()["characterExpMap"][elite], _const()["characterUpgradeCostMap"][elite]
    exp = sum(expmap[lv - 1] for lv in range(from_level, to_level))
    lmd = sum(goldmap[lv - 1] for lv in range(from_level, to_level))
    return {"exp": exp, "lmd": lmd}


def elite_cost(name: str, elite: int) -> dict:
    """Material + lmd cost of promoting elite-1 -> elite (elite is 1 or 2; a single step)."""
    if elite not in (1, 2):
        raise ValueError(f"elite must be 1 or 2, got {elite}")
    c = character(name)
    lmd = _const()["evolveGoldCost"][_rarity_index(c)][elite - 1]
    items: dict[str, int] = {}
    _add_costs(items, c["phases"][elite]["evolveCost"])
    return {"lmd": lmd, "items": items}


def upgrade_cost(name: str, from_elite: int, from_level: int, to_elite: int, to_level: int) -> dict:
    """Cumulative exp/lmd/items from (from_elite, from_level) to (to_elite, to_level), promoting
    through intermediate elite phases as needed (mirrors maapilot.crew.cost.estimate's per-operator loop)."""
    c = character(name)
    e, l = from_elite, from_level
    exp = lmd = 0
    items: dict[str, int] = {}
    while (e, l) < (to_elite, to_level):
        maxl = c["phases"][e]["maxLevel"]
        if e < to_elite:
            step = level_cost(name, e, l, maxl)
            exp += step["exp"]; lmd += step["lmd"]
            ec = elite_cost(name, e + 1)
            lmd += ec["lmd"]
            for k, v in ec["items"].items():
                items[k] = items.get(k, 0) + v
            e, l = e + 1, 1
        else:
            step = level_cost(name, e, l, to_level)
            exp += step["exp"]; lmd += step["lmd"]
            l = to_level
    return {"exp": exp, "lmd": lmd, "items": items}


def skill_level_cost(name: str, from_level: int, to_level: int) -> dict:
    """Material cost {"items": {id: n}} to raise ALL skills from from_level to to_level (1-7; shared across
    an operator's skills, from `allSkillLvlup` — the game does not have per-skill skill-level costs)."""
    c = character(name)
    items: dict[str, int] = {}
    for step in c["allSkillLvlup"][from_level - 1: to_level - 1]:
        _add_costs(items, step["lvlUpCost"])
    return {"items": items}


def mastery_cost(name: str, skill: int, rank: int) -> dict:
    """{"items": {id: n}, "hours"} of a single mastery rank step (rank 1-3) of the skill-th (1-based) skill;
    `hours` is the 训练室 time (lvlUpTime / 3600; 24 for a 6★'s M3)."""
    c = character(name)
    cond = c["skills"][skill - 1]["levelUpCostCond"][rank - 1]
    items: dict[str, int] = {}
    _add_costs(items, cond["levelUpCost"])
    return {"items": items, "hours": int(cond.get("lvlUpTime") or 0) / 3600}


def mastery_cost_range(name: str, skill: int, from_rank: int, to_rank: int) -> dict:
    """Cumulative {"items": {id: n}, "hours"} from_rank -> to_rank (0-3) of one skill's mastery."""
    items: dict[str, int] = {}
    hours = 0.0
    for rank in range(from_rank + 1, to_rank + 1):
        step = mastery_cost(name, skill, rank)
        hours += step["hours"]
        for k, v in step["items"].items():
            items[k] = items.get(k, 0) + v
    return {"items": items, "hours": hours}


def modules_of(name: str) -> list[dict]:
    """uniequip_table entries of an operator's real modules (the ORIGINAL 初始模组 is skipped).
    Raises ModuleDataUnavailable when data/uniequip_table.json is absent."""
    ue = _uniequip()
    if ue is None:
        raise ModuleDataUnavailable(f"{name}: module cost data unavailable (no data/uniequip_table.json; run `maapilot data sync`)")
    c = character(name)
    char_id = next(k for k, v in _character_table().items() if v is c)
    eq = ue["equipDict"]
    return [eq[m] for m in ue.get("charEquip", {}).get(char_id, []) if m in eq and eq[m].get("itemCost")]


def _phase(v) -> int:
    """"PHASE_2" / 2 → 2。"""
    try:
        return int(str(v).rsplit("_", 1)[-1])
    except (TypeError, ValueError):
        return 2


def find_module(name: str, module: str) -> dict:
    """干员的一个模组条目（类型字母 X / Y / D / Δ … 或全名）；没有 → UnknownNameError。"""
    key = str(module).strip().upper()
    hit = next((m for m in modules_of(name) if (m.get("typeName2") or "").upper() == key
                or m.get("uniEquipName") == module or m.get("uniEquipId") == module), None)
    if hit is None:
        raise UnknownNameError(f"{name} 模组 {module}")
    return hit


def module_unlock(entry: dict) -> dict:
    """模组的解锁条件：{"elite", "level"}（uniequip_table 的 unlockEvolvePhase / unlockLevel）。"""
    return {"elite": _phase(entry.get("unlockEvolvePhase", "PHASE_2")), "level": int(entry.get("unlockLevel") or 1)}


def module_missions(entry: dict) -> list[dict]:
    """模组任务：[{desc, stage_id?}]（uniequip_table 的 missionList，按 uniEquipMissionSort）。"""
    ue = _uniequip() or {}
    ms = [ue.get("missionList", {}).get(mid) for mid in entry.get("missionList") or []]
    ms = sorted((m for m in ms if m), key=lambda m: m.get("uniEquipMissionSort") or 0)
    return [{"desc": m.get("desc"), **({"stage_id": m["jumpStageId"]} if m.get("jumpStageId") else {})} for m in ms]


def module_cost(name: str, module: str, stage: int) -> dict:
    """{"lmd", "items": {id: n}} of one module (模组) stage (1-3). `module` is the type letter shown in game
    (X / Y / D / Δ …, uniequip typeName2) or the module's full name. ModuleDataUnavailable without the table;
    UnknownNameError when the operator has no such module; ValueError for a stage the module doesn't have."""
    hit = find_module(name, module)
    costs = (hit.get("itemCost") or {}).get(str(stage))
    if costs is None:
        raise ValueError(f"{name} 模组 {module} 没有第 {stage} 级（有：{sorted(hit.get('itemCost') or {})}）")
    items: dict[str, int] = {}
    _add_costs(items, costs)
    lmd = items.pop(LMD_ID, 0)
    return {"lmd": lmd, "items": items}


def exp_value(items: dict[str, int]) -> int:
    """Total exp represented by 作战记录 in a depot-like {item id or 中文名: count} mapping."""
    return (sum(items.get(k, 0) * v for k, v in EXP_ITEM_IDS.items())
            + sum(items.get(k, 0) * v for k, v in EXP_ITEMS.items()))
