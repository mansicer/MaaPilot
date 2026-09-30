"""配方知识（见 docs/concepts/knowledge.md）：从 data/building_data.json 的 workshopFormulas（加工站）+ manufactFormulas（制造站）生成，
`maapilot data build-recipes` 写成 data/recipes.json（仓库不再手工维护；键是物品 id）。

recipe(id_or_name) -> {item_id, name, station: WORKSHOP|MANUFACTURE, room_level, count, lmd, costs: {id: n},
                      extra_outcome_rate, extra_outcome_group: [{item_id, count, weight}], formula_type, tier}
不可合成返回 None；obtain(item_id) 给主掉落关（stage_table.stageDropInfo，dropType NORMAL/MAIN 的常驻关），
用来区分"基础材料，去刷"和"表里没收录"。只读，不碰模拟器。
"""
from __future__ import annotations

import json
from functools import lru_cache

from maapilot import paths
from maapilot.knowledge.gamedata import UnknownNameError, _items
from maapilot.knowledge.items import display_name, resolve_item

STATIONS = {"workshopFormulas": "WORKSHOP", "manufactFormulas": "MANUFACTURE"}
# 加工站的 F_ASC 是芯片换职业（芯片组 + 芯片助剂 -> 另一职业芯片组），不是"合成"：排除，芯片组仍算刷本获取
_EXCLUDED = {("WORKSHOP", "F_ASC")}
_MAIN_DROP_TYPES = frozenset({"NORMAL", "MAIN"})
_OBTAIN_STAGE_TYPES = frozenset({"MAIN", "SUB", "DAILY"})


def _tier(item_id: str) -> int | None:
    it = _items().get(item_id)
    if not it or not str(it.get("rarity", "")).startswith("TIER_"):
        return None
    return int(it["rarity"].split("_")[1])


def build_from_building_data() -> dict[str, dict]:
    """building_data.json -> {item_id: recipe}。同一物品有多条配方时保留房间等级要求最低的那条。"""
    bd = json.load(open(paths.DATA / "building_data.json", encoding="utf-8"))
    out: dict[str, dict] = {}
    for table, station in STATIONS.items():
        for f in bd.get(table, {}).values():
            item_id = str(f["itemId"])
            if (station, f.get("formulaType")) in _EXCLUDED:
                continue
            rooms = f.get("requireRooms") or []
            room_level = min((int(r.get("roomLevel") or 1) for r in rooms), default=1)
            rec = {
                "item_id": item_id,
                "name": display_name(item_id),
                "station": station,
                "room_level": room_level,
                "count": int(f.get("count") or 1),
                "lmd": int(f.get("goldCost") or 0),
                "costs": {str(c["id"]): int(c["count"]) for c in (f.get("costs") or [])},
                "extra_outcome_rate": float(f.get("extraOutcomeRate") or 0.0),
                "extra_outcome_group": [{"item_id": str(g["itemId"]), "count": int(g.get("itemCount") or 1),
                                         "weight": int(g.get("weight") or 0)} for g in (f.get("extraOutcomeGroup") or [])],
                "formula_type": f.get("formulaType"),
                "tier": _tier(item_id),
            }
            cur = out.get(item_id)
            if cur is None or rec["room_level"] < cur["room_level"]:
                out[item_id] = rec
    return dict(sorted(out.items()))


def write_recipes_json() -> "paths.Path":
    """生成并写 data/recipes.json；返回路径。"""
    built = build_from_building_data()
    p = paths.DATA / "recipes.json"
    p.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    _recipes.cache_clear()
    return p


@lru_cache(maxsize=1)
def _recipes() -> dict[str, dict]:
    p = paths.DATA / "recipes.json"
    if p.exists():
        data = json.load(open(p, encoding="utf-8"))
        if data and all(isinstance(v, dict) and "station" in v for v in data.values()):
            return data
    return build_from_building_data()               # 没生成过（或旧格式）：直接从 building_data 算


def recipe(id_or_name: str) -> dict | None:
    """配方；不可合成（基础材料、芯片组、卷3 等）返回 None；完全不认识的物品抛 UnknownNameError。"""
    return _recipes().get(resolve_item(id_or_name))


def is_craftable(id_or_name: str) -> bool:
    return recipe(id_or_name) is not None


@lru_cache(maxsize=1)
def _drops() -> dict[str, list[str]]:
    """item_id -> 常驻主掉落关 stageId 列表（stage_table 顺序）。"""
    st = json.load(open(paths.DATA / "stage_table.json", encoding="utf-8"))["stages"]
    out: dict[str, list[str]] = {}
    for sid, s in st.items():
        if s.get("stageType") not in _OBTAIN_STAGE_TYPES or "#" in sid or sid.startswith(("easy_", "tough_")):
            continue
        for r in (s.get("stageDropInfo") or {}).get("displayDetailRewards") or []:
            if r.get("dropType") in _MAIN_DROP_TYPES and r.get("type") in ("MATERIAL", "CARD_EXP", "GOLD"):
                out.setdefault(str(r["id"]), []).append(sid)
    return out


def obtain(id_or_name: str) -> list[str]:
    """主掉落关 stageId 列表；空列表 = 认识这个物品但没有常驻掉落关；不认识抛 UnknownNameError。"""
    return list(_drops().get(resolve_item(id_or_name), []))


def tree(id_or_name: str) -> dict:
    """递归配方树。节点：{item_id, name, craftable, need(每单位需要的数量，根为 1), costs: [子节点], leaves: [...]}
    leaves 汇总所有不可合成的叶子（去重）：[{item_id, name, craftable: False, obtain: [stageId]}]。"""
    root_id = resolve_item(id_or_name)
    leaves: dict[str, dict] = {}

    def node(item_id: str, need: int, seen: tuple[str, ...]) -> dict:
        r = _recipes().get(item_id)
        if r is None or item_id in seen:
            leaf = {"item_id": item_id, "name": display_name(item_id), "craftable": False, "need": need,
                    "obtain": obtain(item_id)}
            leaves.setdefault(item_id, {k: v for k, v in leaf.items() if k != "need"})
            return leaf
        return {"item_id": item_id, "name": r["name"], "craftable": True, "need": need, "station": r["station"],
                "room_level": r["room_level"], "yield": r["count"], "lmd": r["lmd"],
                "costs": [node(ing, n, seen + (item_id,)) for ing, n in r["costs"].items()]}

    t = node(root_id, 1, ())
    t["leaves"] = list(leaves.values())
    return t


def workshop_by_name(formula_types: tuple[str, ...] = ("F_EVOLVE",)) -> dict[str, dict]:
    """给 maapilot.crew.craft（OCR 驱动的加工站 UI）用的中文名视图：{名: {count, lmd, tier, costs: {名: n}}}。
    默认只含精英材料配方（F_EVOLVE），因为 craft.py 只会进 精英材料 分页。"""
    return {r["name"]: {"id": iid, "tier": r["tier"], "count": r["count"], "lmd": r["lmd"],
                        "costs": {display_name(i): n for i, n in r["costs"].items()}}
            for iid, r in _recipes().items() if r["station"] == "WORKSHOP" and r["formula_type"] in formula_types}


def workshop_by_id(formula_types: tuple[str, ...] = ("F_EVOLVE",)) -> dict[str, dict]:
    """`workshop_by_name` 的物品 id 版本：{物品 id: 配方}，同样只含加工站的精英材料配方（craft 只会进这个分页）。"""
    return {iid: r for iid, r in _recipes().items()
            if r["station"] == "WORKSHOP" and r["formula_type"] in formula_types}


def manufacture_by_id() -> dict[str, dict]:
    """制造站里养成要用的配方：只有双芯片（2 × 芯片组 + 1 × 芯片助剂，制造站 3 级）。
    制造站的作战记录、赤金、源石碎片不算：它们不耗材料、靠时间产出，当成"合成"会让规划器以为经验和龙门币能凭空做出来。"""
    return {iid: r for iid, r in _recipes().items() if r["station"] == "MANUFACTURE" and r["formula_type"] == "F_ASC"}


@lru_cache(maxsize=1)
def _manufacture_seconds() -> dict[str, int]:
    bd = json.load(open(paths.DATA / "building_data.json", encoding="utf-8"))
    return {str(f["itemId"]): int(f.get("costPoint") or 0) for f in bd.get("manufactFormulas", {}).values()
            if f.get("formulaType") == "F_ASC"}


def manufacture_seconds(item_id: str) -> int | None:
    """制造站造一个的基准耗时（秒，`manufactFormulas.costPoint`，生产力 100%）；不是制造站双芯片 → None。
    双芯片现在是 1 秒（"相见欢"版本起），所以一次运行里就能造完、收取、改回原产物（D135）。"""
    return _manufacture_seconds().get(str(item_id))


def goal_recipes(shop_recipes: dict[str, dict] | None = None) -> dict[str, dict]:
    """养成目标补材料时认的全部配方（D135）：加工站精英材料 + 制造站双芯片 + 商店兑换（`shops.as_recipe`，station SHOP）。
    同一物品既能合成又能兑换时用合成（兑换花凭证，只在合成不了时用）。"""
    out = dict(shop_recipes or {})
    out.update(manufacture_by_id())
    out.update(workshop_by_id())
    return out


def plan_crafts_ids(wanted: dict[str, int], stock: dict[str, int], extra: bool = False, *,
                    recipes: dict[str, dict] | None = None, caps: dict[str, int] | None = None,
                    unknown=frozenset()) -> dict:
    """把想要的 {物品 id: 数量} 按配方树对库存求解（纯函数，不碰模拟器；maapilot.crew.craft.plan_crafts 是它的中文名包装）。

    返回 {"batches": [(物品 id, 次数)], "lmd": int, "consume": {id: n}, "farm": {id: n}, "stock": {id: n},
    "buys": {id: 件数}, "spend": {货币 id: n}, "assumed": {id: n}}：
    batches 按"原料先合成"排序（兑换也在里面，原料先兑换）；lmd 是全部批次的龙门币；consume 是从库存里用掉的（含合成出的余量，照旧算法记账）；
    farm 是合成也补不上、要去刷的缺口；stock 是做完这些合成、扣掉 wanted 之后剩下的库存（规划器的虚拟库存用它）。
    extra=True 时 wanted 是"在现有库存之外新做这么多"。

    recipes 省略时只认加工站精英材料配方（与游戏内合成界面一致）；传 `goal_recipes()` 时另认制造站双芯片与商店兑换（station SHOP）：
    buys 是要兑换的件数，spend 是兑换花的货币；caps 是有限购的商品本期还能兑换多少（超出的算进 farm，即缺口）；
    unknown 是数量读不到的物品（例如识别不到的凭证余额）：要用时按够算、不扣库存，记进 assumed，由执行时读真值再定。"""
    recipes = workshop_by_id() if recipes is None else recipes
    caps = dict(caps or {})
    stock = dict(stock)
    batches: dict[str, int] = {}
    farm: dict[str, int] = {}
    consume: dict[str, int] = {}
    assumed: dict[str, int] = {}
    order: list[str] = []

    def need(item: str, n: int) -> None:
        have = stock.get(item, 0)
        use = min(have, n)
        if use:
            stock[item] = have - use
            consume[item] = consume.get(item, 0) + use
        short = n - use
        if short <= 0:
            return
        if item in unknown:
            assumed[item] = assumed.get(item, 0) + short
            return
        r = recipes.get(item)
        if r is None:
            farm[item] = farm.get(item, 0) + short
            return
        times = -(-short // r["count"])
        if item in caps:                                  # 限购：本期剩下的次数用完，其余是缺口
            ok = min(times, caps[item])
            caps[item] -= ok
            if ok < times:
                farm[item] = farm.get(item, 0) + (times - ok) * r["count"]
                short = ok * r["count"]
            times = ok
            if times <= 0:
                return
        for ing, cnt in r["costs"].items():
            need(ing, cnt * times)
        batches[item] = batches.get(item, 0) + times
        if item in order:
            order.remove(item)
        order.append(item)          # 排在它的原料之后
        stock[item] = stock.get(item, 0) + times * r["count"] - short

    for item, n in wanted.items():
        if extra:
            saved, stock[item] = stock.get(item, 0), 0
            need(item, n)
            stock[item] += saved
        else:
            need(item, n)
    lmd = sum(recipes[i]["lmd"] * t for i, t in batches.items())
    buys = {i: t * recipes[i]["count"] for i, t in batches.items() if recipes[i].get("station") == "SHOP"}
    spend: dict[str, int] = {}
    for i in buys:
        for cur, price in recipes[i]["costs"].items():
            spend[cur] = spend.get(cur, 0) + price * batches[i]
    return {"batches": [(i, batches[i]) for i in order], "lmd": lmd, "consume": consume, "farm": farm,
            "stock": {k: v for k, v in stock.items() if v}, "buys": buys, "spend": spend, "assumed": assumed}
