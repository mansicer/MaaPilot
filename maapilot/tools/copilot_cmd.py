"""`maapilot copilot find / show / strip`：查 MAA 作业站（prts.maa.plus）的作业，对照自己的干员练度（不碰模拟器、不拿锁）。

- find：关卡 → stageId（`stages.resolve_stage`，认不出就到作业站的关卡表里找）→ 作业站按热度一页页查（找到满足要求的就停），
  核对每份是不是这关的，逐个作业标出每个干员有没有、差多少练度。
- show：一份作业的说明、干员、可选位、动作数，标注同上。
- strip：把作业去掉所有练度要求（requirements）存到 private/cache/copilot/<编号>_noreq.json（`maa copilot --ignore-requirements`
  还被 MAA 拒绝时才用）。

技能要精几解锁、哪些名字是召唤物 / 装置（不是干员，不核对）来自游戏数据的 character_table。
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
PAGE_SIZE = 20                                             # 作业站一页几份
MAX_STAGES = 5                                             # 一个词对得上很多关时，最多查几关
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


def query(keyword: str, page: int = 1, size: int = PAGE_SIZE) -> tuple[list[dict], bool, int | None]:
    """按热度查一页：(作业, 后面还有没有, 一共几份)。"""
    d = _api("/copilot/query", levelKeyword=keyword, order_by="hot", limit=size, page=page)
    if d.get("status_code") not in (None, 200):
        raise CliError("network", f"作业站报错：{d.get('message')}", exit=FAILED)
    body = d.get("data") or {}
    return list(body.get("data") or []), bool(body.get("has_next")), body.get("total")


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


def _roster() -> dict | None:
    """游戏数据里的名字：{"ops": 干员名 → 各技能解锁要精几, "others": 召唤物 / 装置名}；没有游戏数据 → None。
    召唤物、装置和干员重名的（阿米娅、红隼……）算干员。"""
    try:
        from maapilot.knowledge import gamedata
        table = gamedata._character_table()
    except Exception:                                    # noqa: BLE001 - 没有数据表时不核对技能解锁
        return None
    ops, others = {}, set()
    for cid, c in table.items():
        if cid.startswith("char_") and c.get("profession") not in ("TOKEN", "TRAP"):
            ops[c["name"]] = [gamedata._phase((s.get("unlockCond") or {}).get("phase")) for s in c.get("skills") or []]
        else:
            others.add(c.get("name"))
    return {"ops": ops, "others": others - set(ops)}


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


def skill_locked(skill: int | None, op: dict | None, unlock: list[int] | None) -> tuple[str, int | None] | None:
    """作业要用的技能这名干员现在还没有：(给人看的中文, 要精几才解锁)；有、或说不准 → None。
    有游戏数据时按技能的解锁条件对精英化；没有时看森空岛列出来的技能里有没有它。"""
    if not skill or op is None:
        return None
    ce = op.get("elite")
    if unlock is not None:
        if skill > len(unlock):
            return f"没有技能{skill}（这名干员只有 {len(unlock)} 个技能）", None
        need = unlock[skill - 1]
        if ce is not None:
            return (f"技能{skill}要精{need}才解锁，现在精{ce}", need) if ce < need else None
    skills = op.get("skills")
    if isinstance(skills, list) and not any(x.get("index") == skill for x in skills):
        return f"现在还没有技能{skill}", None
    return None


def gaps(req: dict, skill: int | None, op: dict | None, cur: dict | None, mtypes: dict,
         unlock: list[int] | None = None) -> list[str]:
    """作业要求与现有练度的差距（给人看的中文）。读不到的项不算差距。`unlock` 是各技能解锁要精几（游戏数据）。"""
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
    locked = skill_locked(skill, op, unlock)
    if locked and not (out and locked[1] is not None and e >= locked[1]):   # 精英化不够时上面已经说了
        out.append(locked[0])
    if sl >= 8 and skill:
        want = sl - 7
        if csl is not None and csl < 7:
            out.append(f"要技能等级 7，现在 {csl}")
        m = cur.get("mastery")
        if not locked and m is not None and m < want:
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


def annotate_oper(o: dict, box: dict | None, mtypes: dict, roster: dict | None = None) -> dict:
    name, skill = o.get("name"), o.get("skill")
    req = o.get("requirements") or {}
    out = {"name": name, "skill": skill, "required": req or None}
    if roster and name not in roster["ops"] and name in roster["others"]:
        # 召唤物、装置（沙地兽、安全起重机……）：不是仓库里的干员，不核对
        return {**out, "summon": True, "have": None, "current": None, "gaps": [], "unknown": []}
    if box is None:
        return {**out, "have": None, "current": None, "gaps": [], "unknown": []}
    op = box.get(name)
    cur = current(op, skill, mtypes)
    unlock = roster["ops"].get(name) if roster else None
    return {**out, "have": op is not None, "current": cur, "gaps": gaps(req, skill, op, cur, mtypes, unlock),
            "unknown": unknowns(req, cur, mtypes, op, skill, unlock)}


def has_requirements(req: dict | None) -> bool:
    """作业真写了练度要求（全写 0、模组 -1、潜能 1 都等于没写）。"""
    if not req:
        return False
    return bool(int(req.get("elite") or 0) or int(req.get("level") or 0) or int(req.get("skill_level") or 0)
                or int(req.get("module") or 0) > 0 or int(req.get("potentiality") or 0) > 1)


def unknowns(req: dict, cur: dict | None, mtypes: dict, op: dict | None = None, skill: int | None = None,
             unlock: list[int] | None = None) -> list[str]:
    """作业有要求、但练度里读不出来、没法对照的项（不算差距）。"""
    if cur is None or not req:
        return []
    out = []
    sl = int(req.get("skill_level") or 0)
    if sl and cur.get("skill_level") is None:
        out.append("技能等级")
    lacks_skill = skill_locked(skill, op, unlock) is not None
    if sl >= 8 and cur.get("mastery") is None and not lacks_skill:   # 还没有这个技能：差距里已经说了
        out.append("专精")
    mt = MODULE_TYPES.get(int(req.get("module") or 0))
    if mt:
        mods = cur.get("modules")
        states = [x.get("unlocked") for x in mods or [] if x.get("type") == MODULE_LABEL[mt]]
        if mods is None or not mtypes or (None in states and True not in states):
            out.append(f"{MODULE_LABEL[mt]}模组")
    return out


_UNSET = object()


def _real(ops: list[dict]) -> list[dict]:
    return [o for o in ops if not o.get("summon")]


def _empty(p: dict) -> bool:
    """作业里一个干员都没写（只有召唤物、装置的也算）。"""
    return not _real(p["opers"]) and not any(_real(g["opers"]) for g in p["groups"])


def annotate(plan: dict, box: dict | None, mtypes: dict | None = None, roster=_UNSET) -> dict:
    """作业站的一条作业 → 输出的样子（干员逐个标注、可选位、缺谁、差距数）。"""
    mtypes = _module_types() if mtypes is None else mtypes
    roster = _roster() if roster is _UNSET else roster
    c = content(plan)
    opers = [annotate_oper(o, box, mtypes, roster) for o in c.get("opers") or []]
    groups = []
    for g in c.get("groups") or []:
        gops = [annotate_oper(o, box, mtypes, roster) for o in g.get("opers") or []]
        owned = [o for o in gops if o["have"]]
        # 差距一样时挑读不到的少、作业写了要求的：能核对的人在前
        best = min(owned, key=lambda o: (len(o["gaps"]), len(o["unknown"]), not has_requirements(o["required"])),
                   default=None)
        groups.append({"name": g.get("name"), "opers": gops,
                       "have": None if box is None or not _real(gops) else bool(owned),
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
        p = {"opers": opers, "groups": groups}
        if _empty(p):
            summons = [o["name"] for o in opers + [x for g in groups for x in g["opers"]] if o.get("summon")]
            n = len(c.get("actions") or [])
            why = (f"只写了召唤物、装置：{'、'.join(summons)}" if summons
                   else f"只写了 {n} 个动作" if n else "")
            unchecked.append(f"作业里没写干员{'（' + why + '）' if why else ''}，没法核对")
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
    return (0 if p["all_owned"] else 1, p["gap_count"] or 0, 1 if _empty(p) else 0, _unchecked_n(p), -(p["hot"] or 0))


# --- 命令 ------------------------------------------------------------------------------------

def _stage_keyword(text: str) -> tuple[dict | None, bool]:
    """关卡 → (这关 {stage_id, level_id, label}, 有没有游戏数据)；本地认不出 → (None, …)。"""
    from maapilot.knowledge import stages
    try:
        ref = stages.resolve_stage(text)
    except stages.AmbiguousStageError as e:
        raise CliError("ambiguous_stage", str(e), hint="写成 活动名/编号", exit=BAD_REQUEST) from None
    except (FileNotFoundError, OSError):                 # 没有游戏数据表
        return None, False
    except Exception:                                    # noqa: BLE001 - 认不出就到作业站的关卡表里找
        return None, True
    code, name = getattr(ref, "code", None), getattr(ref, "name", None)
    label = f"{code}「{name}」" if code and name else (code or name or ref.stage_id)
    # 突袭是 <id>#f#：作业站按同一关的 stageId 查、作业里也写不带后缀的
    return {"stage_id": ref.stage_id.split("#")[0], "level_id": None, "label": label}, True


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
    """作业写的关卡 → stageId 的样子：去掉 #f#（突袭）、#s 之类的后缀；写成 levelId（obt/main/level_main_1-7）的取最后一段。"""
    s = (p.get("stage") or "").split("#")[0]
    return s.rsplit("/", 1)[-1].removeprefix("level_")


def on_stage(plans: list[dict], ids: set[str]) -> list[dict]:
    """作业站按一关的 stageId 给的一页作业里，是这关的那些。作业站认得这关时只给这关的，只是有的作业把关卡写成了关卡号
    （CW-EX-4、16-13：第 10 章起关卡号和 stageId 差一，不能自己换算），这些照样算这关；页里混着别的关的 stageId 时
    （作业站不认这个词，按热度给了全站的作业），只留 stageId 对得上的。"""
    hit = [p for p in plans if _plan_stage(p) in ids]
    if any("_" in _plan_stage(p) for p in plans if p not in hit):   # stageId 都带下划线，关卡号不带
        return hit
    return plans


def _locally_unknown(stage_id: str) -> bool:
    """本机游戏数据里没有这关（悖论模拟本来就不在里面，不算）。"""
    if stage_id.startswith("mem_"):
        return False
    from maapilot.knowledge import stages
    try:
        stages.resolve_stage(stage_id)
    except Exception:                                    # noqa: BLE001
        return True
    return False


def collect(targets: list[dict], box: dict | None, mtypes: dict, roster: dict | None, pages: int) -> dict:
    """按每关的 stageId 一页页查、标注、核对关卡；有一份满足要求（meets）就不再往后翻。没有练度时只看第一页。"""
    ids = {t["stage_id"] for t in targets}
    label = {t["stage_id"]: t["label"] for t in targets}
    plans, seen, dropped, total, more = [], set(), 0, 0, False
    last = pages if box is not None else 1
    for t in targets:
        for page in range(1, last + 1):
            raw, has_next, n = query(t["stage_id"], page)
            raw = [r for r in raw if r.get("id") not in seen]
            seen |= {r.get("id") for r in raw}
            got = [annotate(r, box, mtypes, roster) for r in raw]
            kept = on_stage(got, ids)
            dropped += len(got) - len(kept)
            for p in kept:
                p["stage_label"] = label.get(_plan_stage(p), t["label"])
            plans += kept
            if len(kept) < len(got):                     # 作业站没认这关：往后翻也是别的关的，总数也是全站的
                break
            if page == 1:
                total += n or 0
            if not has_next or any(p["meets"] for p in kept):
                more = more or has_next
                break
            more = more or page == last
    return {"plans": plans, "dropped": dropped, "total": total, "more": more}


def _unknown_names(plans: list[dict], roster: dict | None) -> list[str]:
    if not roster:
        return []
    names = {o["name"] for p in plans for o in p["opers"] + [x for g in p["groups"] for x in g["opers"]]}
    return sorted(n for n in names if n and n not in roster["ops"] and n not in roster["others"])


def _box_info(box: dict | None) -> dict:
    snap = common.read_json(paths.STATE / "box.json") or {}
    # state box（MAA 识别）写的 box.json 没有 source
    return {"box_at": snap.get("at") if box is not None else None,
            "box_source": (snap.get("source") or "maa") if box is not None else None}


def cmd_find(args) -> dict:
    if args.limit < 1:
        raise CliError("bad_arguments", "--limit 至少是 1", exit=BAD_REQUEST)
    if args.pages < 1:
        raise CliError("bad_arguments", "--pages 至少是 1", exit=BAD_REQUEST)
    local, has_data = _stage_keyword(args.stage)
    box, mtypes, roster = load_box(), _module_types(), _roster()
    warn = []
    if local is not None:
        targets = [local]
    else:
        table = levels()
        if table is None:
            # 认不出、也拿不到关卡表：照原样查一页，不核对关卡
            raw, has_next, n = query(args.stage)
            got = [annotate(r, box, mtypes, roster) for r in raw]
            res = {"plans": got, "dropped": 0, "total": n, "more": has_next}
            targets = None
            warn.append(("没有游戏数据（maapilot data sync），" if not has_data else "") +
                        "关卡没认出来，也连不上作业站的关卡表：下面的作业不一定是这关的，看每份的 stage 再挑")
        else:
            targets = match_levels(args.stage, table)
            if not targets:
                warn.append(f"作业站的关卡表里没有「{args.stage}」这一关，没有查作业；换个写法再查（关卡号如 CW-EX-4、关名如「死路」）")
            elif not has_data:
                warn.append("没有游戏数据（maapilot data sync），关卡是按作业站的关卡表认的")
            elif any(_locally_unknown(t["stage_id"]) for t in targets):
                warn.append("本机的游戏数据里没有这关，可能太旧了（maapilot data sync）；关卡是按作业站的关卡表认的")
            if len(targets) > MAX_STAGES:
                warn.append(f"「{args.stage}」对得上 {len(targets)} 关，只查了前 {MAX_STAGES} 关；写得具体些再查")
                targets = targets[:MAX_STAGES]
            if len(targets) > 1:
                warn.append(f"「{args.stage}」对得上 {len(targets)} 关（{'、'.join(t['label'] for t in targets)}）："
                            "看每份的 stage_label 分清是哪关")
    if targets is not None:
        res = collect(targets, box, mtypes, roster, args.pages) if targets else \
            {"plans": [], "dropped": 0, "total": 0, "more": False}
    if res["dropped"] and not res["plans"]:
        warn.insert(0, f"作业站没给这关的作业：它不认这关时会按热度给别的关的，{res['dropped']} 份都去掉了；"
                       "在网页 https://prts.plus 上查查看")
    elif res["dropped"]:
        warn.insert(0, f"去掉了 {res['dropped']} 份不是这关的作业")
    names = _unknown_names(res["plans"], roster)
    if names:
        warn.append(f"作业里有本机游戏数据不认识的名字（{'、'.join(names[:5])}{'…' if len(names) > 5 else ''}）："
                    "可能是新干员、游戏数据太旧（maapilot data sync），这些人的技能解锁没核对")
    plans = sorted(res["plans"], key=_sort_key)
    return {"stage": args.stage, "stage_id": local and local["stage_id"], "count": min(len(plans), args.limit),
            "looked": len(plans), "total": res["total"], "more": res["more"],
            "matched_stages": targets, "dropped": res["dropped"], "warning": "；".join(warn) or None,
            **_box_info(box), "plans": plans[:args.limit]}


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
    out = annotate(plan, box, roster=_roster())
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
    p.add_argument("--limit", type=int, default=20, help="最多列几份作业（默认 20）")
    p.add_argument("--pages", type=int, default=5,
                   help=f"每关最多翻几页（一页 {PAGE_SIZE} 份，默认 5；找到满足要求的就不再往后翻）")
    p.set_defaults(func=cmd_find, command="copilot find")
    p = gs.add_parser("show", help="一份作业的说明、干员、可选位、动作数（同样标注练度）")
    p.add_argument("id", metavar="作业编号", help="作业编号（或 prts://编号）")
    p.set_defaults(func=cmd_show, command="copilot show")
    p = gs.add_parser("strip", help="去掉作业的练度要求，存成本地文件（--ignore-requirements 还不行时用）")
    p.add_argument("id", metavar="作业编号", help="作业编号（或 prts://编号）")
    p.set_defaults(func=cmd_strip, command="copilot strip")
