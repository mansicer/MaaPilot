"""游戏时间的唯一口径（见 docs/concepts/knowledge.md）：官服游戏日按 UTC+8 04:00 换日，游戏周从周一 04:00 开始。
所有需要"今天是周几 / 本周从哪天算起 / 下次重置在什么时候"的模块都从这里取，不自己算。
naive datetime 一律按 GAME_TZ 解释；返回的 datetime 全部带时区。不碰模拟器。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

GAME_TZ = timezone(timedelta(hours=8), name="UTC+8")
DAY_START = time(4, 0)
_DAY_OFFSET = timedelta(hours=DAY_START.hour, minutes=DAY_START.minute)


def to_game_tz(at: datetime | None = None) -> datetime:
    """把 `at`（None = 现在）转成 GAME_TZ 的 aware datetime；naive 视为已经是游戏时区。"""
    if at is None:
        return datetime.now(GAME_TZ)
    if at.tzinfo is None:
        return at.replace(tzinfo=GAME_TZ)
    return at.astimezone(GAME_TZ)


def game_day(at: datetime | None = None) -> date:
    """游戏日：04:00 之前算前一天。"""
    return (to_game_tz(at) - _DAY_OFFSET).date()


def game_weekday(at: datetime | None = None) -> int:
    """游戏日的 ISO 星期（1=周一 … 7=周日）。"""
    return game_day(at).isoweekday()


def daily_reset_at(at: datetime | None = None) -> datetime:
    """`at` 之后的下一次每日重置（严格晚于 `at`；恰好在 04:00 时返回次日 04:00）。"""
    d = game_day(at)
    return datetime.combine(d + timedelta(days=1), DAY_START, tzinfo=GAME_TZ)


def week_start(at: datetime | None = None) -> datetime:
    """`at` 所在游戏周的起点：周一 04:00。"""
    d = game_day(at)
    monday = d - timedelta(days=d.isoweekday() - 1)
    return datetime.combine(monday, DAY_START, tzinfo=GAME_TZ)


def weekly_reset_at(at: datetime | None = None) -> datetime:
    """`at` 之后的下一次每周重置：下周一 04:00（严格晚于 `at`）。"""
    return week_start(at) + timedelta(days=7)


PERIODS = ("week", "day", "month")


def period_start(period: str, at: datetime | None = None) -> datetime:
    """`at` 所在周期的起点（按游戏日，04:00 为界）：week → 周一 04:00，day → 当天 04:00，month → 1 号 04:00。
    未知周期抛 ValueError。"""
    if period == "week":
        return week_start(at)
    d = game_day(at)
    if period == "day":
        return datetime.combine(d, DAY_START, tzinfo=GAME_TZ)
    if period == "month":
        return datetime.combine(d.replace(day=1), DAY_START, tzinfo=GAME_TZ)
    raise ValueError(f"未知周期: {period!r}（{' / '.join(PERIODS)}）")
