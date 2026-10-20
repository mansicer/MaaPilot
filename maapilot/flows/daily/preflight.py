"""开始前：目标到期与目标快照、官方公告与停机维护、游戏版本（旧了交给在场的 agent 更新）。"""
from __future__ import annotations

from maapilot import cliutil
from maapilot.flows import goals as goalsmod
from maapilot.flows import report as reportmod
from maapilot.flows.daily.base import UPDATE, _err, _Run, _Stop
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

# --- 开始前：目标到期、官方公告、游戏版本（都是尽力而为：出错记一条告警，日常照跑） ---------------------------------------

def expire_goals() -> list:
    """过期的限时目标挪到 goals-done.md（goals.expire_due；没有这个函数就什么都不做）。"""
    f = getattr(goalsmod, "expire_due", None)
    return list(f() or []) if f else []


def goals_snapshot() -> dict | None:
    """goals.md + goals-done.md 的快照（goals.snapshot；没有这个函数 → None）。"""
    f = getattr(goalsmod, "snapshot", None)
    return f() if f else None


def goals_diff(before: dict | None, after: dict | None) -> dict | None:
    """两份快照的差 {removed, added, changed}；什么都没变、少一份 → None。"""
    f = getattr(goalsmod, "diff", None)
    if f is None or before is None or after is None:
        return None
    d = f(before, after) or {}
    return d if any(d.get(k) for k in ("removed", "added", "changed")) else None


def _goals_change(r: _Run, before: dict | None) -> None:
    """日常结束时：和开头的快照比，目标有变动就记进 data.goals_change（到期挪走的也在 removed 里）。"""
    try:
        ch = goals_diff(before, goals_snapshot())
    except Exception as e:                               # noqa: BLE001
        r.data["alerts"].append(f"没能比较这次日常前后的目标（{_err(e)}）")
        return
    if ch:
        r.data["goals_change"] = ch
        r.event("goals_change", **ch)


def refresh_news() -> bool:
    """官方公告缓存旧了就重拉。→ 拉了没有。不跟国服日程的服（news.applies）不拉。"""
    from maapilot.knowledge import news
    if not news.applies() or not news.stale():
        return False
    news.refresh()
    return True


def maintenance_now() -> dict | None:
    """国服公告写的维护时段里 → 那条维护；别的服（YoStar、txwy）维护时间不一样，不按它跳过日常。"""
    from maapilot.knowledge import news
    return news.maintenance_now() if news.applies() else None


def version_info() -> dict:
    """模拟器上装的游戏版本与官方最新版本（tools.game.version_info）。测试里替换掉。"""
    from maapilot.tools import game
    return game.version_info()


def _preflight(r: _Run) -> bool:
    """目标到期、公告、维护。→ True：正在维护，这次什么都不跑。"""
    try:
        moved = expire_goals()
        if moved:                                        # 接着跑（--only）时并进前几段挪走的
            r.data["goals_expired"] = [*(r.data.get("goals_expired") or []), *moved]
            r.event("goals_expired", goals=moved)
    except Exception as e:                               # noqa: BLE001
        r.data["alerts"].append(f"没能检查限时目标到期（{_err(e)}）")
    try:
        if daily.refresh_news():
            r.event("news_refreshed")
    except Exception as e:                               # noqa: BLE001 - 联网失败：用旧的缓存
        r.data["alerts"].append(f"官方公告没拉下来（{_err(e)}），按上次的缓存看维护时间")
        r.event("news_failed", error=_err(e))
    try:
        m = daily.maintenance_now()
    except Exception as e:                               # noqa: BLE001
        r.data["alerts"].append(f"看不了维护时间（{_err(e)}）")
        m = None
    if m:
        r.data["maintenance"] = m
        r.data.update(status="maintenance", ok=True)
        r.event("maintenance", **m)
        cliutil.log(f"[daily] {reportmod.maintenance_line(m)}")
        return True
    return False


def _ver(v: dict | None) -> str:
    return str((v or {}).get("version") or "?")


def _game_version(r: _Run, order: list[str]) -> None:
    """游戏客户端旧了：停下交给在场的 agent 更新（handoff.task 是 game_update），更新好它再 --only 接着跑。"""
    try:
        info = daily.version_info()
    except Exception as e:                               # noqa: BLE001
        r.data["alerts"].append(f"没查到游戏版本（{_err(e)}），照常跑")
        r.event("version_failed", error=_err(e))
        return
    info = info or {}
    r.data["game_version"] = {k: info.get(k) for k in ("installed", "latest", "update_needed")}
    r.event("game_version", **r.data["game_version"])
    if info.get("update_needed") is not True:
        return
    msg = f"游戏客户端要更新（{_ver(info.get('installed'))} → {_ver(info.get('latest'))}）"
    cliutil.log(f"[daily] {msg}")
    r.data["handoff"] = {"task": UPDATE, "error": msg, "log": None, "evidence": None, "rest": list(order),
                         "version": r.data["game_version"]}
    r.data["status"] = "handed_off"
    raise _Stop()
