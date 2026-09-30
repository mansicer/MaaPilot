"""基建总览识别（决策 D144）：从首页进基建，只读总览上每张房间卡的类型、等级、状态和进驻人数，回首页。

只读：只点首页的"基建"和总览顶栏的首页按钮（离开时确认"离开罗德岛基建"，页面规则 leave_base_confirm），
其余只左右拖动总览。**从不点房间卡**：有产物可收的卡第一下点就是收取（见 maapilot.crew.manufacture）。
拖动从卡片下方的空白处起手（`SWIPE_Y`），不在卡片上按下。进驻干员不读（D144）；产物名总览上没有，不读。

总览比一屏宽：先拖到最左，再每次向右拖约半屏、每屏读一次，直到画面不再变化。同一张卡可能出现在相邻两屏里：
按"类型相同、同一行、等级相同"的卡配对，算出两屏的横向位移（有歧义时用像素比对选），换算成整张总览的坐标后去重。

每张卡读的东西（真机截图 2026-09-29，tests/fixtures/infrast/）：
- 类型：卡片左上的房间名（全屏 OCR）；控制中枢是大卡，名字下面是"VER 5.0"式的等级。
- 等级：名字右边的竖条数（贸易站 3 条 = 3 级）。按像素数：名字那一行里又高又窄、等距排开的亮条。
- 状态：名字下面的一行小字（生产中 / 已饱和 / 休息中 …），OCR 常错一两个字，按已知状态的共有字归一。
- 进驻：右下的"iN/M"。彩色大数字 OCR 常读不全，读不准就不给（None），不猜。
左上的"进驻总览 / 建造模式"和右上的通知按钮是固定的半透明浮层，压在它们下面、或被屏幕边缘截断的卡这一屏不读。

`read_*` / `merge` 是纯函数（截图 + OCR），离线可测；`scan` 吃 Game（鸭子类型，测试用假对象）。截图是 BGR。
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Callable

import numpy as np

from maapilot.crew.screen import FULL_ROI, REF_W

# --- 页面（1280x720 参考坐标，真机截图 2026-09-29） ----------------------------------------------------------
OVERVIEW_MARK = (r"进驻总览", (20, 95, 140, 50))       # 同 nav.goto_room / manufacture.open_panel
OVERLAYS = ((0, 80, 245, 170),                        # 进驻总览 / 建造模式（固定、半透明）
            (1170, 70, 110, 95))                       # 右上的 EMERGENCY / NOTIFICATION
TOP_BAR_H = 62                                          # 无人机 / 电力 / 龙门币
SWIPE_Y = 705                                           # 最下一排卡片的下沿约 670：在空白处拖。待真机核对
SWIPE_RIGHT = ((1050, SWIPE_Y), (500, SWIPE_Y))        # 看右边：内容向左移约半屏
SWIPE_LEFT = ((250, SWIPE_Y), (1150, SWIPE_Y))         # 回到最左
MAX_SCREENS = 6
SAME_SCREEN_DIFF = 4.0                                  # 两屏卡片区的平均灰度差低于它 = 没拖动（到头了）

# 房间名 → 类型（building_data.rooms 的键，小写）。名字是游戏界面上的，稳定
ROOMS = {"控制中枢": "control", "贸易站": "trading", "制造站": "manufacture", "发电站": "power", "宿舍": "dormitory",
         "会客室": "meeting", "加工站": "workshop", "办公室": "hire", "训练室": "training", "活动室": "private"}
# 总览上见过的状态字（真机 2026-09-29）；联络中是办公室联络未完成时的写法。待真机核对
STATUSES = ("生产中", "获取中", "已饱和", "发电中", "休息中", "空闲中", "训练中", "联络完成", "联络中", "使用中", "招待中")

CHAR_W = 20                 # 房间名每个字的宽度（像素，名字框左沿起算），等级条从这之后开始
BAR_WINDOW = 58             # 等级条最多 5 条、每条间距约 8
BAR_MIN_W, BAR_MAX_W = 5, 10
BAR_PITCH = (6, 11)
CARD_MIN_RIGHT = 175        # 名字框左沿到卡片右沿至少这么宽：不够的是被右边缘截断的卡


@dataclass
class Room:
    type: str                              # ROOMS 的值
    level: int | None = None
    status: str | None = None              # STATUSES 之一；读不出 None
    occupied: int | None = None            # 进驻人数；读不准 None
    capacity: int | None = None            # 读到的"/M"（与数据表对得上才留）
    x: int = 0                             # 名字框在这一屏的左上角（merge 后是整张总览的坐标）
    y: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("x"), d.pop("y")
        return d


# --- 数据表（可选：没有就不校验人数） ----------------------------------------------------------------------

@lru_cache(maxsize=1)
def _room_phases() -> dict[str, list[int]]:
    """{类型: [各级的最大进驻人数]}；没有数据表 → {}。"""
    import json
    from maapilot import paths
    try:
        rooms = json.load(open(paths.DATA / "building_data.json", encoding="utf-8"))["rooms"]
    except (OSError, ValueError, KeyError):
        return {}
    return {k.lower(): [int(p.get("maxStationedNum") or 0) for p in (v.get("phases") or [])] for k, v in rooms.items()}


def max_level(kind: str) -> int | None:
    phases = _room_phases().get(kind)
    return len(phases) if phases else None


def capacity(kind: str, level: int | None) -> int | None:
    """这一级的最大进驻人数；不知道 / 数据表里是 0（活动室）→ None。"""
    phases = _room_phases().get(kind)
    if not phases or level is None or not 1 <= level <= len(phases):
        return None
    return phases[level - 1] or None


# --- 纯函数：一屏的读数 ------------------------------------------------------------------------------------

def _overlaps(box, region) -> bool:
    x, y, w, h = box
    rx, ry, rw, rh = region
    return x < rx + rw and rx < x + w and y < ry + rh and ry < y + h


def room_names(hits) -> list[tuple[str, tuple[int, int, int, int]]]:
    """全屏 OCR 里的房间名 → [(名字, 名字框)]，名字框从名字的第一个字算起（OCR 常把等级条读成名字后面的 0 / m）。"""
    out = []
    for h in hits:
        for name in ROOMS:
            i = h.text.find(name)
            if i < 0:
                continue
            x, y, w, hh = h.box
            if i:                                          # 名字前面粘了别的字：按字数比例挪到名字开头
                x += round(w * i / max(len(h.text), 1))
            out.append((name, (x, y, w, hh)))
            break
    return out


def readable(name: str, box) -> bool:
    """这一屏能读的卡：不在顶栏里、不压在固定浮层下、没被左右边缘截断。"""
    x, y, _, _ = box
    if y < TOP_BAR_H or x < 5 or any(_overlaps(box, r) for r in OVERLAYS):
        return False
    return name == "控制中枢" or x + CARD_MIN_RIGHT <= REF_W


def _value(img: np.ndarray) -> np.ndarray:
    return img.max(axis=2).astype(int)                     # HSV 的 V


def read_level(img: np.ndarray, name: str, box) -> int | None:
    """名字右边的竖条数。条是又高又窄、等距排开的亮块；名字最后一个字的边、右上角的收取气泡都不算。读不出 None。"""
    x, y, _, h = box
    yc = y + h // 2
    x0 = x + len(name) * CHAR_W - 2
    band = _value(img[max(yc - 6, 0):yc + 7, x0:min(x0 + BAR_WINDOW, img.shape[1])])
    if band.size == 0:
        return None
    bg, hi = float(np.percentile(band, 20)), float(np.percentile(band, 98))   # 条可能占了窗口的一半：底色取低分位
    if hi - bg < 40:
        return None
    tall = (band > bg + 0.5 * (hi - bg)).sum(axis=0) >= 8
    runs, i = [], 0
    while i < len(tall):
        if tall[i]:
            j = i
            while j < len(tall) and tall[j]:
                j += 1
            if BAR_MIN_W <= j - i <= BAR_MAX_W:
                runs.append(i)
            i = j
        else:
            i += 1
    best: list[int] = []
    chain: list[int] = []
    for s in runs:                                         # 最长的等距链；一样长取靠右的（名字的边在左）
        chain = chain + [s] if chain and BAR_PITCH[0] <= s - chain[-1] <= BAR_PITCH[1] else [s]
        if len(chain) >= len(best):
            best = chain
    n = len(best)
    top = max_level(ROOMS[name]) or 5
    return n if 1 <= n <= top else None


def read_control_level(hits, box) -> int | None:
    """控制中枢：名字下面"VER 5.0"的大字，取第一个数字（OCR 常把 5.0 读成 5.2、5几）。"""
    x, y, w, _ = box
    for h in hits:
        hx, hy, hw, hh = h.box
        if y + 20 <= hy <= y + 110 and hx < x + w + 60 and hx + hw > x - 60:
            m = re.search(r"[1-5]", h.text)
            if m:
                return int(m.group())
    return None


def match_status(text: str) -> str | None:
    """OCR 读到的状态字 → STATUSES 之一：按共有的字（"中"不算）数，最多且唯一的那个；一个都不共有 → None。"""
    chars = set(text) - {"中"}
    scores = {s: len(chars & (set(s) - {"中"})) for s in STATUSES}
    best = max(scores.values())
    top = [s for s, v in scores.items() if v == best]
    return top[0] if best > 0 and len(top) == 1 else None


def status_roi(box) -> tuple[int, int, int, int]:
    x, y, _, h = box
    return (max(x - 4, 0), y + h - 3, 110, 24)


_OCC_FIX = str.maketrans({"D": "0", "O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "z": "2", "Z": "2", "S": "5"})


def parse_occupancy(text: str) -> tuple[int | None, int | None] | None:
    """"i3/3" → (3, 3)；"i171"（斜杠读成 7）→ (1, 1)；"i3"（没读到 /M）→ (3, None)；认不出 → None。"""
    t = re.sub(r"\s", "", text)
    t = t[:1] + t[1:].translate(_OCC_FIX) if t[:1] in "iIf" else t.translate(_OCC_FIX)
    m = re.search(r"(\d)[/7](\d)$", t) or re.search(r"(\d)/(\d)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"[iIf](\d)", t)
    if m:
        return int(m.group(1)), None
    return None


def read_occupancy(hits, box, right: int, kind: str, level: int | None) -> tuple[int | None, int | None]:
    """卡片右下的"iN/M"（全屏 OCR 里、名字下方 35–80 像素、卡片右半边的字）。人数超过这一级的上限、
    或 /M 和数据表对不上的不要；对不上的 M 丢掉、N 在上限内还留着。"""
    x, y, _, _ = box
    cap = capacity(kind, level)
    for h in hits:
        cx, cy = h.center
        if not (x + 40 <= cx <= right and y + 35 <= cy <= y + 80):
            continue
        got = parse_occupancy(h.text)
        if got is None:
            continue
        n, m = got
        if m is not None and cap is not None and m != cap:
            m = None
        limit = m or cap
        if n is not None and limit is not None and n > limit:
            continue
        return n, m
    return None, None


def read_screen(img: np.ndarray, ocr: Callable) -> list[Room]:
    """一屏总览 → 能读的卡。`ocr(roi)` 返回 OcrHit 列表（全屏一次，每张卡的状态行各一次）。"""
    hits = ocr(FULL_ROI)
    names = room_names(hits)
    out: list[Room] = []
    for name, box in names:
        if not readable(name, box):
            continue
        kind = ROOMS[name]
        x, y, _, _ = box
        if kind == "control":
            out.append(Room(kind, level=read_control_level(hits, box), x=x, y=y))
            continue
        level = read_level(img, name, box)
        status = None
        for h in hits:                                     # 全屏 OCR 读到了就用，没有再单独读一次状态行
            if _overlaps(h.box, status_roi(box)) and h.box[1] > y + 10:
                status = match_status(h.text)
                if status:
                    break
        if status is None:
            status = next((s for s in (match_status(h.text) for h in ocr(status_roi(box))) if s), None)
        right = min([bx - 5 for n2, (bx, by, _, _) in names if abs(by - y) <= 12 and bx > x + 60] + [x + 330, REF_W])
        occupied, cap = read_occupancy(hits, box, right, kind, level)
        out.append(Room(kind, level=level, status=status, occupied=occupied, capacity=cap, x=x, y=y))
    return out


# --- 纯函数：多屏合并 ---------------------------------------------------------------------------------------

def _same(a: Room, b: Room) -> bool:
    return a.type == b.type and abs(a.y - b.y) <= 12 and (a.level is None or b.level is None or a.level == b.level)


def _pixel_diff(a: np.ndarray, b: np.ndarray, d: int) -> float:
    """b 相对 a 向左移了 -d 像素（d < 0）时，两屏重叠部分（卡片区）的平均灰度差。"""
    if d >= 0 or -d >= a.shape[1] - 40:
        return float("inf")
    y0, y1 = TOP_BAR_H + 190, a.shape[0] - 20
    ga, gb = a[y0:y1].mean(axis=2), b[y0:y1].mean(axis=2)
    return float(np.abs(ga[:, -d:] - gb[:, :a.shape[1] + d]).mean())


def offset(prev: list[Room], cur: list[Room], prev_img: np.ndarray | None = None,
           cur_img: np.ndarray | None = None) -> int | None:
    """后一屏相对前一屏的横向位移 d（cur 的 x = prev 的 x + d，向右看时 d < 0）：两屏里"同一张卡"的候选配对
    各给出一个 d，取支持最多的；一样多时用像素比对选重叠部分最像的。没有配对 → None（两屏没有共同的卡）。"""
    cands: dict[int, int] = {}
    for a in prev:
        for b in cur:
            if _same(a, b) and b.x < a.x:
                d = b.x - a.x
                key = next((k for k in cands if abs(k - d) <= 10), d)
                cands[key] = cands.get(key, 0) + 1
    if not cands:
        return None
    best = max(cands.values())
    top = [d for d, n in cands.items() if n == best]
    if len(top) > 1 and prev_img is not None and cur_img is not None:
        top.sort(key=lambda d: _pixel_diff(prev_img, cur_img, d))
    return top[0]


def merge(screens: list[list[Room]], imgs: list[np.ndarray] | None = None) -> list[Room]:
    """按 offset 把各屏换算到第一屏的坐标，去掉重复的卡（后一屏的读数只补前一屏没读到的字段）。
    结果按总览上的位置排序（先左后右、先上后下）。"""
    rooms: list[Room] = []
    shift = 0
    prev: list[Room] = []
    for i, screen in enumerate(screens):
        if i:
            d = offset(prev, screen, imgs[i - 1] if imgs else None, imgs[i] if imgs else None)
            shift -= d if d is not None else -REF_W       # 没有共同的卡：当作整整拖过了一屏
        prev = screen
        for r in screen:
            g = Room(**{**asdict(r), "x": r.x + shift})
            dup = next((o for o in rooms if o.type == g.type and abs(o.y - g.y) <= 12 and abs(o.x - g.x) <= 20), None)
            if dup is None:
                rooms.append(g)
                continue
            for f in ("level", "status", "occupied", "capacity"):
                if getattr(dup, f) is None:
                    setattr(dup, f, getattr(g, f))
    return sorted(rooms, key=lambda r: (r.x // 40, r.y))


def to_state(rooms: list[Room]) -> dict:
    """{类型: 读数}：只有一间的类型（控制中枢、会客室、加工站、办公室、训练室）是一个字典，其余是列表。"""
    single = {"control", "meeting", "workshop", "hire", "training"}
    out: dict = {}
    for r in rooms:
        if r.type in single:
            out.setdefault(r.type, r.to_dict())
        else:
            out.setdefault(r.type, []).append(r.to_dict())
    return out


# --- 导航（碰模拟器） ---------------------------------------------------------------------------------------

def _changed(a: np.ndarray, b: np.ndarray) -> bool:
    y0, y1 = TOP_BAR_H + 190, a.shape[0] - 20
    return float(np.abs(a[y0:y1].astype(int) - b[y0:y1].astype(int)).mean()) >= SAME_SCREEN_DIFF


def open_overview(g, timeout: float = 20) -> bool:
    """首页 → 基建总览（只点首页的"基建"）。在总览了 → True。"""
    from maapilot.crew import nav
    if g.find(*OVERVIEW_MARK):
        return True
    nav.goto(g, "基建")
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if g.find(*OVERVIEW_MARK):
            return True
        time.sleep(1.5)
    return False


def _drag(g, swipe) -> None:
    (x1, y1), (x2, y2) = swipe
    g.swipe(x1, y1, x2, y2, duration_ms=600, delay=1.5)


def scan(g, *, log=print) -> dict:
    """进基建总览、拖到最左，每次向右拖约半屏读一屏，直到画面不变（最多 MAX_SCREENS 屏），回首页。
    → {"rooms": to_state 的结果, "screens": 读了几屏, "home": 回没回到首页}。进不了总览、或一屏都没读到卡抛 RuntimeError。"""
    from maapilot.crew import nav
    if not open_overview(g):
        g.save("infrast_overview_fail")
        nav.ensure_home(g)
        raise RuntimeError("进不了基建总览")
    try:
        img = g.screencap()
        for _ in range(3):                                 # 拖到最左
            _drag(g, SWIPE_LEFT)
            nxt = g.screencap()
            moved = _changed(img, nxt)
            img = nxt
            if not moved:
                break
        screens, imgs = [], []
        while True:
            rooms = read_screen(img, lambda roi, _img=img: g.ocr(roi, _img))
            log(f"基建总览第 {len(screens) + 1} 屏：{len(rooms)} 张卡")
            screens.append(rooms)
            imgs.append(img)
            if len(screens) >= MAX_SCREENS:
                break
            _drag(g, SWIPE_RIGHT)
            nxt = g.screencap()
            if not _changed(img, nxt):                     # 到最右了
                break
            img = nxt
    finally:
        home = nav.ensure_home(g)                          # 会先确认"离开罗德岛基建"
    rooms = merge(screens, imgs)
    if not rooms:
        g.save("infrast_no_rooms")
        raise RuntimeError("基建总览上一张房间卡都没读到")
    return {"rooms": to_state(rooms), "screens": len(screens), "home": home}


def read() -> dict:
    """连上模拟器读一次（maapilot state 调用）。"""
    from maapilot.crew.core import Game
    return scan(Game())
