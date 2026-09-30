"""Screen geometry without MaaFramework: the 1280x720 reference space, the 16:9 check and PNG sizes.

Every coordinate and ROI in MaaCrew is in a 1280x720 *reference space* (``REF_W`` x ``REF_H``).
MaaFramework scales screenshots to a 720 short side and maps click/swipe coordinates back to raw
device pixels, so any 16:9 device works.  Like MAA, only 16:9 screens are supported (``check_aspect``).
Kept free of MaaFramework imports so `maapilot doctor` and tests can use it.
"""
from __future__ import annotations

import struct

REF_W, REF_H = 1280, 720
FULL_ROI: tuple[int, int, int, int] = (0, 0, REF_W, REF_H)
ASPECT_TOLERANCE = 0.01  # relative; covers rounding such as 1366x768

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def check_aspect(width: int, height: int, tolerance: float = ASPECT_TOLERANCE) -> None:
    """Raise if a raw device resolution is not (landscape) 16:9."""
    if width <= 0 or height <= 0:
        raise RuntimeError(f"could not read device resolution ({width}x{height})")
    ratio = width / height
    target = REF_W / REF_H
    if abs(ratio - target) / target > tolerance:
        raise RuntimeError(
            f"device resolution {width}x{height} is not 16:9 (ratio {ratio:.3f}); "
            "like MAA, MaaCrew only supports 16:9 landscape screens — set the emulator "
            "to e.g. 1280x720 or 1920x1080")


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) from a PNG's IHDR chunk; ValueError if `data` is not a PNG."""
    if len(data) < 24 or not data.startswith(_PNG_SIG) or data[12:16] != b"IHDR":
        raise ValueError("not a PNG image")
    w, h = struct.unpack(">II", data[16:24])
    if w <= 0 or h <= 0:
        raise ValueError(f"bad PNG size {w}x{h}")
    return w, h


def check_point(x: int, y: int) -> None:
    """Raise ValueError unless (x, y) is an integer point inside the reference space."""
    if not (isinstance(x, int) and isinstance(y, int) and 0 <= x < REF_W and 0 <= y < REF_H):
        raise ValueError(f"坐标 ({x}, {y}) 超出参考坐标系 {REF_W}x{REF_H}（0 ≤ x < {REF_W}，0 ≤ y < {REF_H}）")


def check_roi(roi: tuple[int, int, int, int]) -> None:
    """Raise ValueError unless roi (x, y, w, h) has positive size and lies inside the reference space."""
    if len(roi) != 4 or not all(isinstance(v, int) for v in roi):
        raise ValueError(f"区域要是 4 个整数 x y w h：{roi}")
    x, y, w, h = roi
    if not (w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= REF_W and y + h <= REF_H):
        raise ValueError(f"区域 {list(roi)} 越界或宽高 ≤ 0（参考坐标系 {REF_W}x{REF_H}）")


def ref_to_device(x: int, y: int, resolution: tuple[int, int]) -> tuple[int, int]:
    """A reference-space point in raw device pixels (for the record only: MaaFramework maps clicks itself)."""
    w, h = resolution
    return (min(max(round(x * w / REF_W), 0), w - 1), min(max(round(y * h / REF_H), 0), h - 1))
