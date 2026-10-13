"""Workshop crafting (加工站 → 精英材料) for T3+ upgrade materials.

Planner: resolve a wanted {item: count} against the depot recursively down the recipe tree
(only T3+ recipes are known; T1/T2 shortfalls are reported as "must farm").
Executor: drive the workshop UI to craft the planned batches, lowest tier first.
"""
from __future__ import annotations

import re
import time

from maapilot.crew import overlays, reading
from maapilot.crew.core import REF_W, Game
from maapilot.knowledge import recipes as _recipes

# 中文名视图（OCR 只认名字）：{名: {count, lmd, costs: {名: n}}}，只含精英材料配方；数据源见 maapilot.knowledge.recipes
# 加工站配方按名字查：数据表在 data/，用到时才读（导入时不读，没同步数据也能导入）

# --- planner -----------------------------------------------------------------

def plan_crafts(wanted: dict[str, int], depot: dict[str, int], extra: bool = False) -> dict:
    """Return {"batches": [(item, times)], "lmd": int, "consume": {item: n}, "farm": {item: n}} keyed by Chinese name.
    batches are ordered so that ingredients are crafted before the recipes that use them.
    Name wrapper around the pure, id-keyed maapilot.knowledge.recipes.plan_crafts_ids (names it can't resolve
    are kept verbatim and end up in "farm", as before)."""
    def to_id(name: str) -> str:
        try:
            return _recipes.resolve_item(name)
        except _recipes.UnknownNameError:
            return name

    def by_name(d: dict[str, int]) -> dict[str, int]:
        return {_recipes.display_name(k): v for k, v in d.items()}

    ids: dict[str, int] = {}
    for k, v in depot.items():
        iid = to_id(k)
        ids[iid] = ids.get(iid, 0) + v
    cp = _recipes.plan_crafts_ids({to_id(k): v for k, v in wanted.items()}, ids, extra=extra)
    return {"batches": [(_recipes.display_name(i), t) for i, t in cp["batches"]], "lmd": cp["lmd"],
            "consume": by_name(cp["consume"]), "farm": by_name(cp["farm"])}


# --- UI ---------------------------------------------------------------------
FORMULA_CENTER = (610, 485)
CAT_ELITE = (150, 222)
LIST_ROI = (250, 80, 1030, 640)
COUNT_ROI = (1040, 330, 140, 90)
BTN_PLUS, BTN_MINUS, BTN_MIN = (1073, 288), (1073, 477), (1210, 477)
BTN_START = (1110, 635)
STOP_WORDS = ("不足", "无法", "失败", "上限")


class CraftError(RuntimeError):
    pass


def goto_workshop(g: Game) -> None:
    """State-aware: works from the crafting screen, the room, the base overview or anywhere else.
    进加工站用 nav.goto_room（和训练室同一条路：抽屉 → 基建 → 进驻总览）。以前这里自己点左上角 (270,38) 当抽屉，
    从首页出发就进不去：首页没有抽屉，那个位置一直是月签到的图标（nav.open_drawer 一直知道这件事），不是版本更新改的。"""
    from maapilot.crew import nav
    for attempt in range(4):
        if g.find(r"离开罗德岛基建", (300, 260, 700, 80)):     # "leave the base?" prompt: stay
            g.tap(443, 495, delay=2.0)
        if g.find(r"加工份数", (1040, 400, 140, 50)):
            return
        hit = g.find(r"点击进行加工", (0, 660, 200, 50))
        if hit:
            g.tap_hit(hit, delay=3)
            g.wait(r"加工份数", (1040, 400, 140, 50))
            return
        try:
            nav.goto_room(g, r"加工站")
        except RuntimeError:
            continue
    g.save("goto_workshop_fail")
    raise CraftError("could not reach the workshop crafting screen")


def _norm(t: str) -> str:
    return t.strip().upper().replace("O", "0").replace("—", "-").replace("–", "-")


def _is_formula(text: str, name: str, vocab) -> bool:
    """列表上的这段字是不是 name 这个配方：原样相同，或按词认（读错一个字也算，"环烽预制体" → 环烃预制体；
    vocab 是全部精英材料配方名，差一个字的别的配方（异铁组 / 异铁块）不会认错）。"""
    return _norm(text) == _norm(name) or reading.matches(text, name, vocab)


def select_formula(g: Game, name: str, max_pages: int = 20) -> None:
    vocab = list(_recipes.workshop_by_name())
    g.tap(*FORMULA_CENTER, delay=2.0)
    hit = g.wait(r"精英材料", (60, 190, 180, 60))
    g.tap_hit(hit, delay=1.5)
    for _ in range(6):                                   # the picker remembers its scroll position
        g.swipe(640, 300, 640, 600, duration_ms=300, delay=0.4)
    time.sleep(0.8)
    prev = None
    for _ in range(max_pages):
        hits = [h for h in g.ocr(LIST_ROI) if h.score > 0.6]
        names = [h.text for h in hits]
        for h in hits:
            if _is_formula(h.text, name, vocab):
                x, y = h.center
                if any("解锁" in o.text for o in hits if abs(o.box[1] - y) < 120 and abs(o.box[0] - x) < 200):
                    raise CraftError(f"formula {name!r} is locked (needs a stage clear)")
                g.tap(x, y, delay=2.0)                   # tapping the header selects the formula
                g.wait(r"加工份数", (1040, 400, 140, 50))
                return
        if names == prev:
            break
        prev = names
        g.swipe(640, 550, 640, 300, duration_ms=800, delay=1.2)
    g.save("select_formula_fail")
    raise CraftError(f"formula {name!r} not found in 精英材料")


ING_ROI = (520, 280, 180, 60)      # "have/need" label under the top ingredient
# 标签本身（深色小框，右边缘 x≈654，左边随数字长短）：白字（不够时"有"是红字），细斜杠常丢（16/16 → 6716、8/1 → 8Z1）。
# 原样读一遍，再按 RGB 最大值 / R、G 较小值二值化、放大两倍读几遍，投票（reading.vote）。
LABEL_ROI = (550, 292, 107, 30)
LABEL_READS = (("max", 150), ("max", 170), ("max", 190), ("min_rg", 150))
TOAST_ROI = (940, 60, 340, 110)    # 右上角的提示条（"材料不足"）


def read_label(g: Game, img=None) -> tuple[int, int] | None:
    """第一种原料下面的"有/要" → (有, 要)；几种读法说法不一或都读不出 → None。"""
    img = img if img is not None else g.screencap()
    readings = [reading.parse_have_need(h.text) for h in g.ocr(ING_ROI, img)]
    for channel, th in LABEL_READS:
        b, roi = reading.binarize(img, LABEL_ROI, threshold=th, channel=channel, scale=2)
        readings.append(reading.parse_have_need("".join(h.text for h in g.ocr(roi, b))))
    return reading.vote(readings)


def read_count(g: Game, name: str, img=None) -> int:
    """Craft count = need shown under the first ingredient / per-craft cost of that ingredient; -1 when unread.
    (The big yellow count digit and the 龙门币 cost OCR badly -- their font's 0 reads as Z; the ingredient label is
    the one that reads, see read_label.)"""
    per = next(iter(_recipes.workshop_by_name()[name]["costs"].values()))
    pair = read_label(g, img)
    if pair is None or pair[1] % per:
        return -1
    return pair[1] // per


def toast(g: Game, img=None) -> str | None:
    """右上角提示条里的"材料不足"之类（STOP_WORDS）；没有 → None。"""
    for h in g.ocr(TOAST_ROI, img):
        if any(w in h.text for w in STOP_WORDS):
            return h.text
    return None


def set_count(g: Game, name: str, n: int) -> None:
    """把加工份数调到 n。读不出份数、弹了"材料不足"、点了不变，都马上停下报错，不接着点
    （以前读不出时一直点"+"，10-08 点到弹"材料不足"还在点）。"""
    g.tap(*BTN_MIN, delay=0.8)
    cur = read_count(g, name)
    prev, stalls = None, 0
    for _ in range(60):
        if cur < 0:
            raise CraftError(f"{name}: cannot read the craft count (ingredient label unread); stopped at the count "
                             f"shown, wanted {n}")
        if cur == n:
            return
        msg = toast(g)
        if msg:
            raise CraftError(f"{name}: {msg} (count {cur}, wanted {n})")
        stalls = stalls + 1 if cur == prev else 0
        if stalls >= 2:
            raise CraftError(f"{name}: craft count does not change (stuck at {cur}, wanted {n})")
        prev = cur
        g.tap(*(BTN_PLUS if cur < n else BTN_MINUS), delay=0.4)
        cur = read_count(g, name)
    raise CraftError(f"could not set count to {n} (stuck at {cur})")


def _back_to_workshop(g: Game) -> bool:
    """加工完回到加工站：关掉"获得物资"（和别的认得的遮挡），看到"加工份数"为止。"""
    for _ in range(10):
        if g.find(r"加工份数", (1040, 400, 140, 50)):
            return True
        if overlays.dismiss(g):
            continue
        g.tap(640, 650, delay=1.5)
    return False


def craft_once(g: Game, name: str, times: int) -> dict:
    """合 times 份 name。→ {item, times, before, after, verified}：before / after 是第一种原料的(有, 要)，
    verified 为真 = 原料正好少了这次要用的数（真合成了）；读不出为 None。"""
    select_formula(g, name)
    set_count(g, name, times)
    before = read_label(g)
    for h in g.ocr((960, 120, 320, 560)):
        if any(w in h.text for w in STOP_WORDS):
            raise CraftError(f"{name}: {h.text}")
    g.tap(*BTN_START, delay=2.0)
    for _ in range(12):                          # "获得物资" result screen: confirm with the check mark
        if g.find(r"获得物资", (500, 120, 280, 60)):
            g.tap(640, 640, delay=2.0)
            break
        time.sleep(1)
    if not _back_to_workshop(g):
        try:
            goto_workshop(g)                     # 没回来：自己走回加工站，再看原料少没少
        except CraftError:
            raise CraftError("did not return to the workshop screen") from None
    after = read_label(g)
    verified = None
    if before and after:
        verified = after[0] == before[0] - before[1]
    return {"item": name, "times": times, "before": before, "after": after, "verified": verified}


def run_crafts_in_session(g: Game, batches: list[tuple[str, int]]) -> list[dict]:
    goto_workshop(g)
    out = []
    for name, times in batches:
        res = craft_once(g, name, times)
        out.append({"item": name, "times": times, "ok": True, "verified": res.get("verified")})
    return out


def run_crafts(batches: list[tuple[str, int]]) -> list[dict]:
    g = Game()
    goto_workshop(g)
    out = []
    for name, times in batches:
        try:
            craft_once(g, name, times)
            out.append({"item": name, "times": times, "ok": True})
        except Exception as e:
            g.save(f"craft_error_{name}")
            out.append({"item": name, "times": times, "error": str(e)})
            break
    return out
