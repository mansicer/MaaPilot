"""Skill mastery (专精) in the 训练室: the screen-level steps (open the plan screen, pick the trainee, read the
confirm dialog, start a rank). Pure execution: no goals, no account state files -- the caller passes the depot
counts used for crafting and records what started (`maapilot op mastery` wraps it).

Materials: the confirm dialog shows have/need for each cost item; T3+ shortfalls are crafted with
maapilot.crew.craft, anything else (卷3, base materials) is reported as blocked.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable, Mapping, Union

from maapilot import paths
from maapilot.crew import overlays, reading
from maapilot.crew.core import REF_W, Game
from maapilot.knowledge import gamedata
from maapilot.crew.nav import goto_room
from maapilot.crew.oplist import CLASS_TAB, is_name

PROFESSION_CN = {"PIONEER": "先锋", "WARRIOR": "近卫", "TANK": "重装", "SNIPER": "狙击",
                 "CASTER": "术师", "MEDIC": "医疗", "SUPPORT": "辅助", "SPECIAL": "特种"}

SLOT = (80, 610)                 # trainee slot on the room view -> plan screen
PORTRAIT = (210, 230)            # trainee portrait on the plan screen -> picker
PICKER_CONFIRM = (1035, 680)
SKILL_BTN = {1: (408, 230), 2: (408, 440), 3: (408, 650)}
DLG_OK, DLG_CANCEL = (1148, 622), (952, 622)


class MasteryError(RuntimeError):
    pass


def _skill_costs(name: str, skill: int) -> list[list[tuple[str, int]]]:
    """[[ (item, count) ... ] for M1, M2, M3 ] from the game data."""
    ct = json.load(open(paths.DATA / "character_table.json", encoding="utf-8"))
    items = json.load(open(paths.DATA / "item_table.json", encoding="utf-8"))["items"]
    c = next(v for v in ct.values() if v["name"] == name)
    out = []
    for cond in c["skills"][skill - 1]["levelUpCostCond"]:
        out.append([(items[m["id"]]["name"], m["count"]) for m in cond["levelUpCost"]])
    return out


# 训练位上的状态字（"训练中"常读成"川练中"，按词认；同一处可能出现的别的字一起列上，免得认错）
SLOT_STATE_ROI = (140, 280, 140, 50)
SLOT_STATES = ("训练中", "训练完成", "空闲中", "训练室")
PLAN_MARK = ("协助者", (60, 350, 150, 50))          # 训练计划页（按词认：截得紧时读成"办助者"）


def dismiss_done(g: Game, wait_s: float = 8.0) -> str | None:
    """上一阶专精练完后（训练位显示"训练完成"），点训练位会先弹"技能 X 已完成专精等级N"（整屏，有几秒动画），点对勾关掉
    （认得的遮挡，表在 maapilot.crew.overlays）。等到出现训练计划页（协助者）或这个弹窗为止，最多 wait_s 秒。
    返回关掉的弹窗名 / None（没有弹窗）。"""
    deadline = time.time() + wait_s
    seen = None
    while time.time() < deadline:
        name = overlays.dismiss(g)
        if name:
            seen = seen or name
            continue
        if seen or g.find_word(*PLAN_MARK):
            return seen
        time.sleep(0.8)
    return seen


def slot_busy(g: Game, img=None) -> bool:
    """训练计划页上训练位写着"训练中"（按词认）。"""
    return g.find_word("训练中", SLOT_STATE_ROI, img, vocab=SLOT_STATES) is not None


def open_plan(g: Game) -> str:
    """Open the training plan screen. Returns 'training' or 'idle'."""
    goto_room(g, "训练室")
    g.tap(*SLOT, delay=3.0)
    if dismiss_done(g) and not g.find_word(*PLAN_MARK):   # 上一阶练完：点训练位先弹完成提示
        g.tap(*SLOT, delay=3.0)
    g.wait_word(*PLAN_MARK)
    time.sleep(1)
    return "training" if slot_busy(g) else "idle"


def current_trainee(g: Game) -> str | None:
    for h in g.ocr((0, 90, 370, 60)):
        if h.text in gamedata.operators():
            return h.text
    return None


def _selected(g: Game, name: str) -> bool:
    """选人页左上角显示的是 name（按词认）。"""
    return any(is_name(h.text, name) for h in g.ocr((0, 90, 370, 60)))


def confirm_shift(g: Game) -> None:
    """Accept the 排班调整 dialog that appears when the trainee is pulled out of another facility."""
    if g.find(r"班调整", (300, 560, 700, 120)):    # tight ROI clips the text, OCR then drops 排
        g.tap(960, 683, delay=3.0)


def pick_trainee(g: Game, name: str) -> None:
    info = gamedata.operators()[name]
    g.tap(*PORTRAIT, delay=3.0)
    g.wait(r"确认", (900, 630, 380, 80))
    if g.find(r"ALL", (1200, 70, 75, 40)) is None:
        g.tap(1240, 38, delay=1.2)
    g.tap(1240, CLASS_TAB[PROFESSION_CN[info["profession"]]], delay=1.5)
    for _ in range(3):
        g.swipe(400, 350, 1100, 350, duration_ms=300, delay=0.5)
    prev = None
    for _ in range(15):
        hits = [h for h in g.ocr((370, 60, 830, 600)) if h.score > 0.6]
        names = [h.text for h in hits]
        for h in hits:
            if is_name(h.text, name):
                x, y = h.center
                for _try in range(3):                # a tap toggles selection; make sure it ends selected
                    g.tap(x, y - 120, delay=1.5)
                    if _selected(g, name):
                        break
                else:
                    raise MasteryError(f"could not select {name} in the picker")
                ok = g.find(r"确认", (900, 630, 380, 80))
                g.tap_hit(ok, delay=3.0) if ok else g.tap(*PICKER_CONFIRM, delay=3.0)
                confirm_shift(g)
                g.wait_word(*PLAN_MARK)
                return
        # operators working in another facility show "工作中/<facility>" instead of their name:
        # preview each such card and read the name from the left panel
        for h in hits:
            if "工作中" in h.text:                   # the level digit often bleeds in ("8工作中")
                x, y = h.center
                g.tap(x, y - 100, delay=1.5)
                if _selected(g, name):
                    ok = g.find(r"确认", (900, 630, 380, 80))
                    g.tap_hit(ok, delay=3.0) if ok else g.tap(*PICKER_CONFIRM, delay=3.0)
                    confirm_shift(g)
                    g.wait_word(*PLAN_MARK)
                    return
        if names == prev:
            break
        prev = names
        g.swipe(1100, 350, 300, 350, duration_ms=800, delay=1.2)
    g.save("pick_trainee_fail")
    raise MasteryError(f"{name} not found in the trainee picker (not 精2, or busy elsewhere?)")


COSTS_ROI = (40, 655, 700, 45)                 # 确认框底下一排：训练时间 + 每样材料的"有/要"
# "有/要"白字 / 黄字在蓝底上，细斜杠常丢（57/15 → 5715、7/6 → 76）或读成"|"：再按 R、G 二值化成白底黑字读两遍，三种读法投票
COSTS_READS = (("min_rg", 150), ("min_rg", 180))
COLUMN_GAP = 40                                 # 两段字中心横向差这么多以上算不同的材料


def read_costs(g: Game, img) -> list[dict]:
    """确认框底下的材料一列一列读：[{x, pair: (有, 要) 或 None, texts: [各读法读到的字]}]，从左到右。"""
    reads = [g.ocr(COSTS_ROI, img)]
    for channel, th in COSTS_READS:
        b, roi = reading.binarize(img, COSTS_ROI, threshold=th, channel=channel)
        reads.append(g.ocr(roi, b))
    found = []
    for hits in reads:
        for h in hits:
            t = h.text.strip()
            if re.fullmatch(r"\d{1,2}:\d\d:\d\d", t) or not re.search(r"\d", t):   # 训练时间、杂点
                continue
            found.append((h.center[0], t))
    cols: list[dict] = []
    for x, t in sorted(found):
        if cols and x - cols[-1]["x"] < COLUMN_GAP:
            cols[-1]["texts"].append(t)
        else:
            cols.append({"x": x, "texts": [t]})
    for c in cols:
        c["pair"] = reading.vote([reading.parse_have_need(t) for t in c["texts"]])
    return cols


DIALOG_TEXT, DIALOG_ROI = r"是否专精该技能", (900, 540, 300, 50)


def started(g: Game) -> bool:
    """点了确定之后：看到"技能专精开始"，或训练位写着"训练中"（按词认）。"""
    img = g.screencap()
    return (g.find(r"技能专精开始", (0, 60, REF_W, 400), img) is not None
            or g.find_word("训练中", (0, 60, REF_W, 400), img, vocab=SLOT_STATES) is not None)


def open_dialog(g: Game, skill: int) -> dict | None:
    """Tap the skill's mastery button. Returns parsed dialog info, or None when no dialog opens
    (skill already at M3 / not eligible). have_need: the columns read as (有, 要), left to right; a column none of
    the reads could split is left out of have_need and listed in "unread" [(第几列, [读到的字])] (only when any)."""
    g.tap(*SKILL_BTN[skill], delay=2.5)
    img = g.screencap()
    if not g.find(DIALOG_TEXT, DIALOG_ROI, img):
        return None
    rank = hours = None
    for h in g.ocr(COSTS_ROI, img):                # training time tells the rank: ~8h / ~16h / ~24h
        m = re.fullmatch(r"\s*(\d{1,2}):\d\d:\d\d\s*", h.text)
        if m:
            hours = int(m.group(1))
            rank = 1 if hours < 12 else 2 if hours < 20 else 3
    cols = read_costs(g, img)
    out = {"target_rank": rank, "have_need": [c["pair"] for c in cols if c["pair"]], "hours": hours if rank else None}
    unread = [(i, c["texts"]) for i, c in enumerate(cols) if not c["pair"]]
    if unread:
        out["unread"] = unread
        out["columns"] = len(cols)
    return out


def align_by_need(costs: list[list[tuple[str, int]]], dlg: dict) -> tuple[int, list[tuple[int, int]], set[int]] | None:
    """确认框有读不出的列（dlg["unread"]）时，拿游戏数据里每一阶的"要"去对：最后 N 列（N = 这一阶材料数）里读出来的
    "要"都对得上、读不出的那列的数字串能按这个"要"切开（"2712" 要 12 → 有 27，`reading.split_by_need`），只有一阶对得上
    → (这一阶, 最后 N 列的 (有, 要), 靠切开得到的那几项的序号)；对不上或不止一阶 → None。切出来的"有"不一定准（"1/1"
    读成"171"会切成 17），调用方只拿它认是哪一阶，算缺多少时不信。"""
    unread = dict(dlg.get("unread") or [])
    if not unread:
        return None
    n_cols = dlg.get("columns") or (len(dlg["have_need"]) + len(unread))
    pairs = iter(dlg["have_need"])
    cols = [("text", unread[i]) if i in unread else ("pair", next(pairs, None)) for i in range(n_cols)]
    fits = []
    for r, cost in enumerate(costs, 1):
        if not cost or len(cols) < len(cost):
            continue
        got, guessed = [], set()
        for k, ((kind, v), (_, need)) in enumerate(zip(cols[-len(cost):], cost)):
            if kind == "pair":
                p = v if v and v[1] == need else None
            else:
                p = next((q for q in (reading.split_by_need(t, need) for t in v) if q), None)
                guessed.add(k)
            if p is None:
                break
            got.append(p)
        else:
            fits.append((r, got, guessed))
    return fits[0] if len(fits) == 1 else None


def rank_by_cost(costs: list[list[tuple[str, int]]], have_need: list[tuple[int, int]]) -> int | None:
    """The rank the dialog is for, from the "need" numbers it shows: the rank whose cost counts equal the last N
    tokens' need values. The count lists differ between ranks for every operator in the game data, so a match is
    unique; None when OCR missed a token (no rank matches) -- the caller then falls back to the training time."""
    found = [r for r, cost in enumerate(costs, 1)
             if cost and len(have_need) >= len(cost) and [n for _, n in have_need[-len(cost):]] == [n for _, n in cost]]
    return found[0] if len(found) == 1 else None


DepotCounts = Union[Mapping[str, int], Callable[[], Mapping[str, int]]]


def start_mastery(g: Game, name: str, skill: int, depot: DepotCounts, dry_run: bool = False,
                  on_idle: Callable[[], object] | None = None, max_rank: int | None = None) -> dict:
    """Start the next rank of <name>'s skill <skill>. Crafts T3+ shortfalls first.

    `max_rank`: the highest rank the caller may start (the goal's rank). A dialog for a higher rank means the goal
    is already reached (status "reached", nothing started); below 3 the rank must be identified from the dialog's
    costs (`rank_by_cost`) -- the training time alone can read low under training-speed bonuses -- or nothing is
    started (status "rank unknown", with a screenshot of the dialog in `shot`). When no dialog opens (M3, or the tap missed) the status is "no dialog" and a
    screenshot is saved (`shot`).

    `depot` is {中文名: 数量} for the crafting plan, or a zero-argument callable returning it -- called only
    when the dialog shows a shortfall (so a missing depot only matters when crafting is needed).
    `on_idle`, if given, is called once the room is found idle, before the trainee is picked (the caller
    uses it to mark whatever was training as finished).

    Returns {name, skill, status, ...}; with status "started" the result also has target_rank and hours,
    which is what the caller records as the room's current training."""
    from maapilot.crew.craft import plan_crafts, run_crafts_in_session
    if open_plan(g) == "training":
        return {"name": name, "skill": skill, "status": "room busy"}
    if on_idle is not None:
        on_idle()                                    # room is idle: whatever was training has finished
    pick_trainee(g, name)                            # the plan screen shows no name, so always (re)pick
    dlg = open_dialog(g, skill)
    if dlg is None:
        shot = g.save(f"mastery_nodialog_{name}_S{skill}")
        return {"name": name, "skill": skill, "status": "no dialog", "shot": str(shot)}
    costs = _skill_costs(name, skill)
    by_cost = rank_by_cost(costs, dlg["have_need"])
    guessed: set[int] = set()                        # 靠游戏数据的"要"切开的列（"有"不一定准）
    if by_cost is None:
        fit = align_by_need(costs, dlg)
        if fit:
            by_cost, pairs_fit, guessed = fit
            dlg = {**dlg, "have_need": pairs_fit}
    target = by_cost or dlg["target_rank"] or 1
    base = {"name": name, "skill": skill, "target_rank": target, "rank_by": "cost" if by_cost else "time"}
    if max_rank is not None and max_rank < 3 and (by_cost is None or by_cost > max_rank):
        shot = None if by_cost else str(g.save(f"mastery_rankunknown_{name}_S{skill}"))   # 给接管的 agent 看确认框
        g.tap(*DLG_CANCEL, delay=1.5)
        return {**base, "status": "reached" if by_cost else "rank unknown", **({"shot": shot} if shot else {})}
    need = costs[target - 1]
    pairs = dlg["have_need"][-len(need):]           # the last N tokens are the N cost items
    short = {item: n - have for (item, n), (have, _) in zip(need, pairs) if have < n}
    unsure = {need[k][0] for k in guessed} & set(short)
    result = {**base, "need": dict(need), "short": short}
    if short and (by_cost is None or unsure):        # OCR missed or misread a token: the pairing is unverified, so the
        shot = str(g.save(f"mastery_costs_{name}_S{skill}"))   # "short" may be wrong (10-13: 卷3 57/15 read as short)
        g.tap(*DLG_CANCEL, delay=1.5)
        return {**result, "short": None, "status": "costs unreadable", "shot": shot}
    if short:
        g.tap(*DLG_CANCEL, delay=1.5)
        cp = plan_crafts(short, dict(depot() if callable(depot) else depot), extra=True)
        result["craft"] = cp
        if cp["farm"] or not cp["batches"]:
            result["status"] = "blocked: need farming " + json.dumps(cp["farm"] or short, ensure_ascii=False)
            return result
        if dry_run:
            result["status"] = "would craft then start"
            return result
        run_crafts_in_session(g, cp["batches"])
        open_plan(g)
        pick_trainee(g, name)
        dlg = open_dialog(g, skill)
        if dlg is None:
            raise MasteryError("dialog did not reopen after crafting")
        pairs = dlg["have_need"][-len(need):]
        if any(have < n for (_, n), (have, _) in zip(need, pairs)):
            g.tap(*DLG_CANCEL, delay=1.5)
            result["status"] = "blocked: still short after crafting"
            return result
    if dry_run:
        g.tap(*DLG_CANCEL, delay=1.5)
        result["status"] = "would start"
        return result
    g.tap(*DLG_OK, delay=4.0)
    ok = started(g)
    if not ok and g.find(DIALOG_TEXT, DIALOG_ROI):  # 第一下没点上（10-13）：确认框还在，才再点一次（不会开两次）
        g.tap(*DLG_OK, delay=4.0)
        ok = started(g)
    if not ok:                                       # 没看到"开始"：关掉认得的遮挡，回训练计划页看训练位是不是在训练
        overlays.dismiss_all(g)
        ok = g.find_word(*PLAN_MARK) is not None and slot_busy(g)
    result["status"] = "started" if ok else "unknown (no confirmation text)"
    if ok:
        result["hours"] = dlg.get("hours")
    g.save(f"mastery_{name}_S{skill}")
    return result
