"""`maapilot data sync / operator / item / recipe / stage`：游戏数据（不碰模拟器）。契约见 docs/cli.md「data」。

数据表在 data/（ArknightsGameData 的官方表 + 企鹅物流掉率、一图流物品价值的缓存），`data sync` 联网更新。
查询只读本地文件；名字认不出退出码 2（unknown_name，data.suggestions 给相近的名字）。
"""
from __future__ import annotations

import difflib

from maapilot import config
from maapilot.cliutil import BAD_REQUEST, ENVIRONMENT, FAILED, CliError

WEEKDAYS = "一二三四五六日"


# --- 公共：名字、数据表 --------------------------------------------------------------------

def require_tables() -> None:
    from maapilot.knowledge import datasync
    try:
        missing = datasync.missing_tables()
    except Exception:
        missing = ["?"]
    if missing:
        raise CliError("no_game_data", f"游戏数据表不全（缺 {', '.join(missing)}）", exit=ENVIRONMENT,
                       hint="bin/maapilot data sync")


def _suggest(name: str, pool) -> list[str]:
    pool = list(pool)
    hits = [p for p in pool if name and (name in p or p in name)][:5]
    return list(dict.fromkeys(hits + difflib.get_close_matches(name, pool, n=5, cutoff=0.4)))[:6]


def resolve_item_or_fail(name: str) -> str:
    require_tables()
    from maapilot.knowledge.gamedata import UnknownNameError, _items_by_name
    from maapilot.knowledge.items import resolve_item
    try:
        return resolve_item(name)
    except UnknownNameError:
        raise CliError("unknown_name", f"认不出物品「{name}」", exit=BAD_REQUEST,
                       data={"suggestions": _suggest(name, _items_by_name())}) from None


def resolve_op_or_fail(name: str) -> dict:
    """干员中文名 → operators() 里的条目（加上 name）。"""
    require_tables()
    from maapilot.knowledge.gamedata import operators
    ops = operators()
    if name not in ops:
        raise CliError("unknown_name", f"认不出干员「{name}」", exit=BAD_REQUEST,
                       data={"suggestions": _suggest(name, ops)})
    return {"name": name, **ops[name]}


def resolve_stage_or_fail(code: str):
    require_tables()
    from maapilot.knowledge import stages
    try:
        return stages.resolve_stage(code)
    except stages.AmbiguousStageError as e:
        raise CliError("ambiguous_stage", str(e), exit=BAD_REQUEST) from None
    except stages.UnknownStageError:
        raise CliError("unknown_name", f"认不出关卡「{code}」", exit=BAD_REQUEST,
                       data={"suggestions": _suggest(code, stages._by_code())}) from None


def items_out(items: dict) -> list[dict]:
    from maapilot.knowledge.items import display_name
    return [{"id": i, "name": display_name(i), "count": n} for i, n in items.items() if n]


def live():
    """企鹅物流掉率（按 config 的服务器）；没有缓存 → None。"""
    try:
        from maapilot.knowledge import live as lv
        return lv.load(config.penguin_server())
    except Exception:
        return None


def open_info(stage_id: str) -> dict:
    from maapilot.knowledge import calendar, stages
    days = stages.open_weekdays(stage_id)
    if days is None:
        return {"open_days": "每天", "open_today": True}
    return {"open_days": "周" + "、".join(WEEKDAYS[d - 1] for d in sorted(days)),
            "open_today": calendar.game_weekday() in days}


def best_stages(item_id: str, lv=None, limit: int = 5) -> list[dict]:
    """这件物品掉得最好的几关（常驻、能导航的关）：每件期望理智 = 理智 ÷ 每次期望掉落。"""
    from maapilot.knowledge import stages
    lv = lv if lv is not None else live()
    out = []
    if lv is not None:
        for sid, rates in lv.rates.items():
            rate = rates.get(item_id)
            ap = lv.ap.get(sid)
            if not rate or not ap:
                continue
            try:
                ref = stages.resolve_stage(sid)
            except Exception:
                continue
            if ref.nav_kind not in ("main", "resource", "chip"):
                continue
            out.append({"stage": ref.code, "stage_id": sid, "ap": ap, "rate": round(rate, 4),
                        "sanity_per_item": round(ap / rate, 1), "samples": lv.samples.get(sid, {}).get(item_id),
                        **open_info(sid)})
        out.sort(key=lambda r: r["sanity_per_item"])
        return out[:limit]
    from maapilot.knowledge.recipes import obtain       # 没有掉率缓存：只给主掉落关
    for sid in obtain(item_id)[:limit]:
        ref = stages.resolve_stage(sid)
        out.append({"stage": ref.code, "stage_id": sid, "ap": ref.ap_cost, "rate": None, "sanity_per_item": None,
                    **open_info(sid)})
    return out


# --- 命令 ----------------------------------------------------------------------------

def cmd_sync(args):
    from maapilot.knowledge import datasync
    try:
        r = datasync.sync(check_only=args.check, server=config.penguin_server())
    except Exception as e:
        raise CliError("sync_failed", f"同步失败（多半是网络）：{type(e).__name__}: {e}", exit=FAILED,
                       hint="稍后再试；本地已有的数据照常能用") from None
    out = {"updated": r.get("updated", []), "to_update": r.get("to_update", []), "missing": r.get("missing", []),
           "recipes_rebuilt": r.get("recipes_rebuilt")}
    lv = r.get("live")
    if isinstance(lv, dict):
        out["live"] = lv
    out["ages"] = datasync.live_ages(config.penguin_server())
    return out


def cmd_operator(args):
    from maapilot.knowledge import gamedata as g
    op = resolve_op_or_fail(args.name)
    name = op["name"]
    c = g.character(name)
    elites = []
    for e in range(1, len(c["phases"])):
        cost = g.elite_cost(name, e)
        elites.append({"elite": e, "lmd": cost["lmd"], "items": items_out(cost["items"]),
                       "needs_level": c["phases"][e - 1]["maxLevel"]})
    levels = [{"elite": e, "max_level": p["maxLevel"],
               "to_max": g.level_cost(name, e, 1, p["maxLevel"])} for e, p in enumerate(c["phases"])]
    skill_levels = [{"to": lv + 1, "items": items_out(g.skill_level_cost(name, lv, lv + 1)["items"])}
                    for lv in range(1, 1 + len(c.get("allSkillLvlup") or []))]
    masteries = []
    for k, sk in enumerate(c.get("skills") or [], start=1):
        ranks = []
        for m in range(1, 1 + len(sk.get("levelUpCostCond") or [])):
            mc = g.mastery_cost(name, k, m)
            ranks.append({"rank": m, "items": items_out(mc["items"]), "hours": mc["hours"]})
        masteries.append({"skill": k, "skill_id": sk.get("skillId"), "ranks": ranks})
    modules = []
    try:
        for m in g.modules_of(name):
            stages_ = []
            for st in sorted(m.get("itemCost") or {}, key=int):
                mc = g.module_cost(name, m.get("typeName2") or m["uniEquipId"], int(st))
                stages_.append({"level": int(st), "lmd": mc["lmd"], "items": items_out(mc["items"])})
            missions = []
            for ms in g.module_missions(m):
                row = {"desc": ms["desc"]}
                if ms.get("stage_id"):
                    row["stage"] = _stage_code(ms["stage_id"])
                missions.append(row)
            modules.append({"type": m.get("typeName2"), "name": m.get("uniEquipName"), "unlock": g.module_unlock(m),
                            "missions": missions, "levels": stages_})
    except g.ModuleDataUnavailable:
        modules = None
    return {"name": name, "id": op["id"], "rarity": op["rarity"], "profession": op["profession"],
            "levels": levels, "elite": elites, "skill_levels": skill_levels, "mastery": masteries, "modules": modules}


def _stage_code(stage_id: str) -> str:
    """stage_table 的关卡 id → 关卡号（如 main_02-05 → 2-5）；认不出原样返回。"""
    try:
        import json
        from maapilot import paths
        st = json.loads((paths.DATA / "stage_table.json").read_text(encoding="utf-8"))
        return (st.get("stages") or {}).get(stage_id, {}).get("code") or stage_id
    except (OSError, ValueError, AttributeError):
        return stage_id


def cmd_item(args):
    from maapilot.knowledge import gamedata, recipes
    iid = resolve_item_or_fail(args.name)
    it = gamedata._items()[iid]
    r = recipes.recipe(iid)
    return {"id": iid, "name": it["name"], "rarity": it.get("rarity"), "type": it.get("itemType"),
            "craftable": r is not None,
            "recipe": None if r is None else {"station": r["station"], "count": r["count"], "lmd": r["lmd"],
                                              "costs": items_out(r["costs"])},
            "stages": best_stages(iid), "drops_source": "penguin" if live() is not None else "stage_table"}


def cmd_recipe(args):
    from maapilot.knowledge import recipes, shops
    iid = resolve_item_or_fail(args.name)
    tree = recipes.tree(iid)
    offer = shops.offers(_shop_server()).get(iid)
    return {"tree": tree, "shop": None if offer is None else {**offer.to_dict(), "currency_name": _name(offer.currency)}}


def _shop_server() -> str:
    return config.server().lower()


def _name(iid: str) -> str:
    from maapilot.knowledge.items import display_name
    return display_name(iid)


def cmd_stage(args):
    from maapilot.knowledge import stages
    ref = resolve_stage_or_fail(args.code)
    lv = live()
    drops = []
    if lv is not None:
        for d in lv.drops(ref.stage_id):
            drops.append({"id": d.item_id, "name": _name(d.item_id), "rate": round(d.rate, 4), "samples": d.samples})
        drops.sort(key=lambda d: -d["rate"])
    st = stages._stage_table().get(ref.stage_id, {})
    main = [{"id": str(r["id"]), "name": _name(str(r["id"])), "type": r.get("dropType")}
            for r in (st.get("stageDropInfo") or {}).get("displayDetailRewards") or []
            if r.get("dropType") in ("NORMAL", "MAIN", "SPECIAL", "ADDITIONAL")]
    return {"code": ref.code, "stage_id": ref.stage_id, "name": ref.name, "ap": ref.ap_cost,
            "navigable": ref.navigable, "kind": ref.nav_kind, **open_info(ref.stage_id),
            "drops": drops, "listed_drops": main}


def register(sub) -> None:
    g = sub.add_parser("data", help="游戏数据：同步与查询")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("sync", help="同步数据表、刷新掉率（联网）")
    p.add_argument("--check", action="store_true", help="只看要不要更新")
    p.set_defaults(func=cmd_sync, command="data sync")
    p = gs.add_parser("operator", aliases=["op"], help="干员的养成消耗")
    p.add_argument("name")
    p.set_defaults(func=cmd_operator, command="data operator")
    p = gs.add_parser("item", aliases=["drops"], help="物品：配方、掉落最好的关")
    p.add_argument("name")
    p.set_defaults(func=cmd_item, command="data item")
    p = gs.add_parser("recipe", help="合成树")
    p.add_argument("name")
    p.set_defaults(func=cmd_recipe, command="data recipe")
    p = gs.add_parser("stage", help="关卡：理智、掉落、开放日")
    p.add_argument("code")
    p.set_defaults(func=cmd_stage, command="data stage")
