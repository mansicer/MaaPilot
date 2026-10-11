"""`maapilot copilot find / show / strip`：查 MAA 作业站（prts.maa.plus）的作业，对照自己的干员练度（不碰模拟器、不拿锁）。

- find：关卡 → stageId（`stages.resolve_stage`，认不出就当关键词）→ 作业站按热度查，逐个作业标出每个干员有没有、差多少练度。
- show：一份作业的说明、干员、可选位、动作数，标注同上。
- strip：把作业去掉所有练度要求（requirements）存到 private/cache/copilot/<编号>_noreq.json（`maa copilot --ignore-requirements`
  还被 MAA 拒绝时才用）。

练度来自 private/state/box.json：森空岛来源有技能等级、专精、模组；MAA 识别来源只有精英化、等级、潜能，读不到的项算"不知道"，不算差距。
只列事实（缺谁、差多少），不判断"打不打得过"。

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
            "unknown": unknowns(req, cur, mtypes)}


def unknowns(req: dict, cur: dict | None, mtypes: dict) -> list[str]:
    """作业有要求、但练度里读不出来、没法对照的项（不算差距）。"""
    if cur is None or not req:
        return []
    out = []
    sl = int(req.get("skill_level") or 0)
    if sl and cur.get("skill_level") is None:
        out.append("技能等级")
    if sl >= 8 and cur.get("mastery") is None:
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
        best = min(owned, key=lambda o: len(o["gaps"]), default=None)
        groups.append({"name": g.get("name"), "opers": gops,
                       "have": None if box is None else bool(owned),
                       "best": best and best["name"], "gaps": len(best["gaps"]) if best else None})
    missing = [o["name"] for o in opers if o["have"] is False]
    missing += [f"{g['name']}（任选：{'/'.join(o['name'] for o in g['opers'])}）" for g in groups if g["have"] is False]
    if box is None:
        gap_count, all_owned = None, None
    else:
        gap_count = sum(len(o["gaps"]) for o in opers if o["have"]) + sum(g["gaps"] or 0 for g in groups)
        all_owned = not missing
    doc = c.get("doc") or {}
    rated = not plan.get("not_enough_rating") and plan.get("rating_level") is not None
    return {"id": plan.get("id"), "uri": f"maa://{plan.get('id')}", "title": doc.get("title"),
            "stage": c.get("stage_name"), "uploader": plan.get("uploader"), "uploaded_at": plan.get("upload_time"),
            "views": plan.get("views"), "hot": round(plan["hot_score"]) if plan.get("hot_score") else None,
            "like": plan.get("like"), "dislike": plan.get("dislike"),
            "rating": plan.get("rating_level") if rated else None,
            "opers": opers, "groups": groups, "missing_ops": missing,
            "gap_count": gap_count, "all_owned": all_owned}


def _sort_key(p: dict):
    return (0 if p["all_owned"] else 1, p["gap_count"] if p["gap_count"] is not None else 0, -(p["hot"] or 0))


# --- 命令 ------------------------------------------------------------------------------------

def _stage_keyword(text: str) -> tuple[str, str | None]:
    """关卡 → (查询用的关键词, stageId)；认不出就原样当关键词。"""
    from maapilot.knowledge import stages
    try:
        sid = stages.resolve_stage(text).stage_id
    except stages.AmbiguousStageError as e:
        raise CliError("ambiguous_stage", str(e), hint="写成 活动名/编号", exit=BAD_REQUEST) from None
    except Exception:                                    # noqa: BLE001 - 认不出（或没有数据表）就当关键词
        return text, None
    return sid, sid


def _box_info(box: dict | None) -> dict:
    snap = common.read_json(paths.STATE / "box.json") or {}
    return {"box_at": snap.get("at") if box is not None else None,
            "box_source": snap.get("source") if box is not None else None}


def cmd_find(args) -> dict:
    if args.limit < 1:
        raise CliError("bad_arguments", "--limit 至少是 1", exit=BAD_REQUEST)
    keyword, sid = _stage_keyword(args.stage)
    box = load_box()
    mtypes = _module_types()
    plans = sorted((annotate(p, box, mtypes) for p in query(keyword, args.limit)), key=_sort_key)
    return {"stage": args.stage, "stage_id": sid, "keyword": keyword, "count": len(plans), **_box_info(box),
            "plans": plans}


def _plan_id(text: str) -> int:
    s = str(text).strip().removeprefix("maa://")
    if not s.isdigit():
        raise CliError("bad_arguments", f"作业编号应是数字（或 maa://数字）：{text}", exit=BAD_REQUEST)
    return int(s)


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
    p.add_argument("id", metavar="作业编号", help="作业编号（或 maa://编号）")
    p.set_defaults(func=cmd_show, command="copilot show")
    p = gs.add_parser("strip", help="去掉作业的练度要求，存成本地文件（--ignore-requirements 还不行时用）")
    p.add_argument("id", metavar="作业编号", help="作业编号（或 maa://编号）")
    p.set_defaults(func=cmd_strip, command="copilot strip")
