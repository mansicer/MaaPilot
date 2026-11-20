"""`maapilot copilot find / show / strip`：查 MAA 作业站（prts.maa.plus）的作业，对照自己的干员练度（不碰模拟器、不拿锁）。

- find：关卡 → stageId（`stages.resolve_stage`，认不出就当关键词）→ 作业站按热度查，逐个作业标出每个干员有没有、差多少练度。
- show：一份作业的说明、干员、可选位、动作数，标注同上。
- strip：把作业去掉所有练度要求（requirements）存到 private/cache/copilot/<编号>_noreq.json（`maa copilot --ignore-requirements`
  还被 MAA 拒绝时才用）。

练度来自 private/state/box.json：森空岛来源有技能等级、专精、模组；MAA 识别来源只有精英化、等级、潜能，读不到的项算"不知道"，不算差距。
只列事实（缺谁、差多少），不判断"打不打得过"。"差距 0"不等于满足要求：没核对到的（读不到的项、作业没写要求的干员）列在
`unchecked`，`meets` 只有人都有、没有差距、也没有没核对到的才是 true。

作业里 requirements 的写法（MAA 作业协议）：skill_level 1–7 是技能等级，8–10 是专精 1–3；module 1 χ、2 γ、3 α、4 Δ，0 / -1 不要求；
potentiality 从 1 数。联网走 `http_get_json`，测试里换成假的。契约见 docs/cli.md「copilot」。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, FAILED, CliError
from maapilot.tools import common

API = "https://prts.maa.plus"
USER_AGENT = "maapilot-copilot"
TIMEOUT_S = 20
MODULE_TYPES = {1: "X", 2: "Y", 3: "A", 4: "D"}            # requirements.module → uniequip_table 的 typeName2
MODULE_LABEL = {"X": "χ", "Y": "γ", "A": "α", "D": "Δ"}


# --- 联网（测试里换掉） ----------------------------------------------------------------------

def http_get_json(url: str) -> dict:
    """GET 一个 JSON。网络错误抛 OSError；作业站的 4xx 也带 JSON（{status_code, message}），照样返回。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except ValueError:
            raise OSError(f"HTTP {e.code}") from None


def _api(path: str, **params) -> dict:
    url = f"{API}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    try:
        d = http_get_json(url)
    except (OSError, ValueError) as e:
        raise CliError("network", f"连不上作业站：{type(e).__name__}: {e}", hint="检查网络后再试", exit=FAILED) from None
    if not isinstance(d, dict):
        raise CliError("network", "作业站的回应看不懂", exit=FAILED)
    return d


def query(keyword: str, limit: int) -> list[dict]:
    d = _api("/copilot/query", levelKeyword=keyword, order_by="hot", limit=limit, page=1)
    if d.get("status_code") not in (None, 200):
        raise CliError("network", f"作业站报错：{d.get('message')}", exit=FAILED)
    return list(((d.get("data") or {}).get("data")) or [])


def get(plan_id: int) -> dict:
    d = _api(f"/copilot/get/{plan_id}")
    if d.get("status_code") == 404 or not d.get("data"):
        raise CliError("not_found", f"作业站没有作业 {plan_id}：{d.get('message') or ''}".rstrip("："),
                       hint="编号写对了吗？用 copilot find <关卡> 找", exit=BAD_REQUEST)
    if d.get("status_code") not in (None, 200):
        raise CliError("network", f"作业站报错：{d.get('message')}", exit=FAILED)
    return d["data"]


def content(plan: dict) -> dict:
    c = plan.get("content")
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except ValueError:
            c = None
    return c if isinstance(c, dict) else {}


# --- 练度 ------------------------------------------------------------------------------------

def load_box() -> dict | None:
    """box.json 的 operators（名 → 练度）；没有 → None。"""
    d = common.read_json(paths.STATE / "box.json")
    ops = (d or {}).get("operators") if isinstance(d, dict) else None
    return ops if isinstance(ops, dict) else None


def _module_types() -> dict:
    """uniequip id → 类型字母（X/Y/A/D）；没有 uniequip_table → {}。"""
    try:
        from maapilot.knowledge import gamedata
        ue = gamedata._uniequip() or {}
    except Exception:                                    # noqa: BLE001 - 没有数据表时模组算不知道
        return {}
    return {k: v.get("typeName2") for k, v in (ue.get("equipDict") or {}).items() if v.get("typeName2")}


def _module_unlocked(m: dict, op: dict) -> bool | None:
    """森空岛把没解锁的模组也列出来（等级 1），只能推断：有 locked 字段就照它；没精2 → 没有；
    等级 ≥2 或正装着 → 有；精2、等级 1、没装着 → 不知道。"""
    if isinstance(m.get("locked"), bool):
        return not m["locked"]
    if (op.get("elite") or 0) < 2:
        return False
    if (m.get("level") or 0) >= 2 or m.get("equipped"):
        return True
    return None


def current(op: dict | None, skill: int | None, mtypes: dict) -> dict | None:
    """box 里一个干员 → {elite, level, potential, skill_level, mastery, modules}；读不到的项是 None。"""
    if op is None:
        return None
    skills = op.get("skills")
    mastery = None
    if isinstance(skills, list) and skill:
        s = next((x for x in skills if x.get("index") == skill), None)
        mastery = None if s is None else s.get("mastery")
    mods = op.get("modules")
    modules = None
    if isinstance(mods, list) and (mtypes or not mods):
        modules = [{"type": MODULE_LABEL.get(mtypes.get(m.get("id")), mtypes.get(m.get("id"))),
                    "level": m.get("level"), "unlocked": _module_unlocked(m, op)}
                   for m in mods if mtypes.get(m.get("id"))]
    return {"elite": op.get("elite"), "level": op.get("level"), "potential": op.get("potential"),
            "skill_level": op.get("skill_level"), "mastery": mastery, "modules": modules}


def gaps(req: dict, skill: int | None, op: dict | None, cur: dict | None, mtypes: dict) -> list[str]:
    """作业要求与现有练度的差距（给人看的中文）。读不到的项不算差距。"""
    if cur is None:
        return ["没有这个干员"]
    req = req or {}
    out = []
    e, lv = int(req.get("elite") or 0), int(req.get("level") or 0)
    ce, cl = cur.get("elite"), cur.get("level")
    if (e or lv) and ce is not None and ((ce, cl or 0) < (e, lv) if lv else ce < e):
        need = f"精{e} {lv}级" if lv else f"精{e}"
        out.append(f"要{need}，现在精{ce} {cl}级" if cl is not None else f"要{need}，现在精{ce}")
    sl = int(req.get("skill_level") or 0)
    csl = cur.get("skill_level")
    skills = op.get("skills") if op else None
    if sl >= 8 and skill:
        want = sl - 7
        if isinstance(skills, list) and not any(x.get("index") == skill for x in skills):
            if not out:                                   # 精英化不够时上面已经说了
                out.append(f"要技能{skill} 专{want}，现在还没有技能{skill}")
        else:
            if csl is not None and csl < 7:
                out.append(f"要技能等级 7，现在 {csl}")
            m = cur.get("mastery")
            if m is not None and m < want:
                out.append(f"要技能{skill} 专{want}，现在专{m}")
    elif sl and csl is not None and csl < sl:
        out.append(f"要技能等级 {sl}，现在 {csl}")
    mt = MODULE_TYPES.get(int(req.get("module") or 0))
    mods = cur.get("modules")
    if mt and mods is not None and mtypes:
        label = MODULE_LABEL[mt]
        states = [x.get("unlocked") for x in mods if x.get("type") == label]
        if True not in states and None not in states:
            out.append(f"要{label}模组，现在没有")
    pot, cp = int(req.get("potentiality") or 0), cur.get("potential")
    if pot > 1 and cp is not None and cp < pot:
        out.append(f"要潜能 {pot}，现在 {cp}")
    return out


def annotate_oper(o: dict, box: dict | None, mtypes: dict) -> dict:
    name, skill = o.get("name"), o.get("skill")
    req = o.get("requirements") or {}
    out = {"name": name, "skill": skill, "required": req or None}
    if box is None:
        return {**out, "have": None, "current": None, "gaps": [], "unknown": []}
    op = box.get(name)
    cur = current(op, skill, mtypes)
    return {**out, "have": op is not None, "current": cur, "gaps": gaps(req, skill, op, cur, mtypes),
            "unknown": unknowns(req, cur, mtypes, op, skill)}


def has_requirements(req: dict | None) -> bool:
    """作业真写了练度要求（全写 0、模组 -1、潜能 1 都等于没写）。"""
    if not req:
        return False
    return bool(int(req.get("elite") or 0) or int(req.get("level") or 0) or int(req.get("skill_level") or 0)
                or int(req.get("module") or 0) > 0 or int(req.get("potentiality") or 0) > 1)


def unknowns(req: dict, cur: dict | None, mtypes: dict, op: dict | None = None, skill: int | None = None) -> list[str]:
    """作业有要求、但练度里读不出来、没法对照的项（不算差距）。"""
    if cur is None or not req:
        return []
    out = []
    sl = int(req.get("skill_level") or 0)
    if sl and cur.get("skill_level") is None:
        out.append("技能等级")
    skills = op.get("skills") if op else None
    lacks_skill = isinstance(skills, list) and bool(skill) and not any(x.get("index") == skill for x in skills)
    if sl >= 8 and cur.get("mastery") is None and not lacks_skill:   # 还没有这个技能：差距里已经说了
        out.append("专精")
    mt = MODULE_TYPES.get(int(req.get("module") or 0))
    if mt:
        mods = cur.get("modules")
        states = [x.get("unlocked") for x in mods or [] if x.get("type") == MODULE_LABEL[mt]]
        if mods is None or not mtypes or (None in states and True not in states):
            out.append(f"{MODULE_LABEL[mt]}模组")
    return out


def annotate(plan: dict, box: dict | None, mtypes: dict | None = None) -> dict:
    """作业站的一条作业 → 输出的样子（干员逐个标注、可选位、缺谁、差距数）。"""
    mtypes = _module_types() if mtypes is None else mtypes
    c = content(plan)
    opers = [annotate_oper(o, box, mtypes) for o in c.get("opers") or []]
    groups = []
    for g in c.get("groups") or []:
        gops = [annotate_oper(o, box, mtypes) for o in g.get("opers") or []]
        owned = [o for o in gops if o["have"]]
        # 差距一样时挑读不到的少、作业写了要求的：能核对的人在前
        best = min(owned, key=lambda o: (len(o["gaps"]), len(o["unknown"]), not has_requirements(o["required"])),
                   default=None)
        groups.append({"name": g.get("name"), "opers": gops,
                       "have": None if box is None else bool(owned),
                       "best": best and best["name"], "gaps": len(best["gaps"]) if best else None,
                       "unknown": best["unknown"] if best else []})
    missing = [o["name"] for o in opers if o["have"] is False]
    missing += [f"{g['name']}（任选：{'/'.join(o['name'] for o in g['opers'])}）" for g in groups if g["have"] is False]
    if box is None:
        gap_count, all_owned, unknown_count, unchecked, meets = None, None, None, [], None
    else:
        gap_count = sum(len(o["gaps"]) for o in opers if o["have"]) + sum(g["gaps"] or 0 for g in groups)
        all_owned = not missing
        # 用到的人：固定干员里有的，加每个可选位里挑出来的那名
        used = [o for o in opers if o["have"]]
        used += [next(o for o in g["opers"] if o["name"] == g["best"]) for g in groups if g["best"]]
        unknown_count = sum(len(o["unknown"]) for o in used)
        unchecked = [f"{o['name']}：{'、'.join(o['unknown'])}读不到，没对照" for o in used if o["unknown"]]
        bare = [o["name"] for o in used if not has_requirements(o["required"])]
        if bare:
            unchecked.append(f"作业没写练度要求，只核对了有没有这名干员：{'、'.join(bare)}")
        if not opers and not groups:
            unchecked.append("作业里没写干员，没法核对")
        meets = False if missing or gap_count else (None if unchecked else True)
    doc = c.get("doc") or {}
    rated = not plan.get("not_enough_rating") and plan.get("rating_level") is not None
    return {"id": plan.get("id"), "uri": f"prts://{plan.get('id')}", "title": doc.get("title"),
            "stage": c.get("stage_name"), "uploader": plan.get("uploader"), "uploaded_at": plan.get("upload_time"),
            "views": plan.get("views"), "hot": round(plan["hot_score"]) if plan.get("hot_score") else None,
            "like": plan.get("like"), "dislike": plan.get("dislike"),
            "rating": plan.get("rating_level") if rated else None,
            "opers": opers, "groups": groups, "missing_ops": missing,
            "gap_count": gap_count, "all_owned": all_owned,
            "unknown_count": unknown_count, "unchecked": unchecked, "meets": meets}


def _unchecked_n(p: dict) -> int:
    """没核对到的有多少：读不到的项，加作业没写要求的干员（每人算一项）。"""
    used = [o for o in p["opers"] if o["have"]]
    used += [o for g in p["groups"] for o in g["opers"] if o["name"] == g["best"]]
    return sum(len(o["unknown"]) + (0 if has_requirements(o["required"]) else 1) for o in used)


def _sort_key(p: dict):
    # 一个干员都没写的作业什么都核对不了，排在同样差距的后面
    empty = not p["opers"] and not p["groups"]
    return (0 if p["all_owned"] else 1, p["gap_count"] or 0, 1 if empty else 0, _unchecked_n(p), -(p["hot"] or 0))


# --- 命令 ------------------------------------------------------------------------------------

def _stage_keyword(text: str) -> tuple[str, str | None, bool]:
    """关卡 → (查询用的关键词, stageId, 有没有游戏数据)；认不出就原样当关键词。"""
    from maapilot.knowledge import stages
    try:
        sid = stages.resolve_stage(text).stage_id
    except stages.AmbiguousStageError as e:
        raise CliError("ambiguous_stage", str(e), hint="写成 活动名/编号", exit=BAD_REQUEST) from None
    except (FileNotFoundError, OSError):                 # 没有游戏数据表
        return text, None, False
    except Exception:                                    # noqa: BLE001 - 认不出就当关键词
        return text, None, True
    return sid, sid, True


def levels() -> list[dict] | None:
    """作业站的关卡表（`/arknights/level`）；连不上、看不懂 → None。"""
    try:
        d = _api("/arknights/level")
    except CliError:
        return None
    lv = d.get("data")
    return lv if isinstance(lv, list) else None


def match_levels(keyword: str, table: list[dict]) -> list[dict]:
    """关键词 → 关卡表里对得上的关：关卡号、关名、stageId 一模一样的；一个都没有再找包含关键词的。"""
    k = keyword.strip()
    def fields(lv):
        return [lv.get("cat_three") or "", lv.get("name") or "", lv.get("stage_id") or ""]
    hit = [lv for lv in table if k in fields(lv)] or [lv for lv in table if k and any(k in f for f in fields(lv))]
    out = {}
    for lv in hit:
        sid = (lv.get("stage_id") or "").split("#")[0]           # 突袭是 <id>#f#，算同一关
        out.setdefault(sid, {"stage_id": sid, "level_id": lv.get("level_id"),
                             "label": f"{lv.get('cat_one')}·{lv.get('cat_two')}·{lv.get('cat_three')}「{lv.get('name')}」"})
    return list(out.values())


def _plan_stage(p: dict) -> str:
    return (p.get("stage") or "").split("#")[0]


def find_by_keyword(keyword: str, limit: int, has_data: bool) -> tuple[list[dict], dict]:
    """关卡没认出来时：先在作业站的关卡表里找这关，按对得上的关的 stageId 去查（作业站不认的词会按热度给别的关的作业），
    再核对每份作业是不是这几关的，不是的去掉。返回 (作业, 要并进输出的 {matched_stages, dropped, warning})。"""
    head = "" if has_data else "没有游戏数据（maapilot data sync），关卡没认出来；"
    table = levels()
    if table is None:
        return query(keyword, limit), {
            "matched_stages": None, "dropped": 0,
            "warning": head + "关卡没认出来，也连不上作业站的关卡表：下面的作业不一定是这关的，看每份的 stage 再挑"}
    matched = match_levels(keyword, table)
    raw, seen = [], set()
    for m in matched[:5] or [{"stage_id": keyword}]:              # 对不上任何关：照原样查，下面会全部去掉
        for p in query(m["stage_id"], limit):
            if p.get("id") not in seen:
                seen.add(p.get("id"))
                raw.append(p)
    return raw, {"matched": matched, "head": head}


def check_stage(keyword: str, plans: list[dict], matched: list[dict], head: str) -> tuple[list[dict], dict]:
    """核对每份作业是不是对得上的关，不是的去掉；返回 (留下的作业, {matched_stages, dropped, warning})。"""
    ids = {m["stage_id"] for m in matched} | {(m["level_id"] or "") for m in matched}
    label = {m["stage_id"]: m["label"] for m in matched}
    kept = []
    for p in plans:
        sid = _plan_stage(p)
        if sid in ids or p.get("stage") in ids:
            p["stage_label"] = label.get(sid)
            kept.append(p)
    dropped = len(plans) - len(kept)
    warn = []
    if not matched:
        warn.append(f"作业站的关卡表里没有「{keyword}」这一关：作业站不认这个词时会按热度给别的关的作业，已经都去掉；"
                    "换个写法再查（关卡号如 CW-EX-4、关名如「死路」）")
    elif dropped:
        warn.append(f"去掉了 {dropped} 份不是这关的作业")
    if len(matched) > 1:
        warn.append(f"「{keyword}」对得上 {len(matched)} 关（{'、'.join(m['label'] for m in matched)}）：看每份的 stage_label 分清是哪关")
    text = "；".join(x for x in [head.rstrip("；"), *warn] if x)
    return kept, {"matched_stages": matched, "dropped": dropped, "warning": text or None}


def _box_info(box: dict | None) -> dict:
    snap = common.read_json(paths.STATE / "box.json") or {}
    # state box（MAA 识别）写的 box.json 没有 source
    return {"box_at": snap.get("at") if box is not None else None,
            "box_source": (snap.get("source") or "maa") if box is not None else None}


def cmd_find(args) -> dict:
    if args.limit < 1:
        raise CliError("bad_arguments", "--limit 至少是 1", exit=BAD_REQUEST)
    keyword, sid, has_data = _stage_keyword(args.stage)
    box = load_box()
    mtypes = _module_types()
    extra = {"matched_stages": None, "dropped": 0, "warning": None}
    if sid is None:
        raw, info = find_by_keyword(keyword, args.limit, has_data)
        plans = [annotate(p, box, mtypes) for p in raw]
        if "matched" in info:
            plans, extra = check_stage(keyword, plans, info["matched"], info["head"])
        else:
            extra = info
    else:
        plans = [annotate(p, box, mtypes) for p in query(keyword, args.limit)]
    plans.sort(key=_sort_key)
    return {"stage": args.stage, "stage_id": sid, "keyword": keyword, "count": len(plans), **extra, **_box_info(box),
            "plans": plans}


def _plan_id(text: str) -> int:
    from maapilot.tools.maa_cmd import plan_id_of
    pid = plan_id_of(text)
    if pid is None:
        raise CliError("bad_arguments", f"作业编号应是数字（或 prts://数字）：{text}", exit=BAD_REQUEST)
    return pid


def cmd_show(args) -> dict:
    plan = get(_plan_id(args.id))
    box = load_box()
    c = content(plan)
    out = annotate(plan, box)
    doc = c.get("doc") or {}
    return {**out, "details": doc.get("details"), "difficulty": c.get("difficulty"),
            "minimum_required": c.get("minimum_required"), "actions": len(c.get("actions") or []),
            **_box_info(box)}


def strip_requirements(c: dict) -> tuple[dict, int]:
    """去掉 opers 与 groups[].opers 里的 requirements，返回 (新作业, 去掉了几个)。"""
    c = json.loads(json.dumps(c))
    n = 0
    for o in (c.get("opers") or []) + [x for g in c.get("groups") or [] for x in g.get("opers") or []]:
        if "requirements" in o:
            del o["requirements"]
            n += 1
    return c, n


def cmd_strip(args) -> dict:
    pid = _plan_id(args.id)
    c = content(get(pid))
    if not c:
        raise CliError("bad_plan", f"作业 {pid} 的内容读不出来", exit=FAILED)
    out, n = strip_requirements(c)
    path = paths.PRIVATE / "cache" / "copilot" / f"{pid}_noreq.json"
    common.write_json(path, out)
    return {"id": pid, "path": common.rel(path), "removed": n}


def register(sub) -> None:
    g = sub.add_parser("copilot", help="MAA 作业站：按关卡找作业、对照自己的练度（不碰模拟器）")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("find", help="按关卡找作业（按热度），标出每份作业缺哪些干员、练度差多少")
    p.add_argument("stage", metavar="关卡", help="关卡编号（1-7、YW-4、活动名/编号）或 stageId；认不出就当关键词")
    p.add_argument("--limit", type=int, default=20, help="最多看几份作业（默认 20）")
    p.set_defaults(func=cmd_find, command="copilot find")
    p = gs.add_parser("show", help="一份作业的说明、干员、可选位、动作数（同样标注练度）")
    p.add_argument("id", metavar="作业编号", help="作业编号（或 prts://编号）")
    p.set_defaults(func=cmd_show, command="copilot show")
    p = gs.add_parser("strip", help="去掉作业的练度要求，存成本地文件（--ignore-requirements 还不行时用）")
    p.add_argument("id", metavar="作业编号", help="作业编号（或 prts://编号）")
    p.set_defaults(func=cmd_strip, command="copilot strip")
