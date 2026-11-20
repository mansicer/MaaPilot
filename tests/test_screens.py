"""识别回归测试：在真机截图（tests/fixtures/screens/）上跑真的 MaaFramework OCR 和读页面的函数，对照人眼看出的正确答案。

别的测试都是假的 OCR 结果；真机上出过的错都在识别这一层（首页理智 130 读成 29、专精确认框 57/15 读成 5715……）。
修识别的毛病先把出错的截图做成样本加进来（做法见 CLAUDE_DEV.md「识别回归测试」），看它失败，再改。
还没修的标 xfail(strict=True)：修好了会变成 XPASS 报错，提醒把 xfail 去掉。

OCR 模型不入库（tests/resources.py：`python -m tests.resources fetch` 下载固定的版本）；不在时整个文件跳过。
模型有两代（OCR_GEN："v3" 是 MAA 2026-11-02 之前的，"v6" 是之后的），用户机器上哪一代都有，CI 两代都跑：
- 只在一代上读错、还没修的：标 `xfail_on("v6", …)`（strict：修好了会报 XPASS）。
- 记下"模型原样读出来是错的"那几句断言只对那一代成立，写在 `if OCR_GEN == …` 里；读页面的函数两代都要对。
"""
from pathlib import Path

import pytest

from tests import resources

SCREENS = Path(__file__).parent / "fixtures" / "screens"
OCR_GEN = resources.ocr_gen()
needs_recipes = resources.needs_data("recipes")

pytestmark = resources.needs(OCR_GEN is not None, "OCR 模型")


def xfail_on(gen: str, reason: str):
    """这一代模型上读错、还没修的（修好了 XPASS 会报错，提醒去掉）。"""
    return pytest.mark.xfail(OCR_GEN == gen, reason=f"{gen} 模型：{reason}", strict=True)


@pytest.fixture(scope="module")
def game(tmp_path_factory):
    """一个不连设备的 Game（加载 OCR 资源要零点几秒，整个文件共用一个）。"""
    from maapilot.crew.core import ImageGame
    return ImageGame(SCREENS / "home_130.png", log_dir=tmp_path_factory.mktemp("maa-log"))


def show(game, name: str):
    game.show(SCREENS / f"{name}.png")
    game.actions.clear()
    return game


# ---- 首页理智 ------------------------------------------------------------------
# 大号数字里的"1"OCR 认不好（旧读法：110 → 0、114 → 4、116 → 6、130 → 29/730、152 → 52、180 → 80、94 → 294）
HOME_SANITY = [0, 2, 18, 34, 41, 94, 104, 110, 114, 116, 130, 152, 180, 189, 190, 358]


@pytest.mark.parametrize("value", HOME_SANITY)
def test_home_sanity(game, value):
    from maapilot.tools import state
    g = show(game, f"home_{value}")
    img = g.screencap()
    assert state.home_sanity(g, img, g.ocr(state.HOME_ROI, img)) == (value, 205)


# 深色主题的理智面板（深灰底、白字，10-28 真机）：旧读法把 112 读成 2（白字当底、整块切不出字，退回 OCR 的"{2"）
@pytest.mark.parametrize("value", [4, 112])
def test_home_sanity_dark(game, value):
    from maapilot.tools import state
    g = show(game, f"home_dark_{value}")
    img = g.screencap()
    assert state.home_sanity(g, img, g.ocr(state.HOME_ROI, img)) == (value, 210)


@pytest.mark.parametrize("value, top", [
    (130, {"lmd": 9621162, "orundum": 19866, "originite": 26}),
    (110, {"lmd": 9397902, "orundum": 19066, "originite": 16}),       # v6 模型把"16+"的"+"号圆钮读成 0（160）
])
def test_home_topbar(game, value, top):
    from maapilot.tools import state
    g = show(game, f"home_{value}")
    img = g.screencap()
    assert state.parse_topbar(g.ocr(state.HOME_ROI, img), img) == top


# 顶栏的"+"号圆钮被读成末尾的 0（v6 模型，源石 16 → 160）：看图判断，只在"最右一块是圆钮、读出的数正好多一位 0"时去掉
@pytest.mark.parametrize("text, box, plus_is_zero", [
    ("160", (1101, 20, 86, 44), True),             # v6 真读出来的样子
    ("16+", (1104, 21, 81, 41), False),            # 读出了"+"：照旧去掉"+"就行
    ("16", (1101, 20, 86, 44), False),             # 位数和图上的数字一样多：不动
    ("190660", (902, 36, 129, 47), True),          # 合成玉 19066 后面的圆钮
    ("19066", (902, 36, 129, 47), False),
    ("19060", (902, 36, 129, 47), False),          # 本来就以 0 结尾、位数对得上：不动
    ("93979020", (717, 54, 124, 46), False),       # 龙门币后面没有圆钮：不动
    ("1600", (1101, 20, 86, 44), False),           # 多了两位：拿不准，不动
])
def test_topbar_plus_read_as_zero(game, text, box, plus_is_zero):
    from types import SimpleNamespace

    from maapilot.tools import state
    img = show(game, "home_110").screencap()
    assert state.plus_read_as_zero(img, SimpleNamespace(text=text, box=box)) is plus_is_zero


def test_topbar_without_image_is_unchanged(game):
    """不给截图就不核对圆钮（照原样用读出来的数）。"""
    from types import SimpleNamespace

    from maapilot.tools import state
    hits = [SimpleNamespace(text="19066+", box=(902, 36, 129, 47)), SimpleNamespace(text="160", box=(1101, 20, 86, 44))]
    assert state.parse_topbar(hits) == {"lmd": None, "orundum": 19066, "originite": 160}


def test_image_game_records_taps_without_device(game):
    g = show(game, "home_130")
    g.tap(10, 20)
    g.swipe(1, 2, 3, 4)
    assert g.actions == [("tap", 10, 20), ("swipe", 1, 2, 3, 4)]
    assert g.screencap().shape == (720, 1280, 3)


# ---- 专精确认框 -----------------------------------------------------------------
# 底下一排"有/要"：OCR 常把细的"/"丢掉（57/15 → 5715、7/6 → 76）或读成"|"，配对就错了（10-13 报"卷3 差 9"）。
# 现在再按 R、G 二值化读两遍、投票（mastery.read_costs）。
MASTERY = [
    ("mastery_dialog_60-1_57-15_6-6_7-6", 3, 24, [(60, 1), (57, 15), (6, 6), (7, 6)]),
    ("mastery_dialog_90-1_27-12_4-4_13-8", 2, 16, [(90, 1), (27, 12), (4, 4), (13, 8)]),
    ("mastery_dialog_70-1_47-12_4-4_11-9", 2, 16, [(70, 1), (47, 12), (4, 4), (11, 9)]),
]


@pytest.mark.parametrize("name, rank, hours, have_need", MASTERY)
def test_mastery_dialog(game, name, rank, hours, have_need):
    from maapilot.crew import mastery
    g = show(game, name)
    assert mastery.open_dialog(g, 2) == {"target_rank": rank, "have_need": have_need, "hours": hours}
    assert g.actions == [("tap", *mastery.SKILL_BTN[2])]


# ---- 加工站份数 -----------------------------------------------------------------
# 第一种原料下面的"有/要"标签，要 ÷ 每份用量 = 份数。斜杠同样会丢（16/16 → 6716、8/1 → 8Z1、1/1 → 1）；
# 现在原样读一遍、二值化放大再读几遍，投票（craft.read_label）。
CRAFT = [
    ("craft_D32钢_16-1", "D32钢", 1),
    ("craft_固化纤维板_14-1", "固化纤维板", 1),
    ("craft_晶体电路_59-2", "晶体电路", 1),
    ("craft_五水研磨石_8-1", "五水研磨石", 1),
    ("craft_五水研磨石_1-1", "五水研磨石", 1),
    ("craft_全新装置_16-16", "全新装置", 4),
    ("craft_固源岩组_4312-5", "固源岩组", 1),
    ("craft_晶体电子单元_1-1", "晶体电子单元", 1),
    ("craft_异铁块_0-2", "异铁块", 1),
]


@needs_recipes
@pytest.mark.parametrize("name, item, count", CRAFT)
def test_craft_count(game, name, item, count):
    from maapilot.crew import craft
    assert craft.read_count(show(game, name), item) == count


@needs_recipes
@pytest.mark.parametrize("name", [c[0] if isinstance(c, tuple) else c.values[0] for c in CRAFT])
def test_craft_label(game, name):
    """不光份数对，有/要两个数都要读对（文件名里是正确答案）。"""
    from maapilot.crew import craft
    have, need = map(int, name.rsplit("_", 1)[1].split("-"))
    assert craft.read_label(show(game, name)) == (have, need)


@needs_recipes
def test_craft_set_count_stops_on_toast(game):
    """10-08：16/16 读成 6716 读不出份数，一直点"+"点到弹"材料不足"还在点。现在看到提示条就停，不再点。"""
    from maapilot.crew import craft
    g = show(game, "craft_全新装置_16-16_材料不足")
    assert "材料不足" in craft.toast(g)
    with pytest.raises(craft.CraftError, match="材料不足"):
        craft.set_count(g, "全新装置", 5)
    assert g.actions == [("tap", *craft.BTN_MIN)]


@needs_recipes
def test_craft_set_count_stops_when_unread(game):
    from maapilot.crew import craft
    g = show(game, "home_130")                      # 没有原料标签
    with pytest.raises(craft.CraftError, match="cannot read the craft count"):
        craft.set_count(g, "全新装置", 2)
    assert g.actions == [("tap", *craft.BTN_MIN)]


# ---- 认得的遮挡 -----------------------------------------------------------------
def test_overlay_mastery_done(game):
    """专精练完的整屏提示（10-06～10-11 挡住 8 次专精快捷操作）：认得出，点对勾关掉。"""
    from maapilot.crew import mastery, overlays
    g = show(game, "mastery_done_popup")
    assert overlays.dismiss(g) == "mastery_done"
    assert g.actions == [("tap", 802, 583)]
    g = show(game, "mastery_done_popup")
    assert mastery.dismiss_done(g, wait_s=0.5) == "mastery_done"


@pytest.mark.parametrize("name", ["home_130", "mastery_dialog_60-1_57-15_6-6_7-6", "craft_全新装置_16-16_材料不足",
                                  "training_busy", "store_tabs"])
def test_overlay_none_taps_nothing(game, name):
    from maapilot.crew import overlays
    g = show(game, name)
    assert overlays.dismiss(g) is None
    assert g.actions == []


# ---- 导航 / 采购中心 -------------------------------------------------------------
def test_goto_store_from_home_taps_the_button(game):
    """首页的"采购中心"OCR 常读成"采购心""采购口心"（10-02～10-05），按字找找不到：从首页点固定的大按钮（nav.HOME_BUTTONS）。"""
    from maapilot.crew import nav
    g = show(game, "home_store_entry")
    assert nav.is_home(g)
    if OCR_GEN == "v3":
        assert [h.text for h in g.ocr((740, 420, 200, 120))] != ["采购中心"]     # 按字认不出（v6 认得出）
    nav.goto(g, "采购中心")
    assert ("tap", *nav.HOME_BUTTONS["采购中心"]) in g.actions


def test_store_tabs(game):
    from maapilot.crew import shop
    g = show(game, "store_tabs")
    assert shop.on_store(g)
    hit = shop.find_tab(g, "凭证交易所")
    assert hit and 780 < hit.center[0] < 900
    assert not shop.on_store(show(game, "home_130"))


# ---- 干员列表 -------------------------------------------------------------------
def test_oplist_first_card_level(game):
    """第一张卡的等级"90"读成"9o"（小写 o），以前只认大写 O，排序方向就判断不了。"""
    from maapilot.crew import oplist
    assert oplist._first_card_level(show(game, "oplist_first_level_90")) == 90


# ---- 训练室 ---------------------------------------------------------------------
def test_training_room_busy(game):
    """训练位的"训练中"OCR 读成"川练中"：按词认（mastery.slot_busy），不再当成空闲。"""
    from maapilot.crew import mastery
    g = show(game, "training_busy")
    assert g.find_word(*mastery.PLAN_MARK)                               # 截得紧，读成"办助者"
    if OCR_GEN == "v3":
        assert [h.text for h in g.ocr(mastery.SLOT_STATE_ROI)] == ["川练中"]      # 原样读还是错的（v6 读对了）
    assert mastery.slot_busy(g)


# ---- screen ocr --image ---------------------------------------------------------
def _cli(capsys, *argv):
    import json

    from maapilot import cli
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_screen_ocr_on_image(capsys):
    """不连模拟器（测试里 common.connect 一调就报错）、不拿锁，直接识别这张图。"""
    img = SCREENS / "home_130.png"
    code, env = _cli(capsys, "screen", "ocr", "--image", str(img), "--roi", "640", "15", "640", "265", "--find", "理智")
    assert code == 0, env
    assert env["data"]["path"] == str(img.resolve())
    assert [h["text"] for h in env["data"]["hits"]] == [{"v3": "理智/2O5"}.get(OCR_GEN, "理智/205")]


def test_screen_ocr_bad_image(capsys, tmp_path):
    code, env = _cli(capsys, "screen", "ocr", "--image", str(tmp_path / "nope.png"))
    assert (code, env["error"]["code"]) == (2, "bad_image")
    (tmp_path / "x.png").write_bytes(b"not a png")
    code, env = _cli(capsys, "screen", "ocr", "--image", str(tmp_path / "x.png"))
    assert (code, env["error"]["code"]) == (2, "bad_image")
