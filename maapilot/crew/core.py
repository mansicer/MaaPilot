"""Thin wrapper around MaaFramework for driving Arknights on an Android device/emulator.

Screenshots, OCR (MAA's PaddleOCR models), clicks and swipes.  Everything the
higher-level features (operator upgrade, etc.) need, nothing more.

Coordinates: every coordinate and ROI is in the 1280x720 reference space of
``maapilot.crew.screen`` (re-exported here).  Which device to connect to comes from
``maapilot.crew.device`` (the caller configures it; MaaPilot reads private/config.yaml).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
from PIL import Image

from maa.controller import AdbController
from maa.define import MaaAdbInputMethodEnum, MaaAdbScreencapMethodEnum
from maa.pipeline import JOCR, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

from maapilot import paths
from maapilot.crew import device
from maapilot.crew.device import Connection
from maapilot.crew.screen import ASPECT_TOLERANCE, FULL_ROI, REF_H, REF_W, check_aspect, png_size  # noqa: F401  (re-export)


@dataclass
class OcrHit:
    text: str
    box: tuple[int, int, int, int]  # x, y, w, h
    score: float

    @property
    def center(self) -> tuple[int, int]:
        x, y, w, h = self.box
        return x + w // 2, y + h // 2


class Game:
    def __init__(self, debug: bool = True, conn: Connection | None = None, *, ocr: bool = True):
        """Connect to the device. ocr=False skips loading the OCR resource (a screenshot-only command is faster);
        ocr() then raises RuntimeError."""
        conn = conn or device.current()
        paths.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        Toolkit.init_option(str(paths.DEBUG_DIR))
        self.ctrl = AdbController(
            conn.adb,
            conn.address,
            screencap_methods=MaaAdbScreencapMethodEnum.RawWithGzip | MaaAdbScreencapMethodEnum.Encode,
            input_methods=MaaAdbInputMethodEnum.Maatouch | MaaAdbInputMethodEnum.AdbShell,
        )
        self.ctrl.set_screenshot_target_short_side(REF_H)  # MaaFw scales screenshots and maps clicks back
        if not self.ctrl.post_connection().wait().succeeded:
            raise RuntimeError("adb connection failed")
        if self.ctrl.resolution == (0, 0):  # raw resolution is known only after a screenshot
            self.ctrl.post_screencap().wait()
        check_aspect(*self.ctrl.resolution)
        self.res = self.tasker = None
        if ocr:
            self.res = Resource()
            if not self.res.post_bundle(str(paths.RESOURCE)).wait().succeeded:
                raise RuntimeError("resource load failed")
            self.tasker = Tasker()
            if not self.tasker.bind(self.res, self.ctrl):
                raise RuntimeError("tasker bind failed")
        self.debug = debug
        self._shot: Optional[np.ndarray] = None

    # ---- screen -----------------------------------------------------------
    def screencap(self) -> np.ndarray:
        self._shot = self.ctrl.post_screencap().wait().get()
        return self._shot

    @property
    def resolution(self) -> tuple[int, int]:
        """The device's raw resolution (clicks are given in the reference space; MaaFramework maps them)."""
        return tuple(self.ctrl.resolution)

    def save_to(self, path: Path, img: Optional[np.ndarray] = None, *, quality: int = 90) -> Path:
        """Save a screenshot (the last one unless `img` is given) as JPEG at `path`, creating its directory."""
        img = img if img is not None else (self._shot if self._shot is not None else self.screencap())
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img[:, :, ::-1]).save(path, "JPEG", quality=quality)
        return path

    def save(self, name: str) -> Path:
        img = self._shot if self._shot is not None else self.screencap()
        p = paths.DEBUG_DIR / f"{name}.png"
        Image.fromarray(img[:, :, ::-1]).save(p)
        return p

    # ---- ocr --------------------------------------------------------------
    def ocr(self, roi: tuple[int, int, int, int] = FULL_ROI, img: Optional[np.ndarray] = None,
            expected: Sequence[str] = ()) -> list[OcrHit]:
        if self.tasker is None:
            raise RuntimeError("Game(ocr=False) 没有加载 OCR 资源")
        img = img if img is not None else self.screencap()
        detail = self.tasker.post_recognition(JRecognitionType.OCR, JOCR(roi=roi, expected=list(expected)), img).wait().get()
        hits: list[OcrHit] = []
        for node in detail.nodes:
            reco = node.recognition
            if reco is None:
                continue
            for r in reco.all_results:
                raw = r.raw if hasattr(r, "raw") else getattr(r, "__dict__", {})
                hits.append(OcrHit(text=str(r.text), box=tuple(r.box), score=float(getattr(r, "score", 0.0))))
        return hits

    def find(self, pattern: str, roi=FULL_ROI, img=None) -> Optional[OcrHit]:
        rx = re.compile(pattern)
        for h in self.ocr(roi, img):
            if rx.search(h.text):
                return h
        return None

    def wait(self, pattern: str, roi=FULL_ROI, timeout: float = 15, interval: float = 0.8) -> OcrHit:
        t0 = time.time()
        while time.time() - t0 < timeout:
            h = self.find(pattern, roi)
            if h:
                return h
            time.sleep(interval)
        self.save("timeout")
        raise TimeoutError(f"'{pattern}' not found within {timeout}s (screenshot saved)")

    # ---- input ------------------------------------------------------------
    def tap(self, x: int, y: int, delay: float = 0.8) -> None:
        self.ctrl.post_click(int(x), int(y)).wait()
        time.sleep(delay)

    def tap_hit(self, hit: OcrHit, delay: float = 0.8) -> None:
        self.tap(*hit.center, delay=delay)

    def swipe(self, x1, y1, x2, y2, duration_ms: int = 400, delay: float = 0.8) -> None:
        self.ctrl.post_swipe(int(x1), int(y1), int(x2), int(y2), duration_ms).wait()
        time.sleep(delay)

    def long_press(self, x: int, y: int, duration_ms: int, delay: float = 0.5) -> None:
        self.ctrl.post_touch_down(int(x), int(y)).wait()
        time.sleep(duration_ms / 1000)
        self.ctrl.post_touch_up().wait()
        time.sleep(delay)

    def key_back(self, delay: float = 0) -> None:
        """The Android back key (KEYCODE_BACK)."""
        if not self.ctrl.post_click_key(4).wait().succeeded:
            raise RuntimeError("back key failed")
        time.sleep(delay)

    def input_text(self, text: str, delay: float = 0) -> None:
        """Type into the focused input box. 待真机核对：adb shell input text 不支持非 ASCII。"""
        if not self.ctrl.post_input_text(text).wait().succeeded:
            raise RuntimeError("input text failed")
        time.sleep(delay)

    def back(self, delay: float = 1.2) -> None:
        self.tap(44, 40, delay)
