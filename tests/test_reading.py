"""识别容错（maapilot.crew.reading）和用到它的快捷操作逻辑：都是假的识别结果，不要 OCR 模型。
真截图上的样本在 tests/test_screens.py。"""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from maapilot import paths
from maapilot.crew import reading


# ---- 按词认 -----------------------------------------------------------------------
@pytest.mark.parametrize("text, vocab, want", [
    ("川练中", ["训练中", "训练完成", "空闲中", "训练室"], "训练中"),       # 10-12 真机
    ("环烽预制体", ["环烃预制体", "异铁块", "异铁组"], "环烃预制体"),       # 10-08 真机
    ("采购心", ["采购中心"], "采购中心"),
    ("采购口心", ["采购中心"], "采购中心"),
    ("加工站B105", ["加工站", "制造站"], "加工站"),                       # 原样出现：前后多的字不算
    ("缄默德克萨斯", ["德克萨斯", "缄默德克萨斯"], "缄默德克萨斯"),         # 都出现：取长的
    ("异铁炮", ["异铁块", "异铁组"], None),                               # 离两个一样近：不猜
    ("训练", ["训练中", "训练室"], None),
    ("确人", ["确认"], None),                                            # 两个字的词要一字不差
    ("训练完成", ["训练中", "训练完成"], "训练完成"),
])
def test_best_word(text, vocab, want):
    assert reading.best_word(text, vocab) == want


def test_matches_needs_the_word_to_win():
    assert reading.matches("川练中", "训练中", ["训练完成"])
    assert not reading.matches("训练完成", "训练中", ["训练完成"])
    assert not reading.matches("异铁块", "异铁组", ["异铁块"])


# ---- 有/要 -------------------------------------------------------------------------
@pytest.mark.parametrize("text, want", [
    ("57/15", (57, 15)), ("47|12", (47, 12)), ("8Z1", (8, 1)), ("14:1", (14, 1)), ("c59/2", (59, 2)),
    ("0/2r,", (0, 2)), ("2O5/1O", (205, 10)), ("L'\"+14/1o", (14, 1)),
    ("5715", None), ("-171", None), ("1/1/1", None), ("/2", None), ("", None),
])
def test_parse_have_need(text, want):
    assert reading.parse_have_need(text) == want


@pytest.mark.parametrize("text, need, want", [
    ("2712", 12, (27, 12)), ("601", 1, (60, 1)), ("76", 6, (7, 6)), ("5715", 15, (57, 15)),
    ("57/15", 15, (57, 15)), ("57/15", 12, None), ("12", 12, None), ("abc", 1, None),
])
def test_split_by_need(text, need, want):
    assert reading.split_by_need(text, need) == want


def test_vote():
    assert reading.vote([None, (1, 1), (1, 1), (17, 1)]) == (1, 1)
    assert reading.vote([(1, 1), (17, 1)]) is None
    assert reading.vote([None, None]) is None


def test_binarize_keeps_bright_strokes_black_on_white():
    img = np.zeros((720, 1280, 3), np.uint8)
    img[10:20, 10:20] = (255, 120, 40)                   # BGR 蓝底
    img[12:14, 12:14] = (255, 255, 255)                  # 白字
    out, roi = reading.binarize(img, (10, 10, 10, 10), threshold=150, channel="min_rg")
    assert roi == (10, 10, 10, 10) and out.shape == img.shape
    assert out[12, 12].tolist() == [0, 0, 0] and out[10, 10].tolist() == [255, 255, 255]
    assert out[0, 0].tolist() == [255, 255, 255]         # roi 外面也是白的
    big, roi2 = reading.binarize(img, (10, 10, 10, 10), threshold=150, scale=2)
    assert roi2 == (0, 0, 60, 60)


# ---- 专精确认框：按游戏数据的"要"对齐 -------------------------------------------------
COSTS = [[("卷2", 8)], [("卷3", 12), ("A", 4)], [("卷3", 15), ("切削原液", 6), ("D32钢", 6)]]


def test_align_by_need_splits_merged_digits():
    from maapilot.crew import mastery
    dlg = {"have_need": [(60, 1), (6, 6), (7, 6)], "unread": [(1, ["5715", "5715"])], "columns": 4}
    r, pairs, guessed = mastery.align_by_need(COSTS, dlg)
    assert r == 3 and pairs == [(57, 15), (6, 6), (7, 6)] and guessed == {0}


def test_align_by_need_no_unique_fit():
    from maapilot.crew import mastery
    assert mastery.align_by_need(COSTS, {"have_need": [(60, 1)], "unread": [(1, ["x"])], "columns": 2}) is None
    assert mastery.align_by_need(COSTS, {"have_need": [(60, 1), (57, 15)]}) is None      # 没有读不出的列


def test_mastery_guessed_short_is_not_trusted(monkeypatch, tmp_path):
    """切出来的"有"显示不够（"1/1"读成"171"切成 17 的反面）：不当真，截图交给 agent。"""
    from maapilot.crew import mastery
    taps = []
    g = SimpleNamespace(tap=lambda *a, **k: taps.append(a), save=lambda n: tmp_path / f"{n}.png")
    monkeypatch.setattr(mastery, "open_plan", lambda g: "idle")
    monkeypatch.setattr(mastery, "pick_trainee", lambda g, n: None)
    monkeypatch.setattr(mastery, "_skill_costs", lambda n, s: COSTS)
    monkeypatch.setattr(mastery, "open_dialog", lambda g, s: {"target_rank": 3, "hours": 24,
                                                               "have_need": [(60, 1), (6, 6), (7, 6)],
                                                               "unread": [(1, ["315"])], "columns": 4})
    r = mastery.start_mastery(g, "丰川祥子", 2, {}, max_rank=3)
    assert r["status"] == "costs unreadable" and r["rank_by"] == "cost"
    assert taps == [mastery.DLG_CANCEL]


# ---- 升技能到 7 级 -----------------------------------------------------------------
class SkillGame:
    """升技能确认框：显示"等级N到等级"，点确认 N+1；到 7 级框里不再有这行字（10-05、10-06 真机）。"""

    def __init__(self, rank):
        self.rank, self.dialog, self.taps, self.backs = rank, False, [], 0

    def tap(self, x, y, delay=0):
        from maapilot.crew import upgrade
        self.taps.append((x, y))
        if (x, y) == upgrade.BTN_SKILL_UP:
            self.dialog = True
        elif (x, y) == upgrade.SKILL_CONFIRM:
            self.rank += 1

    def wait(self, pattern, roi, timeout=15):
        if self.dialog and self.rank < 7:
            return SimpleNamespace(text=f"等级{self.rank}到等级{self.rank + 1}")
        raise TimeoutError(pattern)

    def back(self, delay=0):
        self.backs += 1
        self.dialog = False

    def ocr(self, *a, **k):
        return []


@pytest.fixture
def skill_game(monkeypatch):
    from maapilot.crew import overlays, upgrade
    monkeypatch.setattr(overlays, "dismiss_all", lambda g, **k: [])
    monkeypatch.setattr(upgrade, "on_operator_page", lambda g: not g.dialog)
    monkeypatch.setattr(upgrade, "read_rank", lambda g: g.rank)
    return SkillGame


def test_skill_to_7_reports_success(skill_game):
    from maapilot.crew import upgrade
    g = skill_game(5)
    assert upgrade.skill_to(g, 7) == 7
    assert g.taps.count(upgrade.SKILL_CONFIRM) == 2


def test_skill_to_6_unchanged(skill_game):
    from maapilot.crew import upgrade
    g = skill_game(4)
    assert upgrade.skill_to(g, 6) == 6 and g.backs == 1


def test_skill_dialog_gone_below_target_fails(skill_game, monkeypatch):
    from maapilot.crew import upgrade
    g = skill_game(5)
    monkeypatch.setattr(g, "wait", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("x")))
    with pytest.raises(upgrade.UpgradeError, match="rank now 5"):
        upgrade.skill_to(g, 7)


# ---- op upgrade：报错了先核对 --------------------------------------------------------
def test_verify_upgrade(monkeypatch):
    from maapilot.crew import upgrade
    from maapilot.flows import op
    err = upgrade.UpgradeError("skill loop")
    err.reached = {"start": {"elite": 2, "level": 1}}
    monkeypatch.setattr(upgrade, "read_operator", lambda g, n: {"name": n, "elite": 2, "level": 1, "skill": 7})
    assert op._verify_upgrade(None, "遥", 2, 1, 7, err) == {"name": "遥", "elite": 2, "level": 1, "skill": 7,
                                                           "reached": True, "start": {"elite": 2, "level": 1}}
    assert op._verify_upgrade(None, "遥", 2, 1, 8, err) is None
    monkeypatch.setattr(upgrade, "read_operator", lambda g, n: {"name": n, "elite": 2, "level": None, "skill": 7})
    assert op._verify_upgrade(None, "遥", 2, 1, 7, err) is None              # 等级读不出：不算到了


# ---- 取证截图 -----------------------------------------------------------------------
def test_save_is_timestamped_with_hits(monkeypatch):
    from maapilot.crew import core
    g = core.Game.__new__(core.Game)
    g.tasker = None
    g._shot = np.zeros((720, 1280, 3), np.uint8)
    g._recent_ocr().append({"roi": [0, 0, 10, 10], "hits": [{"text": "5715", "box": [1, 2, 3, 4], "score": 0.9}]})
    a = g.save("op_mastery_error_遥")
    b = g.save("op_mastery_error_遥")
    assert a != b and a.exists() and b.exists()                  # 同一个干员再失败不盖掉上一张
    assert a.name.startswith("op_mastery_error_遥_") and a.parent == paths.DEBUG_DIR
    meta = json.loads(a.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["recent"][0]["hits"][0]["text"] == "5715" and meta["name"] == "op_mastery_error_遥"


def test_prune_debug_keeps_newest(monkeypatch):
    from maapilot.crew import core
    paths.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        p = paths.DEBUG_DIR / f"x_2026101{i}-000000.png"
        p.write_bytes(b"")
        p.with_suffix(".json").write_text("{}")
    (paths.DEBUG_DIR / "maafw.log").write_text("")
    core.prune_debug(keep=2)
    left = sorted(p.name for p in paths.DEBUG_DIR.iterdir())
    assert len([n for n in left if n.endswith(".png")]) == 2 and "maafw.log" in left
