"""认得的遮挡：盖在页面上、点一下就关、关掉什么都不花的弹窗和结果页。

快捷操作等某个页面（`Game.wait`）没等到时，先看是不是被这些挡住了，是就关掉再等（10-05～10-13 有 8 次专精快捷操作
被"已完成专精等级"的提示挡住失败）。只关这里列出来的、认得出的遮挡；点的位置都是关闭按钮或空白处，不会点到花钱的东西。
没列进来的（不认得的弹窗、确认框、"离开罗德岛基建"这种要看场合选的）一律不碰，交给调用方。

`nav.dismiss_dialogs` 也用这张表。坐标都是 1280×720 参考坐标系。

关掉的都记下来（不悄悄处理掉）：本进程关过什么在 `closed()` / `closed_since()`（`input home` 的结果用）；
其中要交给 agent 的（NOTABLE：活动签到页、月签到——关掉就没领奖励，agent 要回去看）另外记进
`private/state/overlays.jsonl`（带这次日常的运行编号 MAAPILOT_RUN_ID），`daily run` 结束时按运行编号取出来写进汇报。
"""
from __future__ import annotations

import json
import os
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
    # 月签到（登录时弹的，或首页左上角的图标点开的）。关掉照样记下交给 agent（NOTABLE）
    Overlay("sign_in", r"月签到", (0, 60, 400, 80), (1215, 68)),
    # 登录活动页（新活动的限时登录签到："结束时间" + LOGIN / DAY）。关掉就没领奖励：记下交给 agent（NOTABLE）
    Overlay("login_event", r"结束时间", (40, 590, 400, 40), (1232, 100), also=(r"LOGIN|DAY", (450, 240, 700, 80))),
    # 设置页（误点打开的）：左上返回
    Overlay("settings", r"重置设置", (1000, 20, 260, 60), (85, 45)),
    # 第一次打开干员页的"角色档案"教程
    Overlay("profile_tutorial", r"角色档案", (150, 120, 600, 150), (156, 109), also=(r"关闭", (120, 90, 100, 40)),
            delay=1.5),
)


# 关掉了要告诉 agent 的遮挡 → 给人看的名字。导航时照样关（不然回不了首页），但每次都写进结果、汇报。
NOTABLE = {"login_event": "活动签到页", "sign_in": "月签到"}

_closed: list[str] = []                    # 本进程关掉过的（按顺序）
RUN_ID_ENV = "MAAPILOT_RUN_ID"             # 同 tools.common.RUN_ID_ENV（daily run 给子进程设）


def closed() -> list[str]:
    """本进程到现在关掉过的遮挡名（按顺序，有重复）。"""
    return list(_closed)


def mark() -> int:
    """记个位置，之后 closed_since(它) 取这之后关掉的。"""
    return len(_closed)


def closed_since(pos: int) -> list[str]:
    return list(_closed[pos:])


def notable(names: Sequence[str]) -> list[str]:
    """names 里要交给 agent 的（NOTABLE），去重、保持先后。"""
    out: list[str] = []
    for n in names:
        if n in NOTABLE and n not in out:
            out.append(n)
    return out


def _log_file():
    from maapilot import paths
    return paths.STATE / "overlays.jsonl"


def _record(name: str) -> None:
    """要交给 agent 的遮挡记进文件（日常的子进程关掉的，daily run 按运行编号取）。尽力而为，写不了不影响导航。"""
    try:
        from datetime import datetime
        p = _log_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "name": name,
               "run_id": os.environ.get(RUN_ID_ENV) or None}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                                    # noqa: BLE001
        pass


def seen_in_run(run_id: str) -> list[str]:
    """这次日常（运行编号）期间被关掉的、要交给 agent 的遮挡名（去重）。"""
    names = []
    try:
        lines = _log_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("run_id") == run_id:
            names.append(str(row.get("name")))
    return notable(names)


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
    _closed.append(ov.name)
    if ov.name in NOTABLE:
        _record(ov.name)
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
