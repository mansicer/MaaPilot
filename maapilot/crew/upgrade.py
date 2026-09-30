"""Operator upgrade feature: level / promote / skill-rank an operator to a target.

Usage:
    python -m maapilot.crew.upgrade plan.json
plan.json: {"芬": {"elite": 1, "level": 55, "skill": 6}, ...}
"""
from __future__ import annotations

import itertools
import json
import re
import sys
import time

from maapilot.crew.core import Game
from maapilot.knowledge import gamedata
from maapilot.crew.oplist import open_operator

PROFESSION_CN = {"PIONEER": "先锋", "WARRIOR": "近卫", "TANK": "重装", "SNIPER": "狙击",
                 "CASTER": "术师", "MEDIC": "医疗", "SUPPORT": "辅助", "SPECIAL": "特种"}

# --- screen geometry (1280x720 reference space, see maapilot.crew.core) ----------
PANEL_ROI = (850, 80, 430, 120)        # LV / EXP / max-level block on the operator page
BTN_LEVEL_PLUS = (1210, 142)
BTN_ELITE = (1000, 275)
BTN_SKILL_UP = (1200, 400)
LVL_MAX_LABEL = (1150, 181)
LVL_CONFIRM = (1190, 492)
LVL_DO = (1170, 605)
ELITE_CONFIRM = (1080, 655)
SKILL_CONFIRM = (1125, 616)
STOP_WORDS = ("不足", "无法", "失败")


class UpgradeError(RuntimeError):
    pass


def _num(s: str) -> int:
    return int(s.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1"))


PANEL_TRIES = 3


def _max_candidates(t: str) -> list[int]:
    """The small "/70" under the LV dial: the slash may be dropped, and 7 sometimes OCRs as Z ("/ZO")."""
    t = t.lstrip("/").replace("o", "0").replace("I", "1").replace("l", "1")
    out = []
    for z in ("7", "2"):
        c = t.replace("Z", z)
        if c.isdigit() and int(c) not in out:
            out.append(int(c))
    return out


# The current level is the big yellow number left of "/cap". Only a tall text box centred in this area counts: the
# level-up toast puts its own small yellow badge ("8", ~32 px high) just above-right of it (F14).
BIG_LEVEL_MIN_H = 45
BIG_LEVEL_CX = (870, 990)
BIG_LEVEL_CY = (100, 170)
TOAST_WORDS = ("等级提升", "晋升")      # "干员等级提升" / promotion toast; it also covers the EXP bar's MAX
TOAST_WAITS = 5


def _big_level(h) -> bool:
    cx, cy = h.center
    return (h.box[3] >= BIG_LEVEL_MIN_H and BIG_LEVEL_CX[0] <= cx <= BIG_LEVEL_CX[1]
            and BIG_LEVEL_CY[0] <= cy <= BIG_LEVEL_CY[1])


def _parse_panel(hits, maxes=None) -> tuple[int | None, int | None]:
    cur = mx = None
    cur_h = 0
    at_max = False
    for h in hits:
        t = h.text.replace(" ", "").replace("O", "0")
        if 160 <= h.box[1] <= 200 and h.box[0] > 960 and mx is None:
            ok = [c for c in _max_candidates(t) if maxes is None or c in maxes]
            if ok:
                mx = ok[0]
        if _big_level(h) and h.box[3] > cur_h and re.fullmatch(r"[0-9OIl]{1,2}", t):
            cur, cur_h = _num(t), h.box[3]
        if t == "MAX" and h.box[0] > 1150 and h.box[1] < 160:  # EXP bar shows MAX at phase cap
            at_max = True
    if at_max and mx is not None:
        cur = mx                                  # the big yellow number OCRs badly (55 -> 65)
    return cur, mx


def _panel_hits(g: Game) -> list:
    """OCR the panel; while a level-up / promotion toast covers it, wait (at most TOAST_WAITS reads)."""
    hits = []
    for i in range(TOAST_WAITS):
        hits = g.ocr(PANEL_ROI)
        if not any(w in h.text for h in hits for w in TOAST_WORDS):
            break
        if i < TOAST_WAITS - 1:
            time.sleep(1)
    return hits


def read_panel(g: Game, maxes: list[int] | None = None, confirm: bool = False) -> tuple[int | None, int]:
    """Return (current level, max level of the current phase) from the operator page.
    maxes: the operator's phase caps; a max-level reading outside them counts as unread.
    confirm: the same (level, cap) must be read twice in a row (after a level-up / promotion).
    The current level is None when the big number cannot be read (a lone "1" often OCRs as nothing).
    At most PANEL_TRIES reads (+1 with confirm); no phase cap after that -> UpgradeError."""
    mx = None
    last = None
    tries = PANEL_TRIES + (1 if confirm else 0)
    for i in range(tries):
        cur, m = _parse_panel(_panel_hits(g), maxes)
        mx = m if m is not None else mx
        if cur is not None and m is not None:
            if not confirm or (cur, m) == last:
                return cur, m
            last = (cur, m)
        else:
            last = None
        if i < tries - 1:
            time.sleep(1)
    if mx is None:
        g.save("panel_fail")
        raise UpgradeError("cannot read level panel")
    return None, mx


def goto_operator_list(g: Game) -> None:
    """From any screen: navigation (drawer / home buttons, dialogs handled by maapilot.crew.nav) -> 干员."""
    from maapilot.crew.nav import goto
    for _ in range(3):
        goto(g, "干员")
        try:
            g.wait(r"等级", (820, 20, 120, 40), timeout=8)
            return
        except TimeoutError:
            continue
    raise UpgradeError("could not open the operator list")


def on_operator_page(g: Game) -> bool:
    return g.find("精英化", (900, 230, 200, 60)) is not None


def level_to_max(g: Game) -> None:
    g.tap(*BTN_LEVEL_PLUS, delay=1.5)
    g.wait(r"MAX", (1090, 160, 140, 45))
    g.tap(*LVL_MAX_LABEL, delay=1.0)
    g.wait(r"确定", (1100, 470, 180, 45))
    g.tap(*LVL_CONFIRM, delay=1.0)
    g.wait(r"提升等级", (1080, 580, 200, 50))
    for h in g.ocr((0, 380, 760, 160)):
        if any(w in h.text for w in STOP_WORDS):
            raise UpgradeError(f"level-up blocked: {h.text}")
    g.tap(*LVL_DO, delay=3.0)
    for _ in range(10):                  # a "LEVEL UP" result screen needs a tap to dismiss
        if on_operator_page(g):
            return
        g.tap(640, 360, delay=1.5)
    raise UpgradeError("did not return to operator page after level-up")


DIAL_TAPS = {2: (923, 60), 1: (923, 170), -1: (923, 480), -2: (923, 590)}   # numbers around the dial
DIAL_ROI = (820, 100, 220, 480)        # the dial column: neighbour above, big centre number, neighbour below
DIAL_BANDS = {"above": (110, 220, -1), "centre": (285, 390, 0), "below": (450, 570, 1)}  # hit centre y -> offset
TARGET_LABEL_ROI = (560, 395, 260, 60)  # "目标等级 LV n" on the left; lags behind while "计算中" shows
CALC_ROI = (1060, 400, 220, 110)        # "计算中.." on the right while the game recomputes the cost
DIAL_SWIPE = {1: ((925, 250), (925, 490)), -1: ((925, 490), (925, 250))}   # drag down = higher, ~4 levels
SWIPE_FROM = 6                           # |diff| at or above this: drag; below: tap the neighbours
DIAL_READ_MISSES = 3
DIAL_STALLS = 3
DIAL_MOVES = 40
CALC_WAITS = 4
_DIGIT_FIX = str.maketrans({"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "|": "1", "!": "1", "]": "1",
                            "[": "1", "{": "1", "}": "1", "亻": "1", "丨": "1", "｜": "1",
                            "S": "5", "B": "8", "G": "6"})
_DIGIT_AMBIG = {"Z": "27", "T": "17"}   # 7 often reads as Z, 1 (thin font) as T; T may also be a 7: try each


def _level_candidates(text: str) -> set[int]:
    """Possible levels in one OCR text: "LV|5" -> {15}; "Z" -> {2, 7}; "T2" -> {12, 72}; "亻3" -> {13}.
    Only the characters after "LV" count, and only a 1-2 digit result; a wrong guess still needs a second
    source agreeing on it (_dial_votes), so the fixes widen what can be read without loosening D113."""
    t = text.upper().replace(" ", "")
    m = re.search(r"[VⅤ]", t)
    if m:
        t = t[m.end():]
    t = t.translate(_DIGIT_FIX)
    if len(t) > 2:
        return set()
    out = set()
    for digits in itertools.product(*(_DIGIT_AMBIG.get(ch, ch) for ch in t)):
        c = "".join(digits)
        if re.fullmatch(r"\d{1,2}", c) and int(c) > 0:
            out.add(int(c))
    return out


def _dial_votes(dial_hits, label_hits, phase_max: int) -> tuple[int | None, list[str]]:
    """Vote over the centre number, its neighbours (shifted by -+1) and the left label: a level needs >= 2 sources
    and 1..phase_max. Returns (level or None, the texts seen per source) -- the texts go into error messages."""
    sources: dict[str, set[int]] = {}
    seen = []
    for band, (lo, hi, off) in DIAL_BANDS.items():
        hs = sorted((h for h in dial_hits if lo <= h.center[1] <= hi), key=lambda h: h.box[0])
        text = "".join(h.text for h in hs)
        if text:
            seen.append(f"{band}={text}")
            sources[band] = {c + off for c in _level_candidates(text)}
    if label_hits is not None:
        text = "".join(h.text for h in sorted(label_hits, key=lambda h: h.box[0]))
        if text:
            seen.append(f"label={text}")
            sources["label"] = _level_candidates(text)
    counts: dict[int, int] = {}
    for cands in sources.values():
        for c in cands:
            if 1 <= c <= phase_max:
                counts[c] = counts.get(c, 0) + 1
    best = max(counts.values(), default=0)
    winners = [c for c, n in counts.items() if n == best]
    return (winners[0] if best >= 2 and len(winners) == 1 else None), seen


def read_target(g: Game, phase_max: int) -> tuple[int | None, list[str]]:
    """Current target level on the dial (see _dial_votes), or None after a few tries. Waits while "计算中" shows
    (the left label is stale then); if it never clears, votes without the label."""
    seen: list[str] = []
    calc = 0
    for _ in range(CALC_WAITS + 3):
        computing = g.find(r"计算中", CALC_ROI) is not None
        if computing and calc < CALC_WAITS:
            calc += 1
            time.sleep(0.8)
            continue
        label = None if computing else g.ocr(TARGET_LABEL_ROI)
        level, seen = _dial_votes(g.ocr(DIAL_ROI), label, phase_max)
        if level is not None:
            return level, seen
        time.sleep(0.8)                       # dial animation still running
    return None, seen


def set_dial(g: Game, target: int, phase_max: int) -> None:
    """Move the level dial to `target`: drag (~4 levels) while far off, then tap the neighbours (+-1 / +-2).
    Bounded: DIAL_READ_MISSES unreadable reads in a row, DIAL_STALLS moves without a change in a row, or
    DIAL_MOVES moves in total -> UpgradeError (screenshot saved)."""
    misses = stalls = 0
    last = None
    seen: list[str] = []
    for _ in range(DIAL_MOVES + 1):
        cur, seen = read_target(g, phase_max)
        if cur is None:
            misses += 1
            if misses >= DIAL_READ_MISSES:
                g.save("dial_fail")
                raise UpgradeError(f"cannot read target level on the dial (OCR: {'; '.join(seen) or 'nothing'})")
            continue
        misses = 0
        if cur == target:
            return
        stalls = stalls + 1 if cur == last else 0
        if stalls >= DIAL_STALLS:
            g.save("dial_fail")
            raise UpgradeError(f"level dial does not move (stuck at {cur}, target {target})")
        last = cur
        diff = target - cur
        if abs(diff) >= SWIPE_FROM:
            (x1, y1), (x2, y2) = DIAL_SWIPE[1 if diff > 0 else -1]
            g.swipe(x1, y1, x2, y2, duration_ms=800, delay=1.0)
        else:
            g.tap(*DIAL_TAPS[max(-2, min(2, diff))], delay=0.7)
    g.save("dial_fail")
    raise UpgradeError(f"could not set target level {target} in {DIAL_MOVES} moves (now {last}; OCR: {'; '.join(seen)})")


def level_to(g: Game, target: int, phase_max: int) -> None:
    """Level up to an arbitrary target (<= phase cap) via the level dial (set_dial)."""
    if target >= phase_max:
        return level_to_max(g)
    g.tap(*BTN_LEVEL_PLUS, delay=1.5)
    g.wait(r"MAX", (1090, 160, 140, 45))
    set_dial(g, target, phase_max)
    g.wait(r"确定", (1100, 470, 180, 45))
    g.tap(*LVL_CONFIRM, delay=1.0)
    g.wait(r"提升等级", (1080, 580, 200, 50))
    for h in g.ocr((0, 380, 760, 160)):
        if any(w in h.text for w in STOP_WORDS):
            raise UpgradeError(f"level-up blocked: {h.text}")
    g.tap(*LVL_DO, delay=3.0)
    for _ in range(10):
        if on_operator_page(g):
            return
        if g.find(r"MAX", (1090, 160, 140, 45)):   # below the cap the game stays on the dial screen
            g.back()
        else:
            g.tap(640, 360, delay=1.5)
    raise UpgradeError("did not return to operator page after level-up")


def promote(g: Game) -> None:
    g.tap(*BTN_ELITE, delay=1.5)
    g.wait(r"是否晋升", (800, 540, 400, 60))
    for h in g.ocr((0, 540, 700, 160)):
        if any(w in h.text for w in STOP_WORDS):
            raise UpgradeError(f"promotion blocked: {h.text}")
    g.tap(*ELITE_CONFIRM, delay=4.0)
    for _ in range(12):  # skip the promotion animation
        g.tap(640, 360, delay=1.5)
        if on_operator_page(g):
            return
    raise UpgradeError("did not return to operator page after promotion")


def read_rank(g: Game) -> int | None:
    for h in g.ocr((1100, 340, 180, 80)):
        m = re.search(r"RANK\s*(\d)", h.text.replace("Z", "7").replace("l", "1"))
        if m:
            return int(m.group(1))
    if g.find(r"专精", (1140, 380, 120, 40)):          # 专精 button only exists at rank 7
        return 7
    return None


def skill_to(g: Game, target: int) -> int:
    rank = read_rank(g)
    if rank is not None and rank >= target:          # at rank 7 the button becomes 专精 (mastery)
        return rank
    if rank == 7:
        raise UpgradeError("mastery (专精) is not supported yet")
    g.tap(*BTN_SKILL_UP, delay=1.5)
    for _ in range(12):
        hit = g.wait(r"等级\d+到等级", (700, 540, 580, 60))
        m = re.search(r"等级(\d+)到等级", hit.text)
        cur = int(m.group(1))
        if cur >= target:
            g.back()
            return cur
        for h in g.ocr((700, 480, 580, 120)):
            if any(w in h.text for w in STOP_WORDS):
                raise UpgradeError(f"skill upgrade blocked at rank {cur}: {h.text}")
        g.tap(*SKILL_CONFIRM, delay=2.0)
    raise UpgradeError("skill loop did not converge")


def _reached(cur_elite: int, cur: int | None, elite: int, level: int) -> bool:
    return (cur_elite, cur if cur is not None else 1) >= (elite, level)     # every operator is at least Lv1


def _open_operator_page(g: Game, name: str) -> dict:
    """From any screen: 干员 list -> `name`'s operator page. Returns the operator's gamedata entry."""
    info = gamedata.operators()[name]
    goto_operator_list(g)
    open_operator(g, name, PROFESSION_CN[info["profession"]])
    for _ in range(3):                  # close the first-time "角色档案" tutorial if it shows
        if on_operator_page(g):
            break
        g.tap(156, 109, delay=1.5)
    return info


def read_operator(g: Game, name: str) -> dict:
    """Read-only: open `name`'s operator page and read {name, elite, level, skill}; taps nothing that consumes.
    level is None when the big number cannot be read, skill None when the rank cannot be read (or no skills).
    No phase cap on the panel -> UpgradeError (read_panel)."""
    info = _open_operator_page(g, name)
    maxes = info["max_levels"]
    cur, mx = read_panel(g, maxes)
    skill = read_rank(g) if info["skills"] > 0 else None
    return {"name": name, "elite": maxes.index(mx), "level": cur, "skill": skill}


LEVEL_REREADS = 2


def _level_after(g: Game, maxes: list[int], mx: int, lo: int, hi: int) -> tuple[int | None, list]:
    """The level after a level-up: a confirmed panel reading with the same phase cap and within [lo, hi] (the
    level never drops, and does not pass the target). Otherwise reread; still off -> None ("unread", F14).
    Returns (level or None, the readings seen)."""
    seen = []
    for _ in range(LEVEL_REREADS):
        cur, m = read_panel(g, maxes, confirm=True)
        seen.append(cur)
        if cur is not None and m == mx and lo <= cur <= hi:
            return cur, seen
    return None, seen


def upgrade_one(g: Game, name: str, elite: int, level: int, skill: int | None) -> dict:
    """Bring `name` to at least 精elite Lv level (and skill rank `skill`). Returns what the game confirmed:
    {name, elite, level, skill, reached, start}; level is None when the panel number cannot be read; start is the
    first reading {elite, level} (a lower bound for the write-back).
    On failure after the panel was read, the exception carries `reached` (the same dict, as far as it got),
    so a promotion that already happened is not lost. A level reading after levelling up that contradicts the
    level-up (below the level before, above the target, not the cap after levelling to the cap) counts as unread:
    UpgradeError (callers save the error_{name} screenshot), and `reached` carries level None and
    level_unread {read, expected}."""
    info = _open_operator_page(g, name)
    maxes = info["max_levels"]
    known = {"name": name, "elite": None, "level": None, "skill": None, "reached": False}
    try:
        cur, mx = read_panel(g, maxes)
        cur_elite = maxes.index(mx)
        known.update(elite=cur_elite, level=cur, start={"elite": cur_elite, "level": cur})
        print(f"[{name}] now 精{cur_elite} Lv{cur}/{mx}; target 精{elite} Lv{level} skill {skill}")
        if not _reached(cur_elite, cur, elite, level):
            while cur_elite < elite:
                if cur is None or cur < mx:
                    level_to_max(g)
                promote(g)
                _, mx = read_panel(g, maxes, confirm=True)
                if maxes.index(mx) != cur_elite + 1:
                    raise UpgradeError(f"promotion to 精{cur_elite + 1} not confirmed (panel max Lv{mx})")
                cur_elite += 1
                cur = 1                                       # promotion resets the level to 1
                known.update(elite=cur_elite, level=cur)
                print(f"[{name}] promoted -> 精{cur_elite} Lv{cur}/{mx}")
            if level > mx:
                raise UpgradeError(f"target Lv{level} exceeds phase max {mx}")
            if cur is None or cur < level:
                lo = level if level == mx else (cur or 1)     # levelling to the cap ends exactly at the cap
                level_to(g, level, mx)
                cur, seen = _level_after(g, maxes, mx, lo, level)
                known["level"] = cur
                if cur is None:
                    known["level_unread"] = {"read": seen, "expected": [lo, level]}
                    raise UpgradeError(f"cannot read the level after levelling up (read {seen}, expected Lv{lo}"
                                       + (f"-{level})" if lo < level else ")"))
                print(f"[{name}] leveled -> Lv{cur}/{mx}")
        known["reached"] = _reached(cur_elite, cur, elite, level)
        if skill and info["skills"] > 0:
            known["skill"] = skill_to(g, skill)
            print(f"[{name}] skill rank -> {known['skill']}")
        return dict(known)
    except Exception as e:
        if known["elite"] is not None:
            e.reached = dict(known)
        raise


def run_plan(plan: dict) -> list[dict]:
    g = Game()
    results = []
    for name, tgt in plan.items():
        try:
            results.append(upgrade_one(g, name, tgt.get("elite", 0), tgt["level"], tgt.get("skill")))
        except Exception as e:  # keep going with the next operator, report at the end
            g.save(f"error_{name}")
            results.append({"name": name, "error": str(e)})
            print(f"[{name}] ERROR: {e}")
    leave_list_unfiltered(g)
    return results


def leave_list_unfiltered(g: Game) -> None:
    """Leave the operator list unfiltered (MAA's OperBox scans it as-is). Best effort: errors are ignored."""
    try:
        goto_operator_list(g)
        if g.find(r"ALL", (1200, 70, 75, 40)) is None:
            g.tap(1240, 38, delay=1.2)
        g.tap(1240, 90, delay=1.0)
    except Exception:
        pass


if __name__ == "__main__":                  # python -m maapilot.crew.upgrade --addr <adb 地址> [--adb <adb>] plan.json
    from maapilot.crew import device
    plan_file = device.configure_from_argv(sys.argv[1:])[0]
    print(json.dumps(run_plan(json.load(open(plan_file, encoding="utf-8"))), ensure_ascii=False, indent=1))
