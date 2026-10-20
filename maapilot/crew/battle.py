"""Battle-result helpers: star count from the 行动结束 screen (3 hexagons at y≈323)."""
from __future__ import annotations

import numpy as np

STAR_XS = (100, 172, 245)
STAR_Y = 323


def stars_from_results(img_bgr: np.ndarray) -> int:
    """img is a BGR screenshot of the mission-results screen. A lit hexagon is cyan-ish
    (B and G clearly above R); an unlit one is grey. Returns 0-3."""
    n = 0
    for x in STAR_XS:
        b, g, r = (int(v) for v in img_bgr[STAR_Y, x])
        if b > r + 40 and g > r + 40:
            n += 1
    return n


def detail_mode(img_bgr: np.ndarray) -> str:
    """关卡详情页现在是哪个模式（`maa copilot --raid raid` 开打前核对用，只看图、不点）→
    "raid"（切换按钮是切回常规的那个：现在是突袭 / 险地）/ "normal"（按钮是"突袭"：现在是常规）/
    "unknown"（两个按钮都认不出：不在详情页，例如已经进了编队页，或这关没有突袭）。
    复用 clears 的模板和区域（MODE_ROI、TPL_THRESHOLD）。"""
    from maapilot.crew import clears
    raid_btn = max(clears.match(img_bgr, n, clears.MODE_ROI)[0] for n in ("RaidDifficulty", "RaidDifficulty-Chapter15"))
    normal_btn = max(clears.match(img_bgr, n, clears.MODE_ROI)[0]
                     for n in ("NormalDifficulty", "NormalDifficulty-Chapter15"))
    if max(raid_btn, normal_btn) < clears.TPL_THRESHOLD:
        return "unknown"
    return "raid" if normal_btn > raid_btn else "normal"
