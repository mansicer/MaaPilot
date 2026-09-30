"""Navigation helpers shared by features (drawer, dialogs, main-story stage pages)."""
from __future__ import annotations

import re
import time

from maapilot.crew.core import FULL_ROI, REF_W, Game

DRAWER = {"首页": (92, 260), "干员": (308, 260), "编队": (237, 190), "终端": (530, 190),
          "基建": (688, 190), "档案": (450, 260)}


HOME_BUTTONS = {"干员": (941, 335), "终端": (960, 190), "基建": (980, 600), "编队": (740, 335), "档案": (395, 625)}


# the home screen's "终端" button, glyphs included whole: the old (880, 100, 160, 80) cut off their lower part and OCR
# then often read only "端" on the home screen (F15). Keep page rule `home` in pagerules/official.yaml on the same box.
HOME_ROI = (860, 100, 200, 110)


def is_home(g: Game) -> bool:
    return g.find(r"终端", HOME_ROI) is not None


# the "system fault" dialog the game shows when it drops the connection (真机 2026-09-29, tests/fixtures/screens/
# offline_dialog.jpg): white text "当前系统发生未知故障，尝试重新恢复" at about (422, 320)-(858, 344) on a dark
# full-width band, a single ✓ centred at (640, 491). Keep page rule `game_offline` in pagerules/official.yaml on the
# same box. MaaCore matches it as OfflineConfirm and stops (D137).
OFFLINE_TEXT = r"未知故障|尝试重新恢复"
OFFLINE_ROI = (300, 290, 680, 90)
OFFLINE_OK = (640, 491)


def dismiss_offline(g: Game) -> bool:
    """Press ✓ on the connection-fault dialog when it is up; True if it was."""
    if g.find(OFFLINE_TEXT, OFFLINE_ROI) is None:
        return False
    g.tap(*OFFLINE_OK, delay=3.0)
    return True


def recover_offline(g: Game, timeout: float = 45.0, interval: float = 3.0) -> dict:
    """After a connection fault (D137): press ✓ and wait for the game's own reconnect to land on the home screen.
    Returns {"tapped": pressed ✓ at least once, "home": back on the home screen}; not home within `timeout` (still
    reconnecting, bounced to the title screen, ...) → home False, the caller restarts the client."""
    tapped = dismiss_offline(g)
    if not tapped:                                        # no dialog: nothing to wait for
        return {"tapped": False, "home": is_home(g)}
    deadline = time.monotonic() + timeout
    while not is_home(g):
        if time.monotonic() >= deadline:
            return {"tapped": True, "home": ensure_home(g, rounds=2)}   # e.g. an announcement after reconnecting
        time.sleep(interval)
        dismiss_offline(g)                                # the fault can come back while reconnecting
    return {"tapped": True, "home": True}


def dismiss_dialogs(g: Game) -> None:
    dismiss_offline(g)                                   # connection fault: ✓ lets the game reconnect (D137)
    if g.find(r"重置设置", (1000, 20, 260, 60)):                # settings page (opened by a stray tap)
        g.tap(85, 45, delay=2.0)
    if g.find(r"活动公告|系统公告|资讯速报", (340, 95, 700, 60)):   # login announcements
        g.tap(1203, 70, delay=2.5)
    if g.find(r"月签到", (0, 60, 400, 80)):                # monthly sign-in calendar (opened by the top-left icon)
        g.tap(1215, 68, delay=2.0)
    if g.find(r"结束时间", (40, 590, 400, 40)) and g.find(r"LOGIN|DAY", (450, 240, 700, 80)):   # login-event page
        g.tap(1232, 100, delay=2.0)
    if g.find(r"离开罗德岛基建", (300, 260, 700, 80)):
        g.tap(842, 495, delay=2.5)                       # confirm leaving the base
    if g.find(r"角色档案", (150, 120, 600, 150)) and g.find(r"关闭", (120, 90, 100, 40)):
        g.tap(156, 109, delay=1.5)


def open_drawer(g: Game) -> bool:
    """Returns True with the drawer open, False if we are on the home screen (which has no drawer)."""
    for _ in range(5):
        dismiss_dialogs(g)
        if is_home(g):
            return False
        g.tap(270, 38, delay=2.0)
        if g.find(r"首页|终端|基建", (40, 80, 720, 200)):
            return True
        if g.find(r"月签到", (0, 60, 400, 80)):          # that tap hit the home sign-in icon => we are home
            g.tap(1215, 68, delay=2.0)
            return False
        time.sleep(1.5)
    g.save("open_drawer_fail")
    raise RuntimeError("could not open the navigation drawer")


def goto(g: Game, entry: str) -> None:
    if open_drawer(g):
        g.tap(*DRAWER[entry], delay=3.0)
    else:                                                 # home screen: use its big buttons
        if entry != "首页":
            g.tap(*HOME_BUTTONS[entry], delay=3.0)
    dismiss_dialogs(g)


def ensure_home(g: Game, rounds: int = 3) -> bool:
    """Back to the home screen from wherever MAA StartUp gave up (decision D110): known dialogs, then goto 首页
    (the drawer); when the drawer won't open, the Android back key. At most `rounds` rounds; False = not home."""
    for _ in range(rounds):
        dismiss_dialogs(g)
        if is_home(g):
            return True
        try:
            if not open_drawer(g):                        # open_drawer found the home screen (itself or via the sign-in icon)
                return True
        except RuntimeError:                              # no drawer: some full-screen page without one
            g.key_back(delay=2.0)
            continue
        g.tap(*DRAWER["首页"], delay=3.0)
        dismiss_dialogs(g)
        if is_home(g):
            return True
    g.save("ensure_home_fail")
    return False


def goto_main_stage(g: Game, code: str) -> None:
    """Open the stage page of a main-story stage of the *current* chapter, e.g. "15-15"."""
    if not g.find(r"推演计分|EPISODE", (0, 600, REF_W, 120)):     # not already on the chapter map
        goto(g, "终端")
        g.wait(r"离解复合|进度|前往上一次作战", FULL_ROI, timeout=10)
        g.tap(760, 185, delay=4.0)                       # the featured (current) chapter card
    # the map opens centred on the newest stage; earlier stages are to the left
    moves = [(400, 900)] * 4 + [(900, 400)] * 8
    for x1, x2 in moves:
        hit = g.find(rf"^{re.escape(code)}$", (0, 250, REF_W, 400))
        if hit:
            g.tap_hit(hit, delay=3.0)
            g.wait(r"推荐平均等级|开始行动", (900, 170, 380, 520), timeout=10)
            return
        g.swipe(x1, 300, x2, 300, duration_ms=500, delay=1.5)
    g.save("goto_main_stage_fail")
    raise RuntimeError(f"stage {code} not found on the chapter map")


def goto_room(g: Game, room: str) -> None:
    """Open a base room by its label (训练室 / 加工站 / ...), from anywhere."""
    for _ in range(8):
        dismiss_dialogs(g)
        if g.find(r"进驻总览", (20, 95, 140, 50)):                    # base overview
            hit = g.find(room, (0, 60, REF_W, 620))
            if hit:
                g.tap_hit(hit, delay=3.0)
                g.wait(r"设施信息", (10, 170, 120, 50), timeout=10)
                return
            g.swipe(900, 400, 500, 400, duration_ms=500, delay=1.5)
            continue
        if g.find(r"设施信息", (10, 170, 120, 50)):                    # inside some room
            g.back(delay=2.5)
            continue
        goto(g, "基建")
        time.sleep(3)
    g.save("goto_room_fail")
    raise RuntimeError(f"could not open room {room}")
