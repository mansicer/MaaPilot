"""Locate an operator in the 干员 list and open their page."""
from __future__ import annotations

from collections import Counter

from maapilot.crew import reading
from maapilot.crew.core import REF_W, Game, OcrHit

CLASS_TAB = {  # right sidebar (1240, y) on the operator list screen
    "ALL": 90, "先锋": 150, "近卫": 225, "重装": 290, "狙击": 360,
    "术师": 430, "医疗": 510, "辅助": 590, "特种": 670,
}
_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "|": "1"})
FIRST_LEVEL_ROI = (26, 308, 54, 38)                 # 第一张卡左下角的等级数字（"LV"下面、黄圈里面）
FIRST_LEVEL_READS = ((180, 2), (180, 3), (210, 2))  # (白字的阈值, 放大几倍)
NAME_ROWS = [(0, 300, REF_W, 100), (0, 620, REF_W, 100)]  # OCR strips for the two card rows


def is_name(text: str, name: str) -> bool:
    """这段字是不是干员 name：原样相同，或按词认（读错一个字也算，三个字以上的名字；全体干员名做词表，
    差一个字的别的干员不会认错）。"""
    if text.strip() == name:
        return True
    from maapilot.crew import reading
    from maapilot.knowledge import gamedata
    return reading.matches(text, name, gamedata.operators())


def _names_on_screen(g: Game, img=None) -> list[OcrHit]:
    img = img if img is not None else g.screencap()
    hits = []
    for roi in NAME_ROWS:
        hits += g.ocr(roi, img)
    return hits


def _scroll_to_start(g: Game) -> None:
    for _ in range(3):
        g.swipe(200, 400, 1100, 400, duration_ms=300, delay=0.6)


def _first_card_level(g: Game) -> int | None:
    """The first card's level, None when unread (then the sort direction is unknown; the scan still works)."""
    # 原样认不稳：框得紧，新的 OCR 模型（PP-OCRv6）把"90"切成"9"和杂点；框松一点，旧模型又只剩一个"o"。
    # 所以只抠白字（等级是白的，外面一圈是黄的）、放大了认几遍，取多数。
    img = g.screencap()
    seen: Counter[int] = Counter()
    for threshold, scale in FIRST_LEVEL_READS:
        clean, roi = reading.binarize(img, FIRST_LEVEL_ROI, threshold=threshold, channel="min", scale=scale)
        for hit in g.ocr(roi, clean):
            t = hit.text.strip().translate(_DIGIT_FIX)    # "90" 读成 "9o"（小写 o，10-12 真机）
            if t.isdigit() and 1 <= int(t) <= 90:
                seen[int(t)] += 1
                break
    top = seen.most_common(2)
    if top and (len(top) == 1 or top[0][1] > top[1][1]):
        return top[0][0]
    g.save("first_card_level_fail")
    return None


def open_operator(g: Game, name: str, cls: str, sort_asc: bool = True, max_pages: int = 40) -> None:
    """Assumes we are on the operator list screen (home -> 干员)."""
    if g.find(r"ALL", (1200, 70, 75, 40)) is None:   # sidebar collapsed from a previous run
        g.tap(1240, 38, delay=1.2)
    g.tap(1240, CLASS_TAB[cls], delay=1.2)
    g.tap(868, 38, delay=1.0)            # sort by level (tapping again flips direction)
    _scroll_to_start(g)
    first = _first_card_level(g)
    if first is not None and first > 1:  # highest level first => descending; flip
        g.tap(868, 38, delay=1.0)
        _scroll_to_start(g)
    g.tap(1240, 38, delay=1.2)           # 收起 the sidebar so the last column is not clipped
    prev = None
    for _ in range(max_pages):
        hits = _names_on_screen(g)
        names = [h.text for h in hits]
        for h in hits:
            if is_name(h.text, name):
                cx, cy = h.center
                g.tap(cx, cy - 150, delay=2.0)
                return
        if names == prev:
            break
        prev = names
        g.swipe(1120, 400, 160, 400, duration_ms=900, delay=1.2)
    g.save("open_operator_fail")
    raise LookupError(f"operator {name!r} not found in class {cls}")
