"""Thin wrapper around MaaFramework for driving Arknights on an Android device/emulator.

Screenshots, OCR (MAA's PaddleOCR models), clicks and swipes.  Everything the
higher-level features (operator upgrade, etc.) need, nothing more.  `ImageGame` runs the same recognition on an
image file without a device (screenshot regression tests, `screen ocr --image`).

Coordinates: every coordinate and ROI is in the 1280x720 reference space of
``maapilot.crew.screen`` (re-exported here).  Which device to connect to comes from
``maapilot.crew.device`` (the caller configures it; MaaPilot reads private/config.yaml).
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
from PIL import Image

from maa.controller import AdbController
from maa.define import LoggingLevelEnum, MaaAdbInputMethodEnum, MaaAdbScreencapMethodEnum
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


RECENT_OCR = 12        # 取证 .json 里记最近几次识别
DEBUG_KEEP = 300       # private/debug 里带时间的取证截图最多留几张（连同 .json）
_STAMPED = re.compile(r"_\d{8}-\d{6}(-\d+)?\.png$")


def _hit_json(h: "OcrHit") -> dict:
    return {"text": h.text, "box": list(h.box), "score": round(h.score, 3)}


def prune_debug(keep: int = DEBUG_KEEP) -> None:
    """只留最近 keep 张带时间的取证截图（和旁边的 .json）；别的文件（MaaFramework 日志等）不动。"""
    try:
        shots = sorted((p for p in paths.DEBUG_DIR.glob("*.png") if _STAMPED.search(p.name)),
                       key=lambda p: p.stat().st_mtime)
        for p in shots[:-keep] if len(shots) > keep else []:
            p.unlink(missing_ok=True)
            p.with_suffix(".json").unlink(missing_ok=True)
    except OSError:
        pass


class Game:
    def __init__(self, debug: bool = True, conn: Connection | None = None, *, ocr: bool = True,
                 readonly: bool = False):
        """Connect to the device. ocr=False skips loading the OCR resource (a screenshot-only command is faster);
        ocr() then raises RuntimeError.

        readonly=True connects with no input method at all (MaaAdbInputMethodEnum.Null): MaaFramework starts no
        input server (maatouch / minitouch) on the device, so a screenshot or OCR can run while MAA or another
        MaaPilot command is driving the game without the lock. tap / swipe / key / text then raise RuntimeError."""
        conn = conn or device.current()
        paths.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        Toolkit.init_option(str(paths.DEBUG_DIR))
        Tasker.set_stdout_level(LoggingLevelEnum.Off)   # MaaFramework 的日志只写进 DEBUG_DIR/maa.log，不刷到命令输出里
        self.ctrl = AdbController(
            conn.adb,
            conn.address,
            screencap_methods=MaaAdbScreencapMethodEnum.RawWithGzip | MaaAdbScreencapMethodEnum.Encode,
            input_methods=(MaaAdbInputMethodEnum.Null if readonly
                           else MaaAdbInputMethodEnum.Maatouch | MaaAdbInputMethodEnum.AdbShell),
        )
        self.readonly = readonly
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
        """失败取证：截图存成 private/debug/<name>_<时间>.png（带时间，同一个干员 / 物品再失败不会盖掉上一次的），
        旁边的同名 .json 是最近几次识别读到了什么（`recent`）和整张图的识别结果（`screen`）。
        只留最近 DEBUG_KEEP 张。"""
        img = self._shot if self._shot is not None else self.screencap()
        paths.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        p = paths.DEBUG_DIR / f"{name}_{stamp}.png"
        n = 1
        while p.exists():                                  # 同一秒存了两张
            n += 1
            p = paths.DEBUG_DIR / f"{name}_{stamp}-{n}.png"
        Image.fromarray(img[:, :, ::-1]).save(p)
        try:
            self._save_hits(p.with_suffix(".json"), name, img)
        except Exception:                                  # noqa: BLE001 - 取证写不了不影响主流程
            pass
        prune_debug()
        return p

    def _save_hits(self, path: Path, name: str, img: np.ndarray) -> None:
        recent = list(self._recent_ocr())
        screen = None
        if self.tasker is not None:
            screen = [_hit_json(h) for h in self.ocr(FULL_ROI, img)]
        path.write_text(json.dumps({"name": name, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "recent": recent,
                                    "screen": screen}, ensure_ascii=False, indent=1), encoding="utf-8")

    def _recent_ocr(self) -> deque:
        """最近 RECENT_OCR 次识别 {roi, hits}（存取证时写进 .json）。"""
        d = self.__dict__.get("_recent")
        if d is None:
            d = self.__dict__["_recent"] = deque(maxlen=RECENT_OCR)
        return d

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
        self._recent_ocr().append({"roi": list(roi), "hits": [_hit_json(h) for h in hits]})
        return hits

    def find(self, pattern: str, roi=FULL_ROI, img=None) -> Optional[OcrHit]:
        rx = re.compile(pattern)
        for h in self.ocr(roi, img):
            if rx.search(h.text):
                return h
        return None

    def find_word(self, word: str, roi=FULL_ROI, img=None, vocab: Sequence[str] = ()) -> Optional[OcrHit]:
        """像 find，但按词认：OCR 读错一个字也算（三个字以上的词；`reading.matches`）。
        vocab 写同一处可能出现的别的词，免得把差一个字的别的词认成 word。"""
        from maapilot.crew import reading
        for h in self.ocr(roi, img):
            if reading.matches(h.text, word, vocab):
                return h
        return None

    def wait(self, pattern: str, roi=FULL_ROI, timeout: float = 15, interval: float = 0.8, *,
             overlays: bool = True) -> OcrHit:
        """等 pattern 出现。没等到的每一轮先看是不是被认得的遮挡挡住了（专精完成提示、获得物资、公告……，
        `maapilot.crew.overlays`），是就关掉再看；overlays=False 不管遮挡（等的就是遮挡本身时）。"""
        return self._wait(lambda: self.find(pattern, roi), repr(pattern), timeout, interval, overlays)

    def wait_word(self, word: str, roi=FULL_ROI, timeout: float = 15, interval: float = 0.8, *,
                  vocab: Sequence[str] = (), overlays: bool = True) -> OcrHit:
        """同 wait，按词认（find_word）。"""
        return self._wait(lambda: self.find_word(word, roi, vocab=vocab), repr(word), timeout, interval, overlays)

    def _wait(self, finder, what: str, timeout: float, interval: float, overlays: bool) -> OcrHit:
        t0 = time.time()
        while time.time() - t0 < timeout:
            h = finder()
            if h:
                return h
            if overlays:
                from maapilot.crew import overlays as _ov
                if _ov.dismiss(self, img=self._shot):
                    continue
            time.sleep(interval)
        self.save("timeout")
        raise TimeoutError(f"{what} not found within {timeout}s (screenshot saved)")

    # ---- input ------------------------------------------------------------
    def _writable(self) -> None:
        if self.readonly:
            raise RuntimeError("Game(readonly=True) 只能截图、识字，不能操作")

    def tap(self, x: int, y: int, delay: float = 0.8) -> None:
        self._writable()
        self.ctrl.post_click(int(x), int(y)).wait()
        time.sleep(delay)

    def tap_hit(self, hit: OcrHit, delay: float = 0.8) -> None:
        self.tap(*hit.center, delay=delay)

    def swipe(self, x1, y1, x2, y2, duration_ms: int = 400, delay: float = 0.8) -> None:
        self._writable()
        self.ctrl.post_swipe(int(x1), int(y1), int(x2), int(y2), duration_ms).wait()
        time.sleep(delay)

    def long_press(self, x: int, y: int, duration_ms: int, delay: float = 0.5) -> None:
        self._writable()
        self.ctrl.post_touch_down(int(x), int(y)).wait()
        time.sleep(duration_ms / 1000)
        self.ctrl.post_touch_up().wait()
        time.sleep(delay)

    def key_back(self, delay: float = 0) -> None:
        """The Android back key (KEYCODE_BACK)."""
        self._writable()
        if not self.ctrl.post_click_key(4).wait().succeeded:
            raise RuntimeError("back key failed")
        time.sleep(delay)

    def input_text(self, text: str, delay: float = 0) -> None:
        """Type into the focused input box. 待真机核对：adb shell input text 不支持非 ASCII。"""
        self._writable()
        if not self.ctrl.post_input_text(text).wait().succeeded:
            raise RuntimeError("input text failed")
        time.sleep(delay)

    def back(self, delay: float = 1.2) -> None:
        self.tap(44, 40, delay)


# ---- 不连设备：在图片上跑识别 ------------------------------------------------
def load_image(path: Path | str) -> np.ndarray:
    """图片文件 → BGR 数组（和 MaaFramework 截图一样的格式），缩到 1280×720 参考坐标系（要求 16:9）。"""
    im = Image.open(path).convert("RGB")
    check_aspect(*im.size)
    if im.size != (REF_W, REF_H):
        im = im.resize((REF_W, REF_H))
    return np.ascontiguousarray(np.asarray(im)[:, :, ::-1])


def _image_controller(game: "ImageGame"):
    """MaaFramework 的自定义控制器：截图返回 game 当前的图片，点击之类直接返回成功。
    （MaaFramework 自带的 DbgController 读图片目录，但 macOS 的 maafw 包里没有它的库。）Tasker 要绑一个控制器才能识别。"""
    from maa.controller import CustomController

    class Ctrl(CustomController):
        def connect(self) -> bool: return True
        def request_uuid(self) -> str: return "maapilot-image"
        def start_app(self, intent: str) -> bool: return True
        def stop_app(self, intent: str) -> bool: return True
        def screencap(self) -> np.ndarray: return game.image
        def click(self, x, y) -> bool: return True
        def swipe(self, x1, y1, x2, y2, duration) -> bool: return True
        def touch_down(self, contact, x, y, pressure) -> bool: return True
        def touch_move(self, contact, x, y, pressure) -> bool: return True
        def touch_up(self, contact) -> bool: return True
        def click_key(self, keycode) -> bool: return True
        def input_text(self, text) -> bool: return True
        def key_down(self, keycode) -> bool: return True
        def key_up(self, keycode) -> bool: return True

    return Ctrl()


class ImageGame(Game):
    """不连设备的 Game：截图就是给定的图片，识别走和真机同一套 MaaFramework OCR（同一份资源、同样的参数）。

    给截图回归测试和 `screen ocr --image` 用。点击、滑动不发到任何地方，只记在 `self.actions` 里（不等待），
    所以读页面的函数（`state.home_sanity`、`mastery.open_dialog` ……）可以原样在图片上跑。
    `show(path)` 换下一张图；`log_dir` 是 MaaFramework 写日志的目录（默认 private/debug，和真机一样）。
    OCR 模型不在（resource/model/ocr 没装）时抛 RuntimeError。"""

    def __init__(self, image: Path | str | np.ndarray, *, log_dir: Path | None = None):
        if not (paths.OCR_MODEL / "rec.onnx").exists():
            raise RuntimeError(f"OCR 模型不在 {paths.OCR_MODEL}")
        log_dir = Path(log_dir) if log_dir else paths.DEBUG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        Toolkit.init_option(str(log_dir))
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        self.image = image if isinstance(image, np.ndarray) else load_image(image)
        self.actions: list[tuple] = []
        self.ctrl = _image_controller(self)
        if not self.ctrl.post_connection().wait().succeeded:
            raise RuntimeError("image controller connection failed")
        self.readonly = False
        self.res = Resource()
        if not self.res.post_bundle(str(paths.RESOURCE)).wait().succeeded:
            raise RuntimeError("resource load failed")
        self.tasker = Tasker()
        if not self.tasker.bind(self.res, self.ctrl):
            raise RuntimeError("tasker bind failed")
        self.debug = False
        self._shot = None

    def show(self, image: Path | str | np.ndarray) -> None:
        """换成下一张图（模拟点击之后页面变了）。"""
        self.image = image if isinstance(image, np.ndarray) else load_image(image)
        self._shot = None

    def screencap(self) -> np.ndarray:
        self._shot = self.image
        return self._shot

    @property
    def resolution(self) -> tuple[int, int]:
        return REF_W, REF_H

    def save(self, name: str) -> Path:
        self.actions.append(("save", name))
        return Path(f"{name}.png")

    def tap(self, x: int, y: int, delay: float = 0.8) -> None:
        self.actions.append(("tap", int(x), int(y)))

    def swipe(self, x1, y1, x2, y2, duration_ms: int = 400, delay: float = 0.8) -> None:
        self.actions.append(("swipe", int(x1), int(y1), int(x2), int(y2)))

    def long_press(self, x: int, y: int, duration_ms: int, delay: float = 0.5) -> None:
        self.actions.append(("long_press", int(x), int(y), int(duration_ms)))

    def key_back(self, delay: float = 0) -> None:
        self.actions.append(("back",))

    def input_text(self, text: str, delay: float = 0) -> None:
        self.actions.append(("text", text))
