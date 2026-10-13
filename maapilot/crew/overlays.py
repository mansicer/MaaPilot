"""认得的遮挡：盖在页面上、点一下就关、关掉什么都不花的弹窗和结果页。

快捷操作等某个页面（`Game.wait`）没等到时，先看是不是被这些挡住了，是就关掉再等（10-05～10-13 有 8 次专精快捷操作
被"已完成专精等级"的提示挡住失败）。只关这里列出来的、认得出的遮挡；点的位置都是关闭按钮或空白处，不会点到花钱的东西。
没列进来的（不认得的弹窗、确认框、"离开罗德岛基建"这种要看场合选的）一律不碰，交给调用方。

`nav.dismiss_dialogs` 也用这张表。坐标都是 1280×720 参考坐标系。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Overlay:
    name: str
    pattern: str                              # 正则：在 roi 里的识别文字上找
    roi: tuple[int, int, int, int]
    tap: tuple[int, int]                      # 关掉它点哪（关闭按钮 / 对勾 / 空白处）
    also: Optional[tuple[str, tuple[int, int, int, int]]] = None   # 还要同时看到的另一处文字（光一处不够认）
    delay: float = 2.0


KNOWN: tuple[Overlay, ...] = (
    # 连接故障（D137）：点 ✓ 让游戏自己重连。同 nav.OFFLINE_*
    Overlay("offline", r"未知故障|尝试重新恢复", (300, 290, 680, 90), (640, 491), delay=3.0),
    # 专精练完后点训练位（或回到训练室）先弹的整屏提示"技能 X 已完成专精等级N"，点下面的对勾（真机 10-06～10-11）
    Overlay("mastery_done", r"已完成专精等级", (300, 480, 800, 80), (802, 583), delay=2.5),
    # 加工 / 兑换 / 领取后的"获得物资"结果页：点空白处
    Overlay("items_got", r"获得物资", (500, 120, 280, 60), (640, 640)),
    # 登录公告
    Overlay("announcement", r"活动公告|系统公告|资讯速报", (340, 95, 700, 60), (1203, 70), delay=2.5),
    # 月签到（首页左上角的图标点开的）
    Overlay("sign_in", r"月签到", (0, 60, 400, 80), (1215, 68)),
    # 登录活动页（"结束时间" + LOGIN / DAY）
    Overlay("login_event", r"结束时间", (40, 590, 400, 40), (1232, 100), also=(r"LOGIN|DAY", (450, 240, 700, 80))),
    # 设置页（误点打开的）：左上返回
    Overlay("settings", r"重置设置", (1000, 20, 260, 60), (85, 45)),
    # 第一次打开干员页的"角色档案"教程
    Overlay("profile_tutorial", r"角色档案", (150, 120, 600, 150), (156, 109), also=(r"关闭", (120, 90, 100, 40)),
            delay=1.5),
)


def detect(g, img=None, known: Sequence[Overlay] = KNOWN) -> Optional[Overlay]:
    """这张截图（img；没给就截一张）上有没有认得的遮挡 → 是哪个（没有 → None）。
    每个遮挡在自己的区域里识别（和以前 nav.dismiss_dialogs 的读法一样），同一张图，不重复截图。"""
    img = img if img is not None else g.screencap()
    for ov in known:
        if g.find(ov.pattern, ov.roi, img) and (ov.also is None or g.find(ov.also[0], ov.also[1], img)):
            return ov
    return None


def dismiss(g, img=None, known: Sequence[Overlay] = KNOWN) -> Optional[str]:
    """看一眼（img 给了就用它），有认得的遮挡就点关掉，返回它的名字；没有 → None，什么都不点。"""
    ov = detect(g, img, known)
    if ov is None:
        return None
    g.tap(*ov.tap, delay=ov.delay)
    return ov.name


def dismiss_all(g, rounds: int = 4, known: Sequence[Overlay] = KNOWN) -> list[str]:
    """一层层关，最多 rounds 层（公告关掉后面还有签到……）。→ 关掉的名字（按顺序）。"""
    out = []
    for _ in range(rounds):
        name = dismiss(g, known=known)
        if name is None:
            break
        out.append(name)
    return out
