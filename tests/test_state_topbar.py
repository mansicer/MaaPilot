"""PC 客户端顶栏读数（state._win32_topbar）：按"带不带加号 + 左右顺序"取，不吃写死的 x。"""
from __future__ import annotations

from dataclasses import dataclass

from maapilot.tools.state import _win32_topbar


@dataclass
class Hit:
    text: str
    box: tuple[int, int, int, int]


def test_three_numbers():
    hits = [Hit("888581", (769, 53, 111, 48)), Hit("240+", (955, 24, 100, 48)), Hit("0+", (1129, 15, 64, 40))]
    assert _win32_topbar(hits) == {"lmd": 888581, "orundum": 240, "originite": 0}


def test_x_drifts_a_few_pixels():
    """实测同一布局两次 OCR 的 x 会差十几像素（939 / 955），写死区间会错位。"""
    hits = [Hit("888581", (769, 53, 111, 48)), Hit("240+", (939, 38, 99, 50)), Hit("0+", (1108, 31, 61, 40))]
    assert _win32_topbar(hits) == {"lmd": 888581, "orundum": 240, "originite": 0}


def test_datetime_and_noise_ignored():
    hits = [Hit("2026/11/18 04:48", (804, 16, 152, 38)),   # 带冒号：不是钱
            Hit("14-21万千愿景", (893, 237, 139, 29)),
            Hit("480终端", (692, 114, 339, 111)),          # 不是纯数字
            Hit("888581", (769, 53, 111, 48)), Hit("240+", (955, 24, 100, 48)), Hit("0+", (1129, 15, 64, 40))]
    assert _win32_topbar(hits) == {"lmd": 888581, "orundum": 240, "originite": 0}


def test_plus_lost_still_reads_by_position():
    """实测 OCR 会把 "240+" 认成 "3400"：加号丢了也要按位置读出来，不能整条作废。"""
    hits = [Hit("898081", (748, 32, 178, 67)), Hit("3400", (983, 17, 107, 52)), Hit("0+", (1167, 15, 68, 30))]
    got = _win32_topbar(hits)
    assert got == {"lmd": 898081, "orundum": 3400, "originite": 0}


def test_single_number_goes_to_its_position():
    """只有一个数：按它的 x 归到对应那一项（955 是合成玉的位置），另一项不给。"""
    hits = [Hit("240+", (955, 24, 100, 48))]
    got = _win32_topbar(hits)
    assert got["orundum"] == 240 and got["originite"] is None and got["lmd"] is None


def test_top_left_of_bar_ignored():
    """顶栏以外（y 太大）的数字不参与。"""
    hits = [Hit("888581", (769, 200, 111, 48)), Hit("240+", (955, 24, 100, 48)), Hit("0+", (1129, 15, 64, 40))]
    got = _win32_topbar(hits)
    assert got["lmd"] is None and got["orundum"] == 240 and got["originite"] == 0
