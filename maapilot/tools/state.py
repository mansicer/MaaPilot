"""`maapilot state depot / box / sanity / show`：读账号状态、存快照。契约见 docs/cli.md「state」。

- depot：MAA 仓库识别（Depot）→ private/state/depot.json `{at, items: {id: n}}`，并记下与上一份的差；
- box：MAA 干员识别（OperBox）→ box.json `{at, operators: {名: {id, rarity, elite, level, potential}}}`；
- op：进这名干员的干员页读精英化、等级、技能等级（MaaFramework，只读，不存快照）；专精、模组读不了；
- sanity：接了森空岛先从森空岛读（准、不碰模拟器）；没接或读不到才回首页 OCR 右上角（MaaFramework）
  → sanity.json `{at, current, max, source}`。首页大号数字的"1"OCR 认不好（真机读错过 130 → 29、114 → 4），
  当前值改为按字形单独认（`home_sanity`）；读数仍比上一份快照推出来的明显低时标 `suspect`。`maa fight` 结束时也会用日志里最后一次的理智顺手更新它（source: maa-log）。
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
    maa.install_config()                    # 每次都同步：config.yaml 改了 adb 也能带给 maa-cli
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
    maa.install_config()                    # 每次都同步：config.yaml 改了 adb 也能带给 maa-cli
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
TOPBAR_MIN_H = 34                 # 顶栏数字的字高 38–45；上面一行的日期时间约 24–31（真机 10-05 见过 31）
SANITY_MAX_RANGE = (80, 300)      # 理智上限的合理范围（真机见过 205）
_DIGITS = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1", ",": "", " ": ""})
_SANITY_MAX = re.compile(r"理智\s*/\s*([0-9OoDlI]{2,3})")
_SANITY_TEXT = re.compile(r"^\s*(\d{1,3})\s*/\s*(\d{2,3})\s*$")


_EDGE_JUNK = re.compile(r"^[^0-9OoDlI|]+|[^0-9OoDlI|]+$")   # 数字前后的杂字符：图标认成的"{"、"+" 号等


def _number(text: str) -> int | None:
    t = _EDGE_JUNK.sub("", text.strip()).translate(_DIGITS)
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


# 理智的大号数字（深灰字，压在浅灰底和白色线框图标上）。OCR 模型认不好这种字体的"1"（读成 7、I、{、亻，或者干脆丢掉：
# 真机 110 → 0、114 → 4、130 → 29/730、152 → 52），所以单独处理这一块：先把深色像素抠成白底黑字，按列切出每个字，
# 窄的（宽 / 高 < 0.45，"1" 约 0.33，别的数字 ≥ 0.54）就是"1"，其余的字交给 OCR，再按位置拼回去。见 sanity_from_image。
SANITY_NUM_ROI = (758, 128, 140, 98)       # 左边是"+"号圆钮（到 x≈757），下面是"理智/上限"的深色底条（从 y≈232）
SANITY_DARK = 100                          # 字的颜色约 (50,50,50)；底、图标、阴影都在 140 以上
ONE_RATIO = 0.45


def sanity_glyphs(img) -> tuple[object, list[bool]] | None:
    """首页截图（BGR 数组）→ (只剩理智数字的白底黑字整张图, 从左到右每个字是不是"1")；这一块没有像数字的东西 → None。"""
    import numpy as np
    if not isinstance(img, np.ndarray) or img.ndim != 3:
        return None
    x, y, w, h = SANITY_NUM_ROI
    dark = img[y:y + h, x:x + w].max(axis=2) < SANITY_DARK
    rows = np.flatnonzero(dark.any(axis=1))
    if rows.size == 0:
        return None
    height = rows[-1] - rows[0] + 1
    if height < 30:                            # 字高约 54–65；太矮的是杂点
        return None
    cols = np.append(dark.any(axis=0), False)
    runs, start = [], None
    for i, c in enumerate(cols):
        if c and start is None:
            start = i
        elif not c and start is not None:
            if start > 0 and i - start >= 4:   # 贴着左边的是"+"号圆钮的边，太窄的是杂点
                runs.append(i - start)
            start = None
    if not runs or len(runs) > 3:
        return None
    clean = np.full_like(img, 255)
    clean[y:y + h, x:x + w][dark] = 0
    return clean, [r / height < ONE_RATIO for r in runs]


def merge_ones(text: str, ones: list[bool]) -> int | None:
    """OCR 读出的字 + 每个字是不是"1" → 数。"1" 的位置一律填 1，别的位置按顺序用 OCR 读出的数字；对不上 → None。"""
    digits = "".join(c for c in text.translate(_DIGITS) if c.isdigit())
    wide = ones.count(False)
    if len(digits) == len(ones):
        others = [d for d, one in zip(digits, ones) if not one]
    else:
        others = list(digits.replace("1", "")) if len(digits) != wide else list(digits)
        if len(others) != wide:
            return None
    if "1" in others:                          # 宽的字不会是 1
        return None
    it = iter(others)
    return int("".join("1" if one else next(it) for one in ones))


def sanity_from_image(game, img) -> int | None:
    """首页截图 → 当前理智（按上面的办法）；这一块认不出数字 → None。"""
    g = sanity_glyphs(img)
    if g is None:
        return None
    clean, ones = g
    x, y, w, h = SANITY_NUM_ROI
    hits = sorted(game.ocr((x - 60, y - 40, w + 120, h + 80), clean), key=lambda c: c.box[0])
    return merge_ones("".join(c.text for c in hits), ones)


def parse_topbar(hits) -> dict | None:
    """首页顶栏 → {lmd, orundum, originite}（精确值，不像仓库识别那样把上万的数取整）。
    按位置取：源石最右、合成玉在它左边；龙门币（最左）读不出时为 None，不影响前两样。少于两个数 → None。"""
    nums = sorted((h.box[0], n) for h in hits if h.box[1] < TOPBAR_MAX_Y and h.box[3] >= TOPBAR_MIN_H
                  and ":" not in h.text and (n := _number(h.text)) is not None)   # 带冒号的是日期时间
    nums = [(x, n) for x, n in nums if x >= 650]
    if len(nums) < 2 or len(nums) > 3:
        return None
    return {"lmd": nums[0][1] if len(nums) == 3 else None, "orundum": nums[-2][1], "originite": nums[-1][1]}


def read_home(game=None) -> tuple[dict | None, tuple[int, int] | None, object]:
    """回首页、OCR 右上角 → (顶栏 {lmd, orundum, originite} 或 None, (理智, 上限) 或 None, game)。不拿锁。"""
    common.ensure_device()
    game = game or common.connect(ocr=True)
    from maapilot.crew import nav
    try:
        nav.ensure_home(game)
    except Exception:
        pass
    img = game.screencap()
    hits = game.ocr(HOME_ROI, img)
    return parse_topbar(hits), home_sanity(game, img, hits), game


def home_sanity(game, img, hits) -> tuple[int, int] | None:
    """首页的 (理智, 上限)：上限从"理智/上限"那行读；当前值按 sanity_from_image 单独认。
    那一块切不出数字（页面布局不一样）时退回 parse_sanity；切出了数字但和 OCR 对不上 → None（宁可读不到，不报错数）。"""
    mx = sanity_max(hits)
    if mx is None or sanity_glyphs(img) is None:
        return parse_sanity(hits)
    cur = sanity_from_image(game, img)
    return None if cur is None else (cur, mx)


def sanity_max(hits) -> int | None:
    """首页"理智/上限"那行的上限；没有或不在合理范围 → None。"""
    for h in hits:
        m = _SANITY_MAX.search(h.text)
        if m and SANITY_MAX_RANGE[0] <= (mx := int(m.group(1).translate(_DIGITS))) <= SANITY_MAX_RANGE[1]:
            return mx
    return None


SUSPECT_GAP = 20      # OCR 读数比快照推出来的少这么多以上 → 可疑（也可能是用户自己打过关，所以只提醒、不改数）


def estimate_sanity(snap: dict | None) -> int | None:
    """快照 → 现在大概多少：没到上限的按 6 分钟 1 点往上加（最多加到上限）；超上限的（用了理智液、升级）不会再涨。"""
    if not snap or snap.get("current") is None:
        return None
    cur, cap = int(snap["current"]), int(snap.get("max") or 0)
    age = common.age_min(snap.get("at")) or 0
    if cap and cur < cap and age:
        cur = min(cap, cur + age // 6)
    return cur


def read_sanity() -> dict:
    """回首页 OCR 理智（不拿锁）。比上一份快照推出来的少 SUSPECT_GAP 以上 → 带 suspect、estimate、shot。"""
    prev = load("sanity")
    top, found, game = read_home()
    from maapilot.tools.screen import capture
    if not found:
        shot = capture(game, "sanity")[1]["path"]
        raise CliError("sanity_failed", "首页上没认出理智（当前/上限）", exit=FAILED, data={"shot": shot},
                       hint="看截图确认在首页；也可以跑一次 maa fight，结束时会顺手记下理智")
    snap = {"at": common.iso(), "current": found[0], "max": found[1], "source": "ocr"}
    est = estimate_sanity(prev)
    if est is not None and found[0] < est - SUSPECT_GAP:
        snap.update(suspect=True, estimate=est, shot=capture(game, "sanity")[1]["path"])
    common.write_json(snap_path("sanity"), snap)
    return snap


def sanity_now() -> dict:
    """`state sanity`：接了森空岛先读森空岛（不拿锁），没接或读不到再拿锁回首页 OCR。"""
    from maapilot.tools import skland_cmd
    err = None
    if skland_cmd.configured():
        try:
            snap = skland_cmd.sanity_now()
            if snap:
                return snap
        except CliError as e:
            err = e.message
    with common.device("state sanity"):
        snap = read_sanity()
    if err:
        snap["skland_error"] = err
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
    out = _sanity_out(sanity_now())
    if out.get("suspect"):
        out["hint"] = (f"读数可能错了：按上一次记下的推算现在约 {out['estimate']}。看截图（shot）里首页右上角的大号数字核对，"
                       "以截图为准；用户自己打过关的话读数低是正常的")
    return out


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
        if cur["elite"] is not None:                     # 现场读到的：森空岛滞后时补上（live_box）
            from maapilot.tools import live_box
            live_box.record(args.name, source="state op", elite=cur["elite"], level=cur["level"],
                            skill_level=cur["skill"])
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
    from maapilot.tools import skland_cmd
    skland_cmd.register_state(gs)
    p = gs.add_parser("sanity", help="识别理智")
    p.set_defaults(func=cmd_sanity, command="state sanity")
    p = gs.add_parser("show", help="读快照（不碰模拟器）")
    p.add_argument("kind", nargs="?", choices=KINDS)
    p.add_argument("--item", nargs="+", metavar="物品")
    p.add_argument("--op", nargs="+", metavar="干员")
    p.set_defaults(func=cmd_show, command="state show")
