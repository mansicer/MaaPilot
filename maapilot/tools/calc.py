"""`maapilot calc need / craft / stages`：纯计算，不碰模拟器，只给建议。契约见 docs/cli.md「calc」。

读 private/state/ 的快照（仓库、干员）做对照；快照时间放在输出里（depot_at、box_at），太旧由 agent 决定要不要先读一次。
"对象"的写法见 docs/cli.md「对象的写法」；`parse_object()` 也给 goals 用。
"""
from __future__ import annotations

import math
import re

from maapilot.cliutil import BAD_REQUEST, CliError
from maapilot.tools import common
from maapilot.tools.data import best_stages, items_out, resolve_item_or_fail, resolve_op_or_fail

DAILY_SANITY = 240          # 每天自然恢复的理智（6 分钟 1 点）

_ARROW = re.compile(r"\s*(?:→|->|=>)\s*")
_SEP = re.compile(r"\s*[，,、；;]\s*")
_ELITE_LV = re.compile(r"^精\s*([0-2])\s*(?:(\d{1,2})\s*级)?$")
_LEVEL = re.compile(r"^(\d{1,2})\s*级$")
_SKILL_LV = re.compile(r"^技能\s*([1-7])\s*级$")
_MASTERY = re.compile(r"^技能\s*([1-3])\s*专\s*([0-3])$")
_MODULE = re.compile(r"^模组\s*(\S+?)\s*([0-3])\s*级$")
_MATERIAL = re.compile(r"^材料\s+(.+?)\s*[×xX*]\s*(\d+)$")


def _bad(msg: str) -> CliError:
    return CliError("bad_object", msg, hint="写法见 docs/cli.md「对象的写法」，如 `干员 遥 → 精2 1级`", exit=BAD_REQUEST)


def parse_parts(text: str) -> dict:
    """"精2 60级，技能 7级，技能3 专3，模组X 2级" → {elite, level, skill_level, mastery: {k: m}, module: {T: n}}。"""
    out: dict = {"mastery": {}, "module": {}}
    for part in [p for p in _SEP.split(text.strip()) if p]:
        if m := _ELITE_LV.match(part):
            out["elite"], out["level"] = int(m.group(1)), int(m.group(2) or 1)
        elif m := _LEVEL.match(part):
            out["level"] = int(m.group(1))
        elif m := _SKILL_LV.match(part):
            out["skill_level"] = int(m.group(1))
        elif m := _MASTERY.match(part):
            out["mastery"][int(m.group(1))] = int(m.group(2))
        elif m := _MODULE.match(part):
            out["module"][m.group(1)] = int(m.group(2))
        else:
            raise _bad(f"看不懂「{part}」")
    return out


def parse_object(text: str) -> dict:
    """对象一行 → {"kind": 干员|材料|关卡|活动|其他, ...}。写法不对抛 CliError(bad_object)。"""
    t = (text or "").strip()
    if t.startswith("干员"):
        head, parts = _split_arrow(t[2:].strip())
        if not head or not parts:
            raise _bad(f"干员对象要写成 `干员 <名> → <练度>`：{t}")
        return {"kind": "干员", "operator": head, "target": parse_parts(parts)}
    if t.startswith("材料"):
        m = _MATERIAL.match(t)
        if not m:
            raise _bad(f"材料对象要写成 `材料 <名> × <数量>`：{t}")
        return {"kind": "材料", "item": m.group(1).strip(), "count": int(m.group(2))}
    for kind in ("关卡", "活动", "其他"):
        if t.startswith(kind):
            return {"kind": kind, "text": t[len(kind):].strip()}
    raise _bad(f"对象要以 干员 / 材料 / 关卡 / 活动 / 其他 开头：{t}")


def _split_arrow(s: str) -> tuple[str, str]:
    parts = _ARROW.split(s, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (s, "")


# --- 快照 ------------------------------------------------------------------------------

def depot_snapshot() -> tuple[dict, str | None]:
    snap = common.read_json(_state("depot")) or {}
    return dict(snap.get("items") or {}), snap.get("at")


def raw_box_snapshot() -> tuple[dict, str | None]:
    """box.json 原样（森空岛 / MAA 识别读的）。"""
    snap = common.read_json(_state("box")) or {}
    return dict(snap.get("operators") or {}), snap.get("at")


def box_snapshot() -> tuple[dict, str | None]:
    """干员快照，叠上现场练度（maapilot.tools.live_box：森空岛滞后时，我们刚在游戏里读到 / 做成的更高的练度）。
    用到现场读数的干员带 `live`。"""
    from maapilot.tools import live_box
    box, at = raw_box_snapshot()
    return live_box.overlay(box, at), at


def _state(kind: str):
    from maapilot import paths
    return paths.STATE / f"{kind}.json"


def _add(acc: dict, items: dict, times: int = 1) -> None:
    for k, v in items.items():
        acc[k] = acc.get(k, 0) + v * times


# --- need ------------------------------------------------------------------------------

def _current_from_box(name: str, target: dict, current: dict, owned: dict | None) -> dict:
    """--from 没给的技能等级、专精、模组，从干员快照补（森空岛读的快照才有；MAA 识别只有精英化、等级）。"""
    if not owned:
        return current
    cur = dict(current)
    cur["mastery"] = dict(current.get("mastery") or {})
    cur["module"] = dict(current.get("module") or {})
    if "skill_level" not in cur and owned.get("skill_level") is not None:
        cur["skill_level"] = int(owned["skill_level"])
    skills = owned.get("skills")
    for k in (target.get("mastery") or {}):
        sk = next((x for x in skills or [] if x.get("index") == k), None)
        if k not in cur["mastery"] and sk is not None and sk.get("mastery") is not None:
            cur["mastery"][k] = int(sk["mastery"])
    mods = owned.get("modules")
    if mods is not None:
        from maapilot.knowledge import gamedata as g
        for t in (target.get("module") or {}):
            if t in cur["module"]:
                continue
            try:
                mid = g.find_module(name, t).get("uniEquipId")
            except Exception:                            # noqa: BLE001 - 没有模组表、名字不对：留给下面报
                continue
            md = next((x for x in mods if x.get("id") == mid), None)
            cur["module"][t] = int((md or {}).get("level") or 0)
    return cur


def operator_need(name: str, target: dict, current: dict, box: dict) -> tuple[dict, dict, list[str]]:
    """(need {lmd, exp, items}, from {…}, assumed [...])。current（--from）没给的部分按干员快照 box 算。"""
    from maapilot.knowledge import gamedata as g
    op = resolve_op_or_fail(name)
    c = g.character(name)
    assumed: list[str] = []
    owned = box.get(name)
    current = _current_from_box(name, target, current, owned)
    cur_e = current.get("elite")
    cur_l = current.get("level")
    if cur_e is None:
        if owned:
            cur_e, cur_l = int(owned.get("elite") or 0), int(owned.get("level") or 1)
        else:
            cur_e, cur_l = 0, 1
            assumed.append("干员识别里没有这名干员：按精0 1级算")
    elif cur_l is None:
        cur_l = 1
    lmd = exp = 0
    items: dict[str, int] = {}
    tgt_e = target.get("elite", cur_e)
    tgt_l = target.get("level")
    if tgt_l is None:
        tgt_l = 1 if tgt_e > cur_e else cur_l
    # 前提：专精要精2；技能 5 级以上要精1；模组要精2 到一定等级。目标里没写到的练度一起算上
    for req_e, req_l, why in _prerequisites(name, c, target, current):
        if (tgt_e, tgt_l) < (req_e, req_l) and (cur_e, cur_l) < (req_e, req_l):
            tgt_e, tgt_l = req_e, req_l
            assumed.append(f"{why}：已算上练到精{req_e} {req_l}级")
    max_l = c["phases"][tgt_e]["maxLevel"] if tgt_e < len(c["phases"]) else None
    if max_l is None:
        raise _bad(f"{name} 没有精{tgt_e}")
    if tgt_l > max_l:
        raise _bad(f"{name} 精{tgt_e} 最高 {max_l} 级")
    if (tgt_e, tgt_l) > (cur_e, cur_l):
        up = g.upgrade_cost(name, cur_e, cur_l, tgt_e, tgt_l)
        lmd += up["lmd"]; exp += up["exp"]
        _add(items, up["items"])
    frm = {"elite": cur_e, "level": cur_l}
    sk_target = target.get("skill_level")
    sk_cur = current.get("skill_level")
    if target.get("mastery") and sk_target is None and sk_cur is None and frm["elite"] >= 1:
        sk_cur = 7
        assumed.append("技能等级不知道：专精的前提是 7 级，按已到 7 级算（不是的话用 --from \"技能 N级\" 给出）")
    if sk_target is not None or (target.get("mastery") and (sk_cur is None or sk_cur < 7)):
        to = sk_target if sk_target is not None else 7
        if sk_cur is None:
            sk_cur = 1
            assumed.append("技能等级不知道（干员识别读不到）：按 1 级算，可以用 --from \"技能 N级\" 给出")
        if to > sk_cur:
            _add(items, g.skill_level_cost(name, sk_cur, to)["items"])
        frm["skill_level"] = sk_cur
    for k, m in (target.get("mastery") or {}).items():
        if k > len(c.get("skills") or []):
            raise _bad(f"{name} 没有技能{k}")
        m_cur = (current.get("mastery") or {}).get(k)
        if m_cur is None:
            m_cur = 0
            assumed.append(f"技能{k} 专精不知道：按专0 算")
        if m > m_cur:
            _add(items, g.mastery_cost_range(name, k, m_cur, m)["items"])
        frm.setdefault("mastery", {})[k] = m_cur
    for t, n in (target.get("module") or {}).items():
        md_cur = (current.get("module") or {}).get(t)
        if md_cur is None:
            md_cur = 0
            assumed.append(f"模组{t} 等级不知道：按没解锁算")
        try:
            for lv in range(md_cur + 1, n + 1):
                mc = g.module_cost(name, t, lv)
                lmd += mc["lmd"]
                _add(items, mc["items"])
        except g.ModuleDataUnavailable:
            raise CliError("no_game_data", "没有模组数据表", hint="maapilot data sync", exit=BAD_REQUEST) from None
        except (g.UnknownNameError, ValueError) as e:
            raise _bad(f"{name} 的模组：{e}") from None
        frm.setdefault("module", {})[t] = md_cur
    lmd += items.pop(g.LMD_ID, 0)
    return {"lmd": lmd, "exp": exp, "items": items}, frm, assumed


def _prerequisites(name: str, c: dict, target: dict, current: dict) -> list[tuple[int, int, str]]:
    """目标里各项的练度前提 [(精英化, 等级, 说明)]。"""
    from maapilot.knowledge import gamedata as g
    out = []
    if target.get("mastery"):
        out.append((2, 1, "专精要先精2"))
    sk_t = target.get("skill_level") or (7 if target.get("mastery") else None)
    if sk_t:
        need = max(((g._phase(x["unlockCond"].get("phase")), int(x["unlockCond"].get("level") or 1))
                    for x in (c.get("allSkillLvlup") or [])[:sk_t - 1] if x.get("unlockCond")), default=(0, 1))
        if need > (0, 1):
            out.append((*need, f"技能 {sk_t} 级要先精{need[0]}"))
    for t in target.get("module") or {}:
        if (current.get("module") or {}).get(t):
            continue                                     # 已经解锁了
        try:
            u = g.module_unlock(g.find_module(name, t))
        except (g.ModuleDataUnavailable, g.UnknownNameError):
            continue                                     # 下面算模组花费时再报
        out.append((u["elite"], u["level"], f"模组{t} 要先精{u['elite']} {u['level']}级才能解锁"))
    return out


def _crafting(short: dict[str, int], stock: dict[str, int]) -> tuple[list[dict], dict[str, int]]:
    """缺的材料里哪些能用仓库现有材料合成；返回 (craftable, 合成之后还要刷的 {id: n})。"""
    from maapilot.knowledge import recipes
    rs = recipes.goal_recipes()
    stock = dict(stock)
    craftable, farm = [], {}
    for iid, n in short.items():
        if iid not in rs:
            farm[iid] = farm.get(iid, 0) + n
            continue
        plan = recipes.plan_crafts_ids({iid: n}, stock, recipes=rs)
        stock = plan["stock"]
        for k, v in plan["farm"].items():
            farm[k] = farm.get(k, 0) + v
        craftable.append({"id": iid, "name": _name(iid), "count": n, "fully": not plan["farm"],
                          "uses": items_out(plan["consume"]), "lmd": plan["lmd"], "missing": items_out(plan["farm"])})
    return craftable, farm


def _name(iid: str) -> str:
    from maapilot.knowledge.items import display_name
    return display_name(iid)


# --- 别的目标占着的材料 -----------------------------------------------------------------

def _same_object(a: str | None, b: str | None) -> bool:
    return re.sub(r"\s+", "", a or "") == re.sub(r"\s+", "", b or "")


def _reserved(box: dict, exclude_title: str | None = None, exclude_object: str | None = None
              ) -> tuple[dict, list[dict]]:
    """进行中的干员 / 材料目标（按 goals.md 的顺序）各自还要的材料：({id: {name, count, goals: [{title, priority, count}]}},
    skipped [{title, why}])。干员目标从快照里的当前练度算起；材料目标按写的数量。只用 operator_need，不递归 need()。"""
    from maapilot.flows import goals as goalsmod
    out: dict[str, dict] = {}
    skipped: list[dict] = []
    for gd in goalsmod.active_goals():
        title, obj_text = gd["title"], gd.get("object")
        if (exclude_title is not None and title == exclude_title) or (
                exclude_object is not None and _same_object(obj_text, exclude_object)):
            continue
        try:
            obj = parse_object(obj_text or "")
            if obj["kind"] == "材料":
                items = {resolve_item_or_fail(obj["item"]): obj["count"]}
            elif obj["kind"] == "干员":
                items = operator_need(obj["operator"], obj["target"], {}, box)[0]["items"]
            else:
                continue                                 # 关卡 / 活动 / 其他：不占材料
        except CliError as e:
            skipped.append({"title": title, "why": e.message})
            continue
        for iid, n in items.items():
            if n <= 0:
                continue
            r = out.setdefault(iid, {"name": _name(iid), "count": 0, "goals": []})
            r["count"] += n
            r["goals"].append({"title": title, "priority": gd.get("priority"), "count": n})
    return out, skipped


def reserved_by_goals(exclude_title: str | None = None, exclude_object: str | None = None) -> dict:
    """进行中的干员 / 材料目标还要用的材料（不含 exclude_* 那个目标）：{物品id: {name, count, goals: [{title, priority, count}]}}。
    算不了的目标（名字不对等）跳过。读一次干员快照。"""
    box, _ = box_snapshot()
    return _reserved(box, exclude_title, exclude_object)[0]


def need(obj_text: str, from_text: str | None = None, ignore_goals: bool = False,
         exclude_title: str | None = None) -> dict:
    """calc need 的函数版本。默认还对照别的进行中目标（reserved_by_goals）：
    reserved、conflicts、short_after_goals；short 仍只按仓库算。ignore_goals=True 不看别的目标。
    exclude_title：要算的就是这个目标（不算它自己占的）；没给就按对象写法相同来排除。"""
    from maapilot.knowledge.gamedata import EXP_ITEM_IDS, LMD_ID, exp_value
    obj = parse_object(obj_text)
    depot, depot_at = depot_snapshot()
    box, box_at = box_snapshot()
    assumed: list[str] = []
    if obj["kind"] in ("关卡", "活动", "其他"):
        raise CliError("not_computable", f"{obj['kind']}目标算不了材料，要自己分解（先看要哪些干员练度）",
                       exit=BAD_REQUEST, data={"object": obj_text})
    if obj["kind"] == "材料":
        iid = resolve_item_or_fail(obj["item"])
        needed = {"lmd": 0, "exp": 0, "items": {iid: obj["count"]}}
        frm = None
    else:
        current = parse_parts(from_text) if from_text else {}
        needed, frm, assumed = operator_need(obj["operator"], obj["target"], current, box)
        if not box_at and "elite" not in current:
            assumed.append("还没有干员识别的快照：先 state box 才准")
    if not depot_at:
        assumed.append("还没有仓库快照：按仓库是空的算（先 state depot）")
    have_items = {i: depot.get(i, 0) for i in needed["items"]}
    have = {"lmd": depot.get(LMD_ID, 0), "exp": exp_value(depot), "items": items_out_all(have_items)}
    short_items = {i: n - have_items[i] for i, n in needed["items"].items() if n > have_items[i]}
    short = {"lmd": max(0, needed["lmd"] - have["lmd"]), "exp": max(0, needed["exp"] - have["exp"]),
             "items": items_out(short_items)}
    stock = {k: v for k, v in depot.items() if k not in EXP_ITEM_IDS}
    for i in needed["items"]:                        # 目标本身要用的，不能再拿去合成别的
        stock[i] = max(0, stock.get(i, 0) - needed["items"][i])
    craftable, farm = _crafting(short_items, stock)
    out = {"object": obj_text, "from": frm, "assumed": assumed,
           "need": {"lmd": needed["lmd"], "exp": needed["exp"], "items": items_out(needed["items"])},
           "have": have, "short": short, "craftable": craftable, "farm": items_out(farm),
           "depot_at": depot_at, "box_at": box_at}
    if ignore_goals:
        return out
    reserved, skipped = _reserved(box, exclude_title, None if exclude_title is not None else obj_text)
    conflicts, after = [], {}
    for iid, n in needed["items"].items():
        r = reserved.get(iid)
        free = max(0, have_items[iid] - (r["count"] if r else 0))
        if n > free:
            after[iid] = n - free
            if r:                                        # 仓库够、被别的目标占了，或本来就不够又被占
                conflicts.append({"id": iid, "name": _name(iid), "need": n, "have": have_items[iid],
                                  "reserved_by_others": [{"goal": x["title"], "priority": x["priority"],
                                                          "count": x["count"]} for x in r["goals"]],
                                  "short_after_reserve": n - free})
    out.update(reserved=reserved, conflicts=conflicts,
               short_after_goals={"lmd": short["lmd"], "exp": short["exp"], "items": items_out(after)})
    if skipped:
        out["reserved_skipped"] = skipped
    return out


def items_out_all(items: dict) -> list[dict]:
    return [{"id": i, "name": _name(i), "count": n} for i, n in items.items()]


# --- craft ------------------------------------------------------------------------------

def _mood() -> dict[str, float]:
    """加工站每合成一次的心情消耗（building_data 的 apCost ÷ 360000）。"""
    import json
    from maapilot import paths
    try:
        bd = json.load(open(paths.DATA / "building_data.json", encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, float] = {}
    for f in bd.get("workshopFormulas", {}).values():
        iid = str(f["itemId"])
        out.setdefault(iid, int(f.get("apCost") or 0) / 360000)
    return out


def craft(item: str, count: int = 1) -> dict:
    from maapilot import config
    from maapilot.knowledge import recipes, shops
    iid = resolve_item_or_fail(item)
    if count < 1:
        raise CliError("bad_arguments", "--count 至少 1", exit=BAD_REQUEST)
    shop = {i: shops.as_recipe(o) for i, o in shops.offers(config.server().lower()).items()}
    rs = recipes.goal_recipes(shop)
    if iid not in rs:
        raise CliError("not_craftable", f"{_name(iid)} 不能合成，只能刷（maapilot calc stages {_name(iid)}）",
                       exit=BAD_REQUEST)
    depot, depot_at = depot_snapshot()
    plan = recipes.plan_crafts_ids({iid: count}, depot, extra=True, recipes=rs)
    mood = _mood()
    steps, total_mood = [], 0.0
    for i, times in plan["batches"]:
        r = rs[i]
        m = mood.get(i, 0.0) * times if r["station"] == "WORKSHOP" else 0.0
        total_mood += m
        steps.append({"item": _name(i), "id": i, "count": times * r["count"], "times": times, "station": r["station"],
                      "costs": items_out({k: v * times for k, v in r["costs"].items()}), "lmd": r["lmd"] * times,
                      **({"mood": m} if m else {})})
    return {"item": _name(iid), "count": count, "steps": steps, "lmd": plan["lmd"], "short": items_out(plan["farm"]),
            "spend": items_out(plan["spend"]), "mood": total_mood, "depot_at": depot_at,
            **({"assumed": ["还没有仓库快照：按仓库是空的算（先 state depot）"]} if not depot_at else {})}


# --- stages ------------------------------------------------------------------------------

def _parse_wanted(specs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in specs:
        name, _, n = s.partition("=")
        try:
            cnt = int(n) if n else 1
        except ValueError:
            raise CliError("bad_arguments", f"数量写错了：{s}（写成 物品=数量）", exit=BAD_REQUEST) from None
        iid = resolve_item_or_fail(name.strip())
        out[iid] = out.get(iid, 0) + max(1, cnt)
    return out


def stages(specs: list[str]) -> dict:
    from maapilot.tools.data import live
    wanted = _parse_wanted(specs)
    lv = live()
    per_item, plan, no_stage = [], {}, []
    for iid, n in wanted.items():
        cands = best_stages(iid, lv, limit=3)
        per_item.append({"id": iid, "name": _name(iid), "count": n, "stages": cands})
        best = next((c for c in cands if c.get("rate")), None)
        if best is None:
            no_stage.append({"id": iid, "name": _name(iid)})
            continue
        times = math.ceil(n / best["rate"])
        cur = plan.get(best["stage"])
        if cur is None or times > cur["times"]:
            plan[best["stage"]] = {"stage": best["stage"], "times": times, "ap": best["ap"],
                                   "open_days": best["open_days"], "open_today": best["open_today"],
                                   "for": sorted({*(cur or {}).get("for", []), _name(iid)})}
        else:
            cur["for"] = sorted({*cur["for"], _name(iid)})
    rows = []
    for p in plan.values():
        rows.append({**p, "sanity": p["times"] * p["ap"]})
    total = sum(r["sanity"] for r in rows)
    out = {"per_item": per_item, "plan": rows, "sanity_total": total,
           "days": round(total / DAILY_SANITY, 1), "daily_sanity": DAILY_SANITY,
           "note": "只是建议：按企鹅物流掉率挑每件期望理智最低的常驻关，没算副产物"}
    if no_stage:
        out["no_stage"] = no_stage
    if lv is None:
        out["note"] = "没有掉率缓存（maapilot data sync）：只列了主掉落关，算不了次数"
    return out


# --- 命令 ------------------------------------------------------------------------------

def cmd_need(args):
    return need(args.object, args.from_, ignore_goals=args.ignore_goals, exclude_title=args.goal)


def cmd_craft(args):
    return craft(args.item, args.count)


def cmd_stages(args):
    return stages(args.items)


# --- event ------------------------------------------------------------------------------

def _tier(iid: str, items: dict) -> int | None:
    r = str((items.get(iid) or {}).get("rarity") or "")
    m = re.search(r"(\d)", r)
    return int(m.group(1)) if m else None


def event_stages(at=None) -> dict:
    """正开着的活动（MAA 的活动列表）里每一关：理智、主要掉落和它的等级（T1–T5）、一图流价值、MAA 能不能刷。
    排序：MAA 能刷的在前 → 主要掉落等级高的在前 → 每点理智的价值高的在前。只给事实，刷哪关由 agent 定。"""
    from maapilot.knowledge import gamedata, maa_activity, stages
    from maapilot.tools.data import live
    items = gamedata._items()
    lv = live()
    values = (lv.values if lv is not None else None) or {}
    rows = stages._stage_table()
    out = []
    for a in maa_activity.current(at):
        sup = maa_activity.supported_values(a)
        st_out = []
        for sid, st in sorted(rows.items()):
            if not (st.get("stageType") == "ACTIVITY" and st.get("difficulty") == "NORMAL" and "#" not in sid
                    and maa_activity._matches(a, st)) or not st.get("apCost"):
                continue
            code = st.get("code") or sid
            drops = []
            for r in (st.get("stageDropInfo") or {}).get("displayDetailRewards") or []:
                iid = str(r.get("id"))
                if r.get("dropType") not in ("NORMAL", "MAIN", "SPECIAL", "ADDITIONAL") or iid not in items:
                    continue
                if (items[iid].get("classifyType") or "") != "MATERIAL":
                    continue                              # 活动代币、家具等不算材料
                drops.append({"id": iid, "name": items[iid].get("name"), "tier": _tier(iid, items),
                              "value": round(values[iid], 2) if iid in values else None})
            eff = lv.efficiency(sid) if lv is not None and sid in (lv.rates or {}) else None
            top = max((d["tier"] or 0 for d in drops), default=0)
            st_out.append({"code": code, "stage_id": sid, "ap": st.get("apCost"), "maa_can_fight": code in sup,
                           "drops": sorted(drops, key=lambda d: -(d["tier"] or 0)), "top_tier": top or None,
                           "value_per_ap": round(eff, 3) if eff is not None else None})
        st_out.sort(key=lambda r: (not r["maa_can_fight"], -(r["top_tier"] or 0), -(r["value_per_ap"] or 0), r["code"]))
        out.append({**a.to_dict(), "stages": st_out})
    return {"activities": out,
            "note": None if out else "MAA 的活动列表里现在没有开着的活动（或者 MAA 还没更新这期活动）"}


def cmd_event(args):
    return event_stages()


def register(sub) -> None:
    g = sub.add_parser("calc", help="纯计算：需求、合成、刷哪关（只给建议）")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("need", help="一个养成目标要多少、缺多少")
    p.add_argument("object", help='对象，如 "干员 遥 → 精2 1级"、"材料 聚酸酯组 × 10"')
    p.add_argument("--from", dest="from_", metavar="当前", help='当前练度，如 "技能 4级，技能3 专1"')
    p.add_argument("--ignore-goals", action="store_true", help="不对照别的进行中目标占着的材料")
    p.add_argument("--goal", metavar="标题", help="算的是这个目标（不算它自己占的；默认按对象写法相同排除）")
    p.set_defaults(func=cmd_need, command="calc need")
    p = gs.add_parser("craft", help="用仓库现有材料合成要做什么")
    p.add_argument("item")
    p.add_argument("--count", type=int, default=1)
    p.set_defaults(func=cmd_craft, command="calc craft")
    p = gs.add_parser("event", help="正开着的活动每关掉什么、材料等级、MAA 能不能刷（刷活动关时挑关用）")
    p.set_defaults(func=cmd_event, command="calc event")
    p = gs.add_parser("stages", help="缺的材料去哪刷最省理智")
    p.add_argument("items", nargs="+", metavar="物品[=数量]")
    p.set_defaults(func=cmd_stages, command="calc stages")
