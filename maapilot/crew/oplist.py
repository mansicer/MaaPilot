"""Locate an operator in the 干员 list and open their page."""
from __future__ import annotations
import time
from maapilot.crew.core import REF_W, Game, OcrHit

CLASS_TAB = {  # right sidebar (1240, y) on the operator list screen
    "ALL": 90, "先锋": 150, "近卫": 225, "重装": 290, "狙击": 360,
    "术师": 430, "医疗": 510, "辅助": 590, "特种": 670,
}
NAME_ROWS = [(0, 300, REF_W, 100), (0, 620, REF_W, 100)]  # OCR strips for the two card rows


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
    for h in g.ocr((20, 295, 80, 60)):
        t = h.text.strip().replace("O", "0").replace("I", "1").replace("l", "1").replace("|", "1")
        if t.isdigit():
            return int(t)
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
            if h.text == name:
                cx, cy = h.center
                g.tap(cx, cy - 150, delay=2.0)
                return
        if names == prev:
            break
        prev = names
        g.swipe(1120, 400, 160, 400, duration_ms=900, delay=1.2)
    g.save("open_operator_fail")
    raise LookupError(f"operator {name!r} not found in class {cls}")
