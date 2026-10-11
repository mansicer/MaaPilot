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
from maapilot.crew.core import REF_W, Game
from maapilot.knowledge import gamedata
from maapilot.crew.nav import goto_room
from maapilot.crew.oplist import CLASS_TAB

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


DONE_OK = (802, 583)          # "技能 X 已完成专精等级N" 弹窗下面的对勾


def dismiss_done(g: Game, wait_s: float = 8.0) -> str | None:
    """上一阶专精练完后（训练位显示"训练完成"），点训练位会先弹"技能 X 已完成专精等级N"（整屏，有几秒动画），点对勾关掉。
    等到出现训练计划页（协助者）或这个弹窗为止，最多 wait_s 秒。返回弹窗文字 / None（没有弹窗）。"""
    deadline = time.time() + wait_s
    text = None
    while time.time() < deadline:
        hit = g.find(r"已完成专精等级", (300, 480, 800, 80))
        if hit:
            text = hit.text
            g.tap(*DONE_OK, delay=2.5)
            continue
        if text or g.find(r"协助者", (60, 350, 150, 50)):
            return text
        time.sleep(0.8)
    return text


def open_plan(g: Game) -> str:
    """Open the training plan screen. Returns 'training' or 'idle'."""
    goto_room(g, r"训练室")
    g.tap(*SLOT, delay=3.0)
    if dismiss_done(g) and not g.find(r"协助者", (60, 350, 150, 50)):   # 上一阶练完：点训练位先弹完成提示
        g.tap(*SLOT, delay=3.0)
    g.wait(r"协助者", (60, 350, 150, 50))
    time.sleep(1)
    return "training" if g.find(r"训练中", (140, 280, 140, 50)) else "idle"


def current_trainee(g: Game) -> str | None:
    for h in g.ocr((0, 90, 370, 60)):
        if h.text in gamedata.operators():
            return h.text
    return None


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
            if h.text == name:
                x, y = h.center
                for _try in range(3):                # a tap toggles selection; make sure it ends selected
                    g.tap(x, y - 120, delay=1.5)
                    if g.find(re.escape(name), (0, 90, 370, 60)):
                        break
                else:
                    raise MasteryError(f"could not select {name} in the picker")
                ok = g.find(r"确认", (900, 630, 380, 80))
                g.tap_hit(ok, delay=3.0) if ok else g.tap(*PICKER_CONFIRM, delay=3.0)
                confirm_shift(g)
                g.wait(r"协助者", (60, 350, 150, 50))
                return
        # operators working in another facility show "工作中/<facility>" instead of their name:
        # preview each such card and read the name from the left panel
        for h in hits:
            if "工作中" in h.text:                   # the level digit often bleeds in ("8工作中")
                x, y = h.center
                g.tap(x, y - 100, delay=1.5)
                if g.find(re.escape(name), (0, 90, 370, 60)):
                    ok = g.find(r"确认", (900, 630, 380, 80))
                    g.tap_hit(ok, delay=3.0) if ok else g.tap(*PICKER_CONFIRM, delay=3.0)
                    confirm_shift(g)
                    g.wait(r"协助者", (60, 350, 150, 50))
                    return
        if names == prev:
            break
        prev = names
        g.swipe(1100, 350, 300, 350, duration_ms=800, delay=1.2)
    g.save("pick_trainee_fail")
    raise MasteryError(f"{name} not found in the trainee picker (not 精2, or busy elsewhere?)")


def open_dialog(g: Game, skill: int) -> dict | None:
    """Tap the skill's mastery button. Returns parsed dialog info, or None when no dialog opens
    (skill already at M3 / not eligible)."""
    g.tap(*SKILL_BTN[skill], delay=2.5)
    if not g.find(r"是否专精该技能", (900, 540, 300, 50)):
        return None
    rank = None
    for h in g.ocr((40, 655, 700, 45)):                # training time tells the rank: ~8h / ~16h / ~24h
        m = re.fullmatch(r"\s*(\d{1,2}):\d\d:\d\d\s*", h.text)
        if m:
            hours = int(m.group(1))
            rank = 1 if hours < 12 else 2 if hours < 20 else 3
    hours = hours if rank else None
    tokens = []
    for h in sorted(g.ocr((40, 655, 700, 45)), key=lambda h: h.box[0]):
        m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", h.text.replace("O", "0"))
        if m:
            tokens.append((int(m.group(1)), int(m.group(2))))
    return {"target_rank": rank, "have_need": tokens, "hours": hours}


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
    target = by_cost or dlg["target_rank"] or 1
    base = {"name": name, "skill": skill, "target_rank": target, "rank_by": "cost" if by_cost else "time"}
    if max_rank is not None and max_rank < 3 and (by_cost is None or by_cost > max_rank):
        shot = None if by_cost else str(g.save(f"mastery_rankunknown_{name}_S{skill}"))   # 给接管的 agent 看确认框
        g.tap(*DLG_CANCEL, delay=1.5)
        return {**base, "status": "reached" if by_cost else "rank unknown", **({"shot": shot} if shot else {})}
    need = costs[target - 1]
    pairs = dlg["have_need"][-len(need):]           # the last N tokens are the N cost items
    short = {item: n - have for (item, n), (have, _) in zip(need, pairs) if have < n}
    result = {**base, "need": dict(need), "short": short}
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
    ok = g.find(r"技能专精开始|训练中", (0, 60, REF_W, 400)) is not None
    result["status"] = "started" if ok else "unknown (no confirmation text)"
    if ok:
        result["hours"] = dlg.get("hours")
    g.save(f"mastery_{name}_S{skill}")
    return result
