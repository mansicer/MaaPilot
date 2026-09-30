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
