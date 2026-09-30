"""制造站造双芯片（2 × 芯片组 + 1 × 芯片助剂，制造站 3 级），见决策 D135。

双芯片的配方耗时是 1 秒（`building_data.manufactFormulas` 的 `costPoint`，"相见欢"版本起对所有人都是这样，不需要特定干员），
所以一次调用里就能造完：**记下原产物与份数 → 收取已有产物 → 改产双芯片 → 造 → 收取 → 改回原产物与份数**，不跨运行，
MAA 的基建换班也就没有机会在生产中途插进来。流程按真机采集（2026-09-29，一次真实的改产 → 造 → 收取 → 改回）写成。

真机上看到的：
- 改产要确认两次：选中产物后生产份数旁的小弹窗"执行更改?"（✗ 853,597 / ✓ 950,597），再是整屏的
  "更改配方后将取消当前制造计划，是否确认更改？"（✗ 443,494 / ✓ 842,494），计划是空的或做完的也会弹。
- 改产会取消当前的制造计划：正在做的那一份的进度没了（作战记录不耗原料，没有材料损失），排着的份数要改回时重新设。
- 双芯片 1 秒后产物卡显示"已完成"，仓库容量 05/42，"收取"之后回到 00/42。
- 基建总览里有产物可收的卡片，第一下点是收取、第二下才进房间；离开基建会弹"是否确认离开罗德岛基建？"，要先确认。
- 产物卡上的"已完成"OCR 会读成"已克成"，仓库容量读成"Z/54"（2026-09-30 日常的 maafw 日志）：只认字形不稳的字会漏判。
- 没收取就改回原产物时，已造好的双芯片照样进了仓库（同一次日常：没点"收取"，仓库多了 4 个双芯片）。

原产物改不回去（读不出原产物、找不到它、改产失败）不抛错：返回 `restored: False` 和当时的截图 `shot`，
由调用方交给 agent 改回。改产之后出的错同样先试着改回，
ManufactureError 带 `switched`（动过产物没有）与 `restored`；动过产物、又说不清改回没有的，调用方一样当没改回。
"""
from __future__ import annotations

import re
import time

READY = True                          # 页面流程已在真机上核对（2026-09-29）

# --- 页面（1280x720 参考坐标系，真机采集 2026-09-29） -------------------------------------------------------------
ROOM_LABEL = r"制造站"                  # 基建总览里制造站格子的文字；房间页顶栏是"制造站B102"
ROOM_TITLE_ROI = (380, 15, 300, 50)     # 房间页 / 生产面板顶栏的房间名
PRODUCT_PANEL = (270, 600)              # 房间页左下的产物面板，点它进生产面板（pagerules mfg_production）
PANEL_MARK = (r"生产份数", (800, 370, 200, 50))
BTN_PLUS, BTN_MAX = (865, 205), (965, 205)          # 生产份数 +、最多
BTN_MINUS, BTN_MIN = (865, 460), (965, 460)         # 生产份数 −、最少
COUNT_ROI = (840, 280, 120, 90)          # 生产份数的大号数字（"99" / "01" / "00"）
PRODUCT_CARD = (1125, 300)               # 右侧产物卡：点它进产物选择（pagerules mfg_product_list）
PRODUCT_NAME_ROI = (1030, 235, 190, 45)  # 产物卡上的产物名
DONE_ROI = (1030, 310, 190, 40)          # 产物卡上的"已完成"
DONE_MARK = r"已.?成"                     # OCR 会把"已完成"读成"已克成"
CAPACITY_ROI = (1090, 530, 130, 40)      # 仓库容量 "05/42"
BTN_COLLECT = (1125, 640)                # 收取
LIST_MARK = (r"品质|耗时|序列", (780, 15, 340, 55))
CATEGORY_TABS = {"作战记录": (150, 128), "贵金属": (150, 222), "芯片": (150, 318), "源石材料": (150, 412)}
PRODUCT_LIST_ROI = (250, 85, 1000, 620)  # 产物卡片的标题（芯片分页两列，辅助 / 特种在最下面要往上滑）
LIST_SWIPE = ((740, 600), (740, 250))
EXEC_ROI = (800, 505, 200, 50)           # "执行更改?"
EXEC_OK, EXEC_NO = (950, 597), (853, 597)
CANCEL_PLAN = (r"取消当前制造计划", (300, 280, 700, 90))
CANCEL_PLAN_OK = (842, 494)
WEIGHT_DUAL_CHIP = 5                     # manufactFormulas.weight：每个双芯片占的库存
DONE_WAIT_S = 15                         # 1 秒一个，留足余量


class ManufactureError(RuntimeError):
    """造的过程中出错。`made` 是确认造好、收取了的个数；`pending` 是等不到"已完成"的那一轮的个数（可能已经造好：
    改回原产物时会直接进仓库，以之后的仓库识别为准）；`switched` 是动过产物没有（False = 没碰产物，不用改回）；
    `restored` 是原产物改回去没有（None = 没改过产物）；`original` / `original_count` 是原产物与份数，`shot` 是没改回时的截图。"""

    def __init__(self, message: str, *, made: int = 0, pending: int = 0, switched: bool = False,
                 restored: bool | None = None, original: str | None = None, original_count: int | None = None,
                 shot: str | None = None):
        super().__init__(message)
        self.made, self.pending, self.switched, self.restored = made, pending, switched, restored
        self.original, self.original_count, self.shot = original, original_count, shot


class ManufactureNotReady(ManufactureError):
    """流程没放开：没有碰模拟器。"""


# --- 纯函数 -----------------------------------------------------------------------------------------------

def rounds(times: int, capacity: int | None, weight: int = WEIGHT_DUAL_CHIP) -> list[int]:
    """按库存上限（`CAPACITY_ROI` 读到的，例如 42）把 times 个分成几轮；容量未知时一轮做完（由游戏的上限提示兜底）。"""
    per = max(1, capacity // weight) if capacity else times
    out = []
    while times > 0:
        out.append(min(per, times))
        times -= out[-1]
    return out


def category(product: str) -> str | None:
    """产物名 → 产物选择左侧的分类；认不出 → None。"""
    if "双芯片" in product:
        return "芯片"
    if "作战记录" in product:
        return "作战记录"
    if "赤金" in product:
        return "贵金属"
    if "源石" in product:
        return "源石材料"
    return None


_DIGIT_LOOKALIKE = str.maketrans({"O": "0", "o": "0", "Z": "2", "z": "2"})


def _digits(t: str) -> str:
    return t.translate(_DIGIT_LOOKALIKE)


def parse_count(texts: list[str]) -> int | None:
    """生产份数 / 数字：唯一的整数；读不准 → None。"""
    nums = {int(_digits(t)) for t in texts if re.fullmatch(r"\d+", _digits(t))}
    return nums.pop() if len(nums) == 1 else None


def parse_capacity(texts: list[str]) -> tuple[int, int] | None:
    """仓库容量 "05/42" → (5, 42)（OCR 把 0 / 2 读成 O / Z 的照样认）；读不出 → None。"""
    for t in texts:
        m = re.search(r"(\d+)\s*/\s*(\d+)", _digits(t))
        if m:
            used, cap = (int(x) for x in m.groups())
            return used, cap
    return None


# --- 执行（碰模拟器） ---------------------------------------------------------------------------------------

def _texts(g, roi) -> list[str]:
    return [h.text for h in g.ocr(roi)]


def _name(g) -> str:
    return "".join(_texts(g, PRODUCT_NAME_ROI)).strip()


def _count(g) -> int | None:
    return parse_count(_texts(g, COUNT_ROI))


def open_panel(g) -> None:
    """从任何地方进一间制造站的生产面板。总览上有产物的卡片第一下是收取，所以点过以后再看一次。"""
    from maapilot.crew import nav
    from maapilot.crew.core import REF_W
    for _ in range(10):
        if g.find(*PANEL_MARK):
            return
        if g.find(r"设施信息", (10, 170, 120, 50)):                  # 某个房间里
            if g.find(ROOM_LABEL, ROOM_TITLE_ROI):
                g.tap(*PRODUCT_PANEL, delay=2.5)
            else:
                g.back(delay=2.5)
            continue
        if g.find(r"进驻总览", (20, 95, 140, 50)):                    # 基建总览
            hit = g.find(ROOM_LABEL, (0, 60, REF_W, 620))
            if hit is not None:
                g.tap_hit(hit, delay=3.0)                             # 可能只是收取了产物：下一轮再点
            else:
                g.swipe(900, 400, 500, 400, duration_ms=500, delay=1.5)
            continue
        nav.goto(g, "基建")
        time.sleep(3)
    g.save("mfg_panel_fail")
    raise ManufactureError("进不了制造站的生产面板")


def collect(g) -> bool:
    """点"收取"；弹出"获得物资" → True（关掉它）。"""
    g.tap(*BTN_COLLECT, delay=2.5)
    if g.find(r"获得物资", (500, 120, 280, 60)):
        g.tap(640, 640, delay=2.0)
        return True
    return False


def wait_done(g, n: int) -> bool:
    """这一轮 n 个造完了没有：产物卡上出现"已完成"（字形宽松匹配），或等满 DONE_WAIT_S 后仓库容量已占到 n 个的量。"""
    for _ in range(DONE_WAIT_S):
        if g.find(DONE_MARK, DONE_ROI):
            return True
        time.sleep(1)
    cap = parse_capacity(_texts(g, CAPACITY_ROI))
    return bool(cap and cap[0] >= n * WEIGHT_DUAL_CHIP)


def select(g, product: str, *, count: int | None = None, restore_count: int | None = None) -> None:
    """改产 product：产物卡 → 分类 → 卡片 → "执行更改?" 时设份数 → ✓ → "取消当前制造计划" ✓。
    count：造几份（按"+"加到正好）；restore_count：改回原产物时的份数（"最多"之后按"−"减到它；0 或读不到原份数时用"最少"）。"""
    cat = category(product)
    if cat is None:
        raise ManufactureError(f"认不出 {product} 在哪个分类")
    g.tap(*PRODUCT_CARD, delay=2.0)
    g.wait(*LIST_MARK, timeout=8)
    g.tap(*CATEGORY_TABS[cat], delay=1.5)
    hit = None
    for _ in range(3):
        hit = g.find(rf"^{re.escape(product)}$", PRODUCT_LIST_ROI)
        if hit is not None:
            break
        (x1, y1), (x2, y2) = LIST_SWIPE
        g.swipe(x1, y1, x2, y2, duration_ms=500, delay=1.2)
    if hit is None:
        g.save("mfg_product_not_found")
        g.back(delay=1.5)
        raise ManufactureError(f"产物列表里找不到 {product}")
    g.tap_hit(hit, delay=2.0)
    g.wait(r"执行更改", EXEC_ROI, timeout=8)
    if product not in _name(g):
        g.tap(*EXEC_NO, delay=1.5)
        raise ManufactureError(f"选中的产物不是 {product}（读到 {_name(g)}）")
    if count is not None:
        for _ in range(60):
            n = _count(g)
            if n == count:
                break
            g.tap(*(BTN_PLUS if n is None or n < count else BTN_MINUS), delay=0.4)
        if _count(g) != count:
            g.tap(*EXEC_NO, delay=1.5)
            raise ManufactureError(f"生产份数设不到 {count}（读到 {_count(g)}）")
    elif restore_count:
        g.tap(*BTN_MAX, delay=0.8)
        for _ in range(120):
            n = _count(g)
            if n is None or n <= restore_count:
                break
            g.tap(*BTN_MINUS, delay=0.3)
    else:
        g.tap(*BTN_MIN, delay=0.8)
    g.tap(*EXEC_OK, delay=2.0)
    if g.find(*CANCEL_PLAN):
        g.tap(*CANCEL_PLAN_OK, delay=2.5)
    g.wait(*PANEL_MARK, timeout=8)


def _restore(g, original: str | None, original_count: int | None) -> bool:
    """改回原产物与份数。先回到生产面板；产物和份数本来就对（改产没生效）就不再改，免得又取消一次计划。"""
    if not original or category(original) is None:
        return False
    try:
        open_panel(g)
        if original in _name(g) and (original_count is None or _count(g) == original_count):
            return True
        select(g, original, restore_count=original_count)
        return original in _name(g)
    except Exception:                                   # noqa: BLE001 - 改不回去就报 restored: False
        return False


def _restore_shot(g) -> str | None:
    """没改回时的截图（maafw_debug/mfg_restore_fail.png），给接管的 agent 看；存不了就不给。"""
    try:
        return str(g.save("mfg_restore_fail"))
    except Exception:                                   # noqa: BLE001
        return None


def _home(g) -> None:
    from maapilot.crew import nav
    try:
        nav.ensure_home(g)                               # 会先确认"离开罗德岛基建"
    except Exception:                                   # noqa: BLE001 - 回不了首页不影响造了多少、改回没有
        pass


def make(item_name: str, times: int) -> dict:
    """在一间 3 级制造站造 times 个 item_name（双芯片），造完改回原产物与份数。
    → {item, times, made, original, original_count, restored, shot?}。改回失败不抛错：`restored: False` 带截图 `shot`
    （调用方交给 agent 改回）。造的过程中出错抛 ManufactureError（带 made / pending / switched / restored /
    original / original_count / shot），抛之前照样试着改回；没碰产物之前出的错 `switched` 为 False。"""
    if not READY:
        raise ManufactureNotReady("制造站造双芯片的流程没放开（maapilot.crew.manufacture.READY = False）")
    from maapilot.crew.core import Game
    try:
        g = Game()
        open_panel(g)
        original, original_count = _name(g) or None, _count(g)
        cap = parse_capacity(_texts(g, CAPACITY_ROI))
        if cap and cap[0] > 0:
            collect(g)                                   # 先收走原来的产物，免得改产时被取消
    except Exception as e:
        raise ManufactureError(str(e) if isinstance(e, ManufactureError) else f"{type(e).__name__}: {e}") from e
    made, pending, switched = 0, 0, False
    try:
        for n in rounds(times, cap[1] if cap else None):
            switched = True                              # select 半途出错时产物可能已经改了
            select(g, item_name, count=n)
            if not wait_done(g, n):
                pending = n
                raise ManufactureError(f"{item_name}×{n} 等了 {DONE_WAIT_S} 秒没看到“已完成”（原料不够，或没认出来；"
                                       f"已造好的会在改回原产物时进仓库，以之后的仓库识别为准）")
            collect(g)
            made += n
    except Exception as e:
        restored = _restore(g, original, original_count) if switched else None
        shot = _restore_shot(g) if restored is False else None
        _home(g)
        msg = str(e) if isinstance(e, ManufactureError) else f"{type(e).__name__}: {e}"
        raise ManufactureError(msg, made=made, pending=pending, switched=switched, restored=restored,
                               original=original, original_count=original_count, shot=shot) from e
    restored = _restore(g, original, original_count)
    shot = _restore_shot(g) if not restored else None
    _home(g)
    return {"item": item_name, "times": times, "made": made, "original": original, "original_count": original_count,
            "restored": restored, **({"shot": shot} if shot else {})}
