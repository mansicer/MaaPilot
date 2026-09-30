"""采购中心兑换（凭证交易所）：为养成目标兑换刷不到的材料（芯片助剂），见 docs/concepts/jobs.md「执行」、决策 D135。

页面流程已按真机采集（2026-09-29）写好；用户 2026-09-29 确认凭证规则后放开（`READY = True`，D135）。
`READY` 为假时 `buy()` 不连模拟器、直接抛 `ShopNotReady`，规划器也不排兑换（`uncalibrated`）。

流程：首页 → 采购中心 → 凭证交易所 → 采购凭证区 → 读顶栏余额 → 横滑找到商品卡 → 点开购买框 → 用"+"设数量 →
**购买框核对**（商品名、购买数量、总计支付 = 数量 × 单价、余额够、没有"不足 / 无法"）→ 点"购买物品" → 关掉获得物资 → 再读余额。
购买框没有取消按钮，核对不过按返回键关掉、什么都不花（`ShopAborted`）；确认之后读不到余额不抛错，`after` 为 None，由调用方对账。

纯函数（`parse_balance`、`check_confirm`）不导入 MaaFramework，可以离线测试。
"""
from __future__ import annotations

import re
import time

READY = True                          # 用户 2026-09-29 确认凭证规则后放开（D135）；关掉就不连模拟器、规划器标 uncalibrated

# --- 页面（1280x720 参考坐标系，真机采集 2026-09-29） -------------------------------------------------------------
STORE_ENTRY = r"^采购中心$"            # 首页右侧的入口按钮文字（首页没单独截图，按导航菜单同名项；待核对）
STORE_TITLE_ROI = (0, 0, 420, 100)     # 左上角（与 pagerules 的 store 规则同一处）
TABS_ROI = (0, 85, 1280, 45)           # 顶部标签栏 y≈106：可露希尔推荐 / 源石交易所 / 组合包 / 时装商店 / 凭证交易所(x≈836) / 家具商店 / 信用交易所
SUBTABS_ROI = (0, 150, 250, 510)       # 凭证交易所左侧分区 x≈170：资质 y≈193、高级 y≈291、通用 y≈390、采购 y≈488、寻访参数模型 y≈586
BALANCE_ROI = (1070, 20, 85, 40)       # 顶栏余额 图标+数字：资质 / 高级 / 通用 / 采购凭证(第 4 个，红，数字 x≈1085–1140, y≈40) / 情报
GOODS_ROI = (255, 140, 1025, 510)      # 商品卡 4×2：顶部名称条、右上"剩余N"、底部价格条
GOODS_SWIPE = ((1150, 400), (350, 400))  # 商品网格横着翻：芯片助剂在第二屏第一列第一行（≈433,260）
GOODS_PAGES = 4                        # 最多翻几屏
DIALOG_ROI = (145, 150, 1000, 490)     # 购买框整体
NAME_ROI = (175, 175, 300, 45)         # 购买框左上的商品名
QTY_ROI = (878, 410, 82, 45)           # 购买数量（最少 769 / − 843 / 数量 918 / + 993 / 最多 1068，y 432）
TOTAL_ROI = (905, 500, 110, 40)        # "总计支付" 右边的数字（前面有红色图标）
BTN_PLUS = (993, 432)
BTN_BUY = (918, 580)                   # "购买物品"
BUY_TEXT = r"购买物品"
STOP_WORDS = ("不足", "无法", "失败", "上限")


class ShopError(RuntimeError):
    pass


class ShopNotReady(ShopError):
    """流程没放开（READY 为假）：没有碰模拟器。"""


class ShopAborted(ShopError):
    """确认之前就停下了：什么都没花。`balance` 是读到的余额（读不到为 None）。"""

    def __init__(self, message: str, balance: int | None = None):
        super().__init__(message)
        self.balance = balance


# --- 纯函数 -----------------------------------------------------------------------------------------------

def _digits(t: str) -> int | None:
    """一段 OCR 文字里的数：逗号分隔也算，前面粘着一两个图标误读的字符也算（"[]90"）；别的形状 → None。"""
    t = t.replace("O", "0").replace("o", "0").replace(",", "").replace("，", "").replace(" ", "")
    m = re.fullmatch(r"\D{0,2}(\d+)", t)
    return int(m.group(1)) if m else None


def parse_balance(texts: list[str]) -> int | None:
    """一小块区域的 OCR 文字 → 其中唯一的数；没有或有多个不同的数 → None（读不准不猜）。"""
    nums = {n for t in texts if (n := _digits(t)) is not None}
    return nums.pop() if len(nums) == 1 else None


def check_confirm(*, name: list[str], qty: list[str], total: list[str], dialog: list[str], item_name: str, count: int,
                  unit_price: int, balance: int | None) -> list[str]:
    """购买框核对（纯函数）：左上商品名是 item_name；购买数量区读出 count；总计支付区读出 count × unit_price；
    余额够；整个购买框里没有"不足 / 无法"字样。→ 问题列表（空 = 可以点"购买物品"）。"""
    problems = []
    if not any(item_name == t.strip() for t in name):
        problems.append(f"购买框上的商品不是 {item_name}（读到 {'、'.join(name) or '空'}）")
    if parse_balance(qty) != count:
        problems.append(f"购买数量不是 {count}（读到 {'、'.join(qty) or '空'}）")
    want = count * unit_price
    if parse_balance(total) != want:
        problems.append(f"总计支付不是 {want}（{count} × {unit_price}，读到 {'、'.join(total) or '空'}）")
    if balance is None:
        problems.append("读不到余额")
    elif balance < want:
        problems.append(f"余额 {balance} 不够 {want}")
    stop = [t for t in dialog if any(w in t for w in STOP_WORDS)]
    if stop:
        problems.append("购买框上有：" + "、".join(stop))
    return problems


# --- 执行（碰模拟器；READY 为假时不会走到这里） ---------------------------------------------------------

def _texts(g, roi) -> list[str]:
    return [h.text for h in g.ocr(roi)]


def read_balance(g) -> int | None:
    return parse_balance(_texts(g, BALANCE_ROI))


def goto_tab(g, shop: str, tab: str) -> None:
    """首页 → 采购中心 → shop（凭证交易所）→ tab（采购凭证区）。"""
    from maapilot.crew import nav
    if not g.find(STORE_ENTRY, STORE_TITLE_ROI):
        if not nav.ensure_home(g):
            raise ShopError("回不到首页")
        g.tap_hit(g.wait(STORE_ENTRY), delay=3.0)
    g.tap_hit(g.wait(re.escape(shop), TABS_ROI), delay=2.5)
    g.tap_hit(g.wait(re.escape(tab), SUBTABS_ROI), delay=2.5)


def find_goods(g, item_name: str):
    """商品卡的名称条：当前屏没有就横着翻，最多 GOODS_PAGES 屏。"""
    for _ in range(GOODS_PAGES):
        hit = g.find(rf"^{re.escape(item_name)}$", GOODS_ROI)
        if hit is not None:
            return hit
        (x1, y1), (x2, y2) = GOODS_SWIPE
        g.swipe(x1, y1, x2, y2, duration_ms=600, delay=1.5)
    return None


def buy(item_name: str, count: int, *, shop: str, tab: str, unit_price: int) -> dict:
    """兑换 count 件 item_name。→ {item, count, unit_price, before, after}（after 读不到为 None）。
    READY 为假 → ShopNotReady（不碰模拟器）；确认前停下 → ShopAborted（什么都没花）。"""
    if not READY:
        raise ShopNotReady("采购中心兑换还没放开（maapilot.crew.shop.READY = False：规则文字待用户确认）")
    if count <= 0:
        raise ShopAborted("数量要大于 0")
    from maapilot.crew.core import Game
    g = Game()
    goto_tab(g, shop, tab)
    before = read_balance(g)
    if before is None:
        g.save("shop_balance_unread")
        raise ShopAborted("读不到余额")
    if before < count * unit_price:
        raise ShopAborted(f"余额 {before} 不够 {count * unit_price}", balance=before)
    hit = find_goods(g, item_name)
    if hit is None:
        g.save("shop_item_not_found")
        raise ShopAborted(f"{tab} 里找不到 {item_name}", balance=before)
    g.tap_hit(hit, delay=2.0)                      # 点名称条打开购买框
    g.wait(BUY_TEXT, DIALOG_ROI, timeout=8)
    for _ in range(count - 1):
        g.tap(*BTN_PLUS, delay=0.4)
    time.sleep(0.5)
    problems = check_confirm(name=_texts(g, NAME_ROI), qty=_texts(g, QTY_ROI), total=_texts(g, TOTAL_ROI),
                             dialog=_texts(g, DIALOG_ROI), item_name=item_name, count=count, unit_price=unit_price,
                             balance=before)
    if problems:
        g.save("shop_confirm_mismatch")
        g.back(delay=1.5)                          # 购买框没有取消按钮，返回键关掉（真机核对过）
        raise ShopAborted("购买框核对不过，没有购买：" + "；".join(problems), balance=before)
    g.tap(*BTN_BUY, delay=2.5)
    for _ in range(8):                             # 获得物资：点空白关掉
        if g.find(r"获得物资", (500, 120, 280, 60)):
            g.tap(640, 640, delay=2.0)
            break
        time.sleep(1)
    g.save("shop_after")
    return {"item": item_name, "count": count, "unit_price": unit_price, "before": before, "after": read_balance(g)}
