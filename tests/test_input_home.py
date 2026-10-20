"""回首页（`input home`）与关掉的遮挡：活动签到页 / 月签到照样关（不然回不了首页），但每次都写进结果、交给 agent。
假的游戏：每页是几处文字（字和中心点），点哪儿跳到哪页；不碰模拟器。"""
import json
import os
import re

import numpy as np
import pytest

from maapilot import cli, paths
from maapilot.crew import lock, nav, overlays
from maapilot.tools import common
from tests.test_tools import cfg  # noqa: F401  假的 maa

LOGIN = [("结束时间 10月28日", (200, 610)), ("LOGIN DAY 3", (800, 280))]   # 新活动的限时登录签到
SIGN_IN = [("月签到", (150, 100))]
HOME = [("终端", (960, 160))]
STRANGE = [("某个不认得的页面", (640, 360))]


class Hit:
    def __init__(self, text, center):
        self.text, self.box, self.score = text, (center[0] - 10, center[1] - 10, 20, 20), 0.9


class PageGame:
    """screens：页名 → 文字；moves：(页名, 点的位置) → 下一页。"""

    def __init__(self, start, screens, moves):
        self.page, self.screens, self.moves, self.calls = start, screens, moves, []

    def find(self, pattern, roi, img=None):
        x0, y0, w, h = roi
        for text, (x, y) in self.screens[self.page]:
            if re.search(pattern, text) and x0 <= x < x0 + w and y0 <= y < y0 + h:
                return Hit(text, (x, y))
        return None

    def screencap(self):
        return np.zeros((720, 1280, 3), dtype=np.uint8)

    def tap(self, x, y, delay=0.8):
        self.calls.append(("tap", x, y))
        self.page = self.moves.get((self.page, (x, y)), self.page)

    def key_back(self, delay=0):
        self.calls.append(("back",))

    def save(self, name):
        return None


SCREENS = {"login": LOGIN, "sign_in": SIGN_IN, "home": HOME, "strange": STRANGE}
MOVES = {("login", (1232, 100)): "sign_in", ("sign_in", (1215, 68)): "home"}


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def device(monkeypatch):
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    monkeypatch.setattr(nav.time, "sleep", lambda s: None)
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\n", encoding="utf-8")

    def use(start):
        g = PageGame(start, SCREENS, MOVES)
        monkeypatch.setattr(common, "connect", lambda ocr=False, readonly=False: g)
        return g
    return use


def test_dismiss_records_what_it_closed(monkeypatch):
    g = PageGame("login", SCREENS, MOVES)
    pos = overlays.mark()
    assert overlays.dismiss_all(g) == ["login_event", "sign_in"]
    assert g.calls == [("tap", 1232, 100), ("tap", 1215, 68)] and g.page == "home"
    assert overlays.closed_since(pos) == ["login_event", "sign_in"]
    assert overlays.notable(["announcement", "login_event", "login_event", "sign_in"]) == ["login_event", "sign_in"]


def test_notable_ones_recorded_by_run_id(monkeypatch):
    """签到页记进文件，带日常的运行编号（daily run 按它取）；别的遮挡只记在本进程。"""
    monkeypatch.setenv("MAAPILOT_RUN_ID", "20261021-0440")
    overlays.dismiss_all(PageGame("login", SCREENS, MOVES))
    monkeypatch.delenv("MAAPILOT_RUN_ID")
    overlays.dismiss_all(PageGame("sign_in", SCREENS, MOVES))               # 日常之外：没有运行编号
    assert overlays.seen_in_run("20261021-0440") == ["login_event", "sign_in"]
    assert overlays.seen_in_run("other") == []
    rows = [json.loads(ln) for ln in (paths.STATE / "overlays.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["name"], r["run_id"]) for r in rows] == [("login_event", "20261021-0440"), ("sign_in", "20261021-0440"),
                                                         ("sign_in", None)]


def test_input_home_reports_closed_sign_in_pages(device, capsys):
    g = device("login")
    code, env = run(capsys, "input", "home")
    assert code == 0, env
    d = env["data"]
    assert d["home"] is True and g.page == "home"
    assert d["overlays"] == ["login_event", "sign_in"] and d["seen"] == ["login_event", "sign_in"]
    assert "活动签到页、月签到" in d["hint"] and "领" in d["hint"]
    assert d["shot"].endswith("-home.png") and os.path.exists(d["shot"])
    assert lock.current() is None


def test_input_home_already_home(device, capsys):
    g = device("home")
    code, env = run(capsys, "input", "home")
    assert code == 0 and env["data"]["home"] is True and env["data"]["overlays"] == [] and env["data"]["seen"] == []
    assert "hint" not in env["data"] and g.calls == []                 # 已经在首页：什么都不点


def test_input_home_not_home(device, capsys):
    """回不去：退出码 1（not_home），照样给截图；只按返回键、点左上菜单，不点别的。"""
    g = device("strange")
    code, env = run(capsys, "input", "home")
    assert code == 1 and env["error"]["code"] == "not_home"
    assert env["data"]["home"] is False and env["data"]["shot"]
    assert set(g.calls) <= {("tap", 270, 38), ("back",)}


def test_input_home_screenshot_failure_is_an_error(device, capsys, monkeypatch):
    """截图是 agent 看活动入口的唯一依据：截不了就退出码 1（input_failed），不能当成做成了；路上关了什么照样在 data 里。"""
    from maapilot.tools import screen

    def broken(game, name):
        raise OSError("screencap failed")
    monkeypatch.setattr(screen, "capture", broken)
    device("login")
    code, env = run(capsys, "input", "home")
    assert code == 1 and env["error"]["code"] == "input_failed"
    assert env["data"]["home"] is True and env["data"]["shot"] is None and env["data"]["seen"] == ["login_event", "sign_in"]
    assert lock.current() is None


def test_startup_reports_closed_sign_in_pages(cfg, capsys, monkeypatch):  # noqa: F811
    """maa startup 成功后回首页：路上关掉的签到页写进 data.overlays / seen（假的 maa，假的游戏）。"""
    monkeypatch.setattr(nav.time, "sleep", lambda s: None)
    g = PageGame("login", SCREENS, MOVES)
    monkeypatch.setattr(common, "connect", lambda ocr=False, readonly=False: g)
    code, env = run(capsys, "maa", "startup")
    assert code == 0, env
    d = env["data"]
    assert d["home"] is True and d["overlays"] == ["login_event", "sign_in"] and d["seen"] == ["login_event", "sign_in"]
    g = PageGame("home", SCREENS, MOVES)
    code, env = run(capsys, "maa", "startup")
    assert code == 0 and env["data"]["home"] is True and "overlays" not in env["data"]
