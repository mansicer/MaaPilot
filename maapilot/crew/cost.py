"""Dry-run cost estimation for an upgrade plan, checked against MAA's Depot/OperBox JSON."""
from __future__ import annotations

import json
from maapilot import paths



def load_gamedata():
    ct = json.load(open(paths.DATA / "character_table.json", encoding="utf-8"))
    items = json.load(open(paths.DATA / "item_table.json", encoding="utf-8"))["items"]
    gc = json.load(open(paths.DATA / "gamedata_const.json", encoding="utf-8"))
    return ct, items, gc


def estimate(plan: dict, box: dict, depot: dict | None = None) -> dict:
    """plan: {name: {elite, level, skill}}; box: {name: {elite, level, rarity}} from OperBox."""
    ct, items, gc = load_gamedata()
    expmap, goldmap, evg = gc["characterExpMap"], gc["characterUpgradeCostMap"], gc["evolveGoldCost"]
    by_name = {c["name"]: c for c in ct.values()}
    iname = lambda i: items[i]["name"] if i in items else i
    total = {"exp": 0, "lmd": 0, "mats": {}, "per_op": []}
    for name, tgt in plan.items():
        c = by_name[name]
        r = int(c["rarity"][-1]) - 1
        o = box.get(name)
        if o is None:
            total["per_op"].append({"name": name, "error": "not owned"})
            continue
        e, l = o["elite"], o["level"]
        te, tl, ts = tgt.get("elite", e), tgt["level"], tgt.get("skill")
        exp = lmd = 0
        mats: dict[str, int] = {}
        while (e, l) < (te, tl):
            maxl = c["phases"][e]["maxLevel"]
            if e < te:
                for lv in range(l, maxl):
                    exp += expmap[e][lv - 1]; lmd += goldmap[e][lv - 1]
                for m in c["phases"][e + 1]["evolveCost"] or []:
                    mats[iname(m["id"])] = mats.get(iname(m["id"]), 0) + m["count"]
                lmd += evg[r][e]; e += 1; l = 1
            else:
                for lv in range(l, tl):
                    exp += expmap[e][lv - 1]; lmd += goldmap[e][lv - 1]
                l = tl
        if ts:
            # current skill rank is not known from OperBox; assume rank 1 (upper bound)
            for lv in c["allSkillLvlup"][: max(0, ts - 1)]:
                for m in lv["lvlUpCost"] or []:
                    mats[iname(m["id"])] = mats.get(iname(m["id"]), 0) + m["count"]
        total["exp"] += exp; total["lmd"] += lmd
        for k, v in mats.items():
            total["mats"][k] = total["mats"].get(k, 0) + v
        total["per_op"].append({"name": name, "from": f"精{o['elite']}Lv{o['level']}", "to": f"精{te}Lv{tl}",
                                "skill": ts, "exp": exp, "lmd": lmd, "mats": mats})
    if depot:
        have_exp = (depot.get("基础作战记录", 0) * 200 + depot.get("初级作战记录", 0) * 400
                    + depot.get("中级作战记录", 0) * 1000 + depot.get("高级作战记录", 0) * 2000)
        total["stock"] = {"exp": have_exp, "lmd": depot.get("龙门币", 0)}
        total["short"] = {k: v - depot.get(k, 0) for k, v in total["mats"].items() if depot.get(k, 0) < v}
        if have_exp < total["exp"]:
            total["short"]["exp"] = total["exp"] - have_exp
        if depot.get("龙门币", 0) < total["lmd"]:
            total["short"]["龙门币"] = total["lmd"] - depot.get("龙门币", 0)
    return total
