"""识别回归测试：在真机截图（tests/fixtures/screens/）上跑真的 MaaFramework OCR 和读页面的函数，对照人眼看出的正确答案。

别的测试都是假的 OCR 结果；真机上出过的错都在识别这一层（首页理智 130 读成 29、专精确认框 57/15 读成 5715……）。
修识别的毛病先把出错的截图做成样本加进来（做法见 docs/design.md「识别回归测试」），看它失败，再改。
还没修的标 xfail(strict=True)：修好了会变成 XPASS 报错，提醒把 xfail 去掉。

OCR 模型（resource/model/ocr/，不入库）不在时整个文件跳过。
"""
from pathlib import Path

import pytest

from maapilot import paths

SCREENS = Path(__file__).parent / "fixtures" / "screens"
HAS_OCR = (paths.OCR_MODEL / "rec.onnx").exists() and (paths.OCR_MODEL / "det.onnx").exists()
HAS_RECIPES = (paths.DATA / "recipes.json").exists()

pytestmark = pytest.mark.skipif(not HAS_OCR, reason="没有 OCR 模型（resource/model/ocr/）")


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


@pytest.mark.parametrize("value, top", [
    (130, {"lmd": 9621162, "orundum": 19866, "originite": 26}),
    (110, {"lmd": 9397902, "orundum": 19066, "originite": 16}),
])
def test_home_topbar(game, value, top):
    from maapilot.tools import state
    g = show(game, f"home_{value}")
    assert state.parse_topbar(g.ocr(state.HOME_ROI, g.screencap())) == top


def test_image_game_records_taps_without_device(game):
    g = show(game, "home_130")
    g.tap(10, 20)
    g.swipe(1, 2, 3, 4)
    assert g.actions == [("tap", 10, 20), ("swipe", 1, 2, 3, 4)]
    assert g.screencap().shape == (720, 1280, 3)


# ---- 专精确认框 -----------------------------------------------------------------
# 底下一排"有/要"：OCR 常把细的"/"丢掉（57/15 → 5715、7/6 → 76）或读成"|"，配对就错了（10-13 报"卷3 差 9"）。
SLASH_DROPPED = pytest.mark.xfail(strict=True, reason="专精确认框：OCR 把有/要中间的斜杠丢了或读成 |（mastery.open_dialog 待修）")

MASTERY = [
    pytest.param("mastery_dialog_60-1_57-15_6-6_7-6", 3, 24, [(60, 1), (57, 15), (6, 6), (7, 6)], marks=SLASH_DROPPED),
    pytest.param("mastery_dialog_90-1_27-12_4-4_13-8", 2, 16, [(90, 1), (27, 12), (4, 4), (13, 8)], marks=SLASH_DROPPED),
    pytest.param("mastery_dialog_70-1_47-12_4-4_11-9", 2, 16, [(70, 1), (47, 12), (4, 4), (11, 9)], marks=SLASH_DROPPED),
]


@pytest.mark.parametrize("name, rank, hours, have_need", MASTERY)
def test_mastery_dialog(game, name, rank, hours, have_need):
    from maapilot.crew import mastery
    g = show(game, name)
    assert mastery.open_dialog(g, 2) == {"target_rank": rank, "have_need": have_need, "hours": hours}
    assert g.actions == [("tap", *mastery.SKILL_BTN[2])]


# ---- 加工站份数 -----------------------------------------------------------------
# 第一种原料下面的"有/要"标签，要 ÷ 每份用量 = 份数。斜杠同样会丢（16/16 → 6716、8/1 → 8Z1、1/1 → 1）。
CRAFT_SLASH = pytest.mark.xfail(strict=True, reason="加工站原料标签：OCR 把有/要中间的斜杠丢了（craft.read_count 待修）")

CRAFT = [
    ("craft_D32钢_16-1", "D32钢", 1),
    ("craft_固化纤维板_14-1", "固化纤维板", 1),
    ("craft_晶体电路_59-2", "晶体电路", 1),
    pytest.param("craft_五水研磨石_8-1", "五水研磨石", 1, marks=CRAFT_SLASH),
    pytest.param("craft_五水研磨石_1-1", "五水研磨石", 1, marks=CRAFT_SLASH),
    pytest.param("craft_全新装置_16-16", "全新装置", 4, marks=CRAFT_SLASH),
    pytest.param("craft_固源岩组_4312-5", "固源岩组", 1, marks=CRAFT_SLASH),
    pytest.param("craft_晶体电子单元_1-1", "晶体电子单元", 1, marks=CRAFT_SLASH),
    pytest.param("craft_异铁块_0-2", "异铁块", 1, marks=CRAFT_SLASH),
]


@pytest.mark.skipif(not HAS_RECIPES, reason="没有 data/recipes.json")
@pytest.mark.parametrize("name, item, count", CRAFT)
def test_craft_count(game, name, item, count):
    from maapilot.crew import craft
    assert craft.read_count(show(game, name), item) == count


# ---- 训练室 ---------------------------------------------------------------------
@pytest.mark.xfail(strict=True, reason="训练位的'训练中'OCR 读成'川练中'，mastery.open_plan 会当成空闲（待修）")
def test_training_room_busy(game):
    g = show(game, "training_busy")
    assert g.find(r"协助者", (60, 350, 150, 50))
    assert g.find(r"训练中", (140, 280, 140, 50))     # 同 mastery.open_plan


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
    assert [h["text"] for h in env["data"]["hits"]] == ["理智/2O5"]


def test_screen_ocr_bad_image(capsys, tmp_path):
    code, env = _cli(capsys, "screen", "ocr", "--image", str(tmp_path / "nope.png"))
    assert (code, env["error"]["code"]) == (2, "bad_image")
    (tmp_path / "x.png").write_bytes(b"not a png")
    code, env = _cli(capsys, "screen", "ocr", "--image", str(tmp_path / "x.png"))
    assert (code, env["error"]["code"]) == (2, "bad_image")
