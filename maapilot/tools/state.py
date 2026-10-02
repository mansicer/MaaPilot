"""`maapilot state depot / box / sanity / show`：读账号状态、存快照。契约见 docs/cli.md「state」。

- depot：MAA 仓库识别（Depot）→ private/state/depot.json `{at, items: {id: n}}`，并记下与上一份的差；
- box：MAA 干员识别（OperBox）→ box.json `{at, operators: {名: {id, rarity, elite, level, potential}}}`；
- op：进这名干员的干员页读精英化、等级、技能等级（MaaFramework，只读，不存快照）；专精、模组读不了；
- sanity：回首页 OCR 右上角的理智（MaaFramework）→ sanity.json `{at, current, max, source}`；
  `maa fight` 结束时也会用日志里最后一次的理智顺手更新它（source: maa-log）。
- show：读快照，不碰模拟器。

`read_depot()` / `read_box()` 是不拿锁的函数版本（guard、daily run 用）。
"""
from __future__ import annotations

import re

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, FAILED, CliError
from maapilot.tools import common

KINDS = ("depot", "box", "sanity")


def snap_path(kind: str):
    return paths.STATE / f"{kind}.json"


def load(kind: str) -> dict | None:
    return common.read_json(snap_path(kind))


def _name(iid: str) -> str:
    try:
        from maapilot.knowledge.items import display_name
        return display_name(iid)
    except Exception:
        return iid


def diff(before: dict | None, after: dict) -> list[dict]:
    """两份 {id: n} 的差（只列变了的）。"""
    before = before or {}
    out = []
    for iid in sorted(set(before) | set(after), key=lambda k: (len(k), k)):
        d = int(after.get(iid, 0)) - int(before.get(iid, 0))
        if d:
            out.append({"id": iid, "name": _name(iid), "delta": d})
    return out


def items_list(items: dict, only: list[str] | None = None) -> list[dict]:
    ids = sorted(items, key=lambda k: (len(k), k))
    if only:
        ids = [i for i in ids if i in only]
    return [{"id": i, "name": _name(i), "count": items[i]} for i in ids]


# --- 读（碰模拟器，不拿锁） -------------------------------------------------------------

def read_depot() -> dict:
    """识别仓库、写快照，返回 {at, items: {id: n}, changed: [...]}。失败抛 CliError(depot_failed)。"""
    from maapilot.crew import maa
    common.ensure_device()
    if maa.config_missing():
        maa.install_config()
    try:
        items = maa.depot_ids()
    except Exception as e:
        raise CliError("depot_failed", f"仓库识别失败：{e}", exit=FAILED,
                       hint="截图看看在哪个页面（maapilot screen shot），回首页后再试") from None
    prev = load("depot")
    snap = {"at": common.iso(), "items": items}
    common.write_json(snap_path("depot"), snap)
    return {**snap, "changed": diff((prev or {}).get("items"), items)}


def read_box() -> dict:
    from maapilot.crew import maa
    common.ensure_device()
    if maa.config_missing():
        maa.install_config()
    try:
        ops = maa.operbox()
    except Exception as e:
        raise CliError("box_failed", f"干员识别失败：{e}", exit=FAILED,
                       hint="截图看看在哪个页面（maapilot screen shot），回首页后再试") from None
    try:
        from maapilot.knowledge.gamedata import operators
        known = operators()
        for name, o in ops.items():
            if not o.get("id") and name in known:
                o["id"] = known[name]["id"]
    except Exception:
        pass
    snap = {"at": common.iso(), "operators": ops}
    common.write_json(snap_path("box"), snap)
    return snap


# 首页右上角（2026-09 真机，1280×720）：顶栏一排 龙门币 / 合成玉（带"+"）/ 源石（带"+"），y 约 20–100；
# 下面"终端"按钮左边是理智：大号的当前值（约 767,133），下面一行小字"理智/上限"（约 783,233）。
HOME_ROI = (640, 15, 640, 265)
TOPBAR_MAX_Y = 110
TOPBAR_MIN_H = 30                 # 顶栏数字的字高约 38；更小的是上面一行的日期时间
SANITY_MAX_RANGE = (80, 300)      # 理智上限的合理范围（真机见过 205）
_DIGITS = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1", ",": "", " ": ""})
_SANITY_MAX = re.compile(r"理智\s*/\s*([0-9OoDlI]{2,3})")
_SANITY_TEXT = re.compile(r"^\s*(\d{1,3})\s*/\s*(\d{2,3})\s*$")


def _number(text: str) -> int | None:
    t = text.strip().rstrip("+").strip().translate(_DIGITS)
    return int(t) if t.isdigit() else None


def parse_sanity(hits) -> tuple[int, int] | None:
    """首页 OCR 的结果（有 text、box 的对象）→ (当前, 上限)。认得两种写法：一段"82/135"，或大号数字 + 下面的"理智/135"。"""
    for h in hits:
        m = _SANITY_TEXT.match(h.text.replace(" ", "").translate(_DIGITS))
        if m and SANITY_MAX_RANGE[0] <= int(m.group(2)) <= SANITY_MAX_RANGE[1]:
            return int(m.group(1)), int(m.group(2))
    for h in hits:
        m = _SANITY_MAX.search(h.text)
        if not m:
            continue
        mx = int(m.group(1).translate(_DIGITS))
        if not SANITY_MAX_RANGE[0] <= mx <= SANITY_MAX_RANGE[1]:
            continue
        x, y = h.box[0], h.box[1]
        # 当前值：在"理智/上限"上方、横向挨着的纯数字里，字最大的那个
        cands = [(c.box[3], n) for c in hits if (n := _number(c.text)) is not None and n <= 999
                 and c.box[1] + c.box[3] <= y + 5 and y - c.box[1] < 160 and abs(c.box[0] - x) < 120]
        if cands:
            return max(cands)[1], mx
    return None


def parse_topbar(hits) -> dict | None:
    """首页顶栏 → {lmd, orundum, originite}（精确值，不像仓库识别那样把上万的数取整）。认不全三样 → None。"""
    nums = sorted((h.box[0], n) for h in hits if h.box[1] < TOPBAR_MAX_Y and h.box[3] >= TOPBAR_MIN_H
                  and (n := _number(h.text)) is not None)
    # 按位置取：龙门币最左，源石最右
    nums = [(x, n) for x, n in nums if x >= 650]
    if len(nums) != 3:
        return None
    return {"lmd": nums[0][1], "orundum": nums[1][1], "originite": nums[2][1]}


def read_home(game=None) -> tuple[dict | None, tuple[int, int] | None, object]:
    """回首页、OCR 右上角 → (顶栏 {lmd, orundum, originite} 或 None, (理智, 上限) 或 None, game)。不拿锁。"""
    common.ensure_device()
    game = game or common.connect(ocr=True)
    from maapilot.crew import nav
    try:
        nav.ensure_home(game)
    except Exception:
        pass
    hits = game.ocr(HOME_ROI, game.screencap())
    return parse_topbar(hits), parse_sanity(hits), game


def read_sanity() -> dict:
    top, found, game = read_home()
    if not found:
        from maapilot.tools.screen import capture
        shot = capture(game, "sanity")[1]["path"]
        raise CliError("sanity_failed", "首页上没认出理智（当前/上限）", exit=FAILED, data={"shot": shot},
                       hint="看截图确认在首页；也可以跑一次 maa fight，结束时会顺手记下理智")
    snap = {"at": common.iso(), "current": found[0], "max": found[1], "source": "ocr"}
    common.write_json(snap_path("sanity"), snap)
    return snap


# --- 命令 ----------------------------------------------------------------------------

def _depot_out(snap: dict, only=None) -> dict:
    out = {"at": snap["at"], "age_min": common.age_min(snap["at"]), "items": items_list(snap["items"], only)}
    if "changed" in snap:
        out["changed"] = snap["changed"]
    return out


def _box_out(snap: dict, ops_filter=None) -> dict:
    ops = snap.get("operators") or {}
    names = [n for n in ops if not ops_filter or n in ops_filter]
    return {"at": snap["at"], "age_min": common.age_min(snap["at"]),
            "operators": [{"name": n, **ops[n]} for n in names],
            **({"missing": [n for n in ops_filter if n not in ops]} if ops_filter else {})}


def _sanity_out(snap: dict) -> dict:
    return {**snap, "age_min": common.age_min(snap.get("at"))}


def _item_ids(names: list[str] | None) -> list[str] | None:
    if not names:
        return None
    from maapilot.tools.data import resolve_item_or_fail
    return [resolve_item_or_fail(n) for n in names]


@common.logged
def cmd_depot(args):
    only = _item_ids(args.item)
    with common.device("state depot") as lk:
        with lk.keepalive():
            snap = read_depot()
    out = _depot_out(snap, only)
    out["_log"] = {"depot_change": snap["changed"]}
    return out


@common.logged
def cmd_box(args):
    with common.device("state box") as lk:
        with lk.keepalive():
            snap = read_box()
    return _box_out(snap, args.op)


@common.logged
def cmd_sanity(args):
    with common.device("state sanity"):
        return _sanity_out(read_sanity())


def cmd_op(args):
    """读一名干员的精英化、等级、技能等级（进干员页看，不点任何会花东西的按钮）。"""
    from maapilot.flows._common import check_operator, rel, run_shortcut
    info = check_operator(args.name)

    def body(g, pr):
        from maapilot.crew import upgrade as up
        pr.stage = "进干员页读练度"
        cur = up.read_operator(g, args.name)
        shot = g.save(f"state_op_{args.name}")
        try:
            up.leave_list_unfiltered(g)
        except Exception:                                # noqa: BLE001 - 清筛选失败不影响读到的数
            pass
        return {"name": args.name, "rarity": info.get("rarity"), "elite": cur["elite"], "level": cur["level"],
                "skill_level": cur["skill"], "at": common.iso(), "shot": rel(shot),
                "unread": ["专精", "模组"] + ([] if cur["level"] is not None else ["等级"])
                + ([] if cur["skill"] is not None or not info.get("skills") else ["技能等级"])}

    return run_shortcut("state op", {"operator": args.name}, f"state_op_error_{args.name}", body)


def cmd_show(args):
    kinds = [args.kind] if args.kind else list(KINDS)
    only = _item_ids(args.item)
    out = {}
    for k in kinds:
        snap = load(k)
        if snap is None:
            continue
        out[k] = {"depot": lambda s: _depot_out(s, only), "box": lambda s: _box_out(s, args.op),
                  "sanity": _sanity_out}[k](snap)
    if not out:
        what = args.kind or "任何状态"
        raise CliError("no_snapshot", f"还没有读过{what}", exit=BAD_REQUEST,
                       hint=f"先 maapilot state {args.kind or 'depot'}")
    return out[args.kind] if args.kind else out


def register(sub) -> None:
    g = sub.add_parser("state", help="读仓库、干员练度、理智并存快照")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("depot", help="识别仓库（MAA Depot）")
    p.add_argument("--item", nargs="+", metavar="物品", help="只输出这些物品")
    p.set_defaults(func=cmd_depot, command="state depot")
    p = gs.add_parser("box", help="识别干员练度（MAA OperBox）")
    p.add_argument("--op", nargs="+", metavar="干员", help="只输出这些干员")
    p.set_defaults(func=cmd_box, command="state box")
    p = gs.add_parser("op", help="进干员页读一名干员的精英化、等级、技能等级（只读，约一分钟）")
    p.add_argument("name", help="干员名")
    p.set_defaults(func=cmd_op, command="state op")
    p = gs.add_parser("sanity", help="识别理智")
    p.set_defaults(func=cmd_sanity, command="state sanity")
    p = gs.add_parser("show", help="读快照（不碰模拟器）")
    p.add_argument("kind", nargs="?", choices=KINDS)
    p.add_argument("--item", nargs="+", metavar="物品")
    p.add_argument("--op", nargs="+", metavar="干员")
    p.set_defaults(func=cmd_show, command="state show")
