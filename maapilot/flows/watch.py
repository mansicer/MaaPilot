"""`maapilot daily watch`：等下一次日常（定时或手动）跑完，或者官方日程到点（维护结束、活动开始），交给会话里的 agent 去告诉用户。

只读：不拿锁、不碰模拟器。看的是汇报 private/reports/*.json（`daily run` 每一步都会更新）和运行标记
private/daily-run.json（`daily.running()`）。命令开始时正在跑的那次也算"下一次"：等它跑完。
开始时已经有 UNSEEN_H 小时内跑完、还没 `report seen` 的（上一个 watch 结束到这一个挂上之间跑完的）→ 直接交出来。
会话里的 agent 一直在后台挂着一个（AGENTS.md「盯着日常」），结束就告诉用户、`report seen`、再挂一个。
契约见 docs/cli.md「daily watch」。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from maapilot.cliutil import FAILED, CliError
from maapilot.flows import report as reportmod
from maapilot.flows._common import now

POLL_S = 20
# Claude Code 的后台 Bash 最长 2 小时：默认等 110 分钟就正常结束（kind: timeout），agent 什么都不说、再挂一个
DEFAULT_TIMEOUT_MIN = 110
UNSEEN_H = 24                   # 开始时补交多久以内跑完、没告诉过用户的日常
NEWS_RETRY_S = 600              # 公告拉不下来（断网）之后隔多久再拉：别每次轮询都去连


def _ts(iso: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


def _finished_since(start: datetime, watching: set[str]) -> dict | None:
    """开始等之后跑完的那次（最早跑完的）；正在等的那次进程没了而汇报还写着 running → 当没跑完（aborted）。"""
    from maapilot.flows import daily
    live = daily.running()
    found = []
    for rid in reportmod.runs()[:10]:
        try:
            d = reportmod.load(rid)
        except CliError:
            continue
        st = d.get("status")
        if st == "running":
            if rid in watching and not (live and live.get("run_id") == rid):
                found.append((now(), {**d, "status": "aborted", "ok": False,
                                      "alerts": (d.get("alerts") or []) + ["日常的进程不在了，汇报停在“还在跑”"]}))
            continue
        fin = _ts(d.get("finished_at"))
        if fin is not None and fin >= start:
            found.append((fin, d))
    return min(found, key=lambda x: x[0])[1] if found else None


def _unseen_before(start: datetime) -> dict | None:
    """开始等之前 UNSEEN_H 小时内跑完、还没告诉过用户的（最早跑完的那次）。"""
    seen = reportmod.seen_runs()
    found = []
    for rid in reportmod.runs()[:10]:
        if rid in seen:
            continue
        try:
            d = reportmod.load(rid)
        except CliError:
            continue
        fin = _ts(d.get("finished_at"))
        if d.get("status") != "running" and fin is not None and start - timedelta(hours=UNSEEN_H) <= fin < start:
            found.append((fin, d))
    return min(found, key=lambda x: x[0])[1] if found else None


def watch(timeout_min: float | None = None, *, poll_s: float = POLL_S, sleep=time.sleep) -> dict:
    from maapilot.flows import daily
    start = now() - timedelta(seconds=1)
    deadline = start + timedelta(minutes=float(DEFAULT_TIMEOUT_MIN if timeout_min is None else timeout_min))
    watching: set[str] = set()
    d = _unseen_before(start)
    if d is not None:
        return _result(d)
    news_state: dict = {}
    while True:
        live = daily.running()
        if live and live.get("run_id"):
            watching.add(str(live["run_id"]))
        d = _finished_since(start, watching)
        if d is not None:
            return _result(d)
        n = _news_due(news_state)
        if n is not None:
            return n
        if now() >= deadline:
            # 等满了没有日常跑完：正常结束（不是出错），agent 什么都不说、再挂一个（seen: true 也是"不用说"）
            return {"kind": "timeout", "waited_min": int((deadline - start).total_seconds() // 60),
                    "running": live, "next_at": _next_at(), "seen": True}
        sleep(max(0.5, min(poll_s, (deadline - now()).total_seconds())))


def _next_at() -> str | None:
    from maapilot.flows import daily
    try:
        sched = daily.schedule_info()
        return daily._next_at(sched["times"]) if sched.get("installed") else None
    except Exception:                                  # noqa: BLE001 - 读不了 crontab 不影响
        return None


def _news_due(state: dict | None = None) -> dict | None:
    """官方日程里到点（维护结束、活动开始）、还没告诉过用户、也还没交给过 watch 的 → 结束等待，让 agent 去问用户（maapilot-events）。
    交出去的记成 handed：agent 没 `news told` 就重挂也不会立刻又醒（启动检查照样列着）。
    缓存旧了顺手拉一次（只联网，不碰模拟器）；拉不下来 NEWS_RETRY_S 秒内不再拉。只有国服日程（news.applies）；出错当没有。"""
    state = {} if state is None else state
    try:
        from maapilot.knowledge import news
        if not news.applies():
            return None
        d = news.load()
        failed = state.get("failed_at")
        if d.get("items") and news.stale(d) and (failed is None or time.monotonic() - failed >= NEWS_RETRY_S):
            try:                                       # 没缓存（还没跑过日常）就不拉：日常开头会拉
                d = news.refresh()
                state.pop("failed_at", None)
            except Exception:                          # noqa: BLE001 - 拉不到就用缓存
                state["failed_at"] = time.monotonic()
        due = news.unhanded_due(d)
        if due:
            news.mark_handed([e["key"] for e in due])
    except Exception:                                  # noqa: BLE001
        return None
    if not due:
        return None
    return {"kind": "news", "due": due, "lines": news.lines(d), "seen": False}


def _result(d: dict) -> dict:
    rid = d.get("run_id")
    out = {"kind": "daily", **reportmod.problem_brief(d), "lines": reportmod.summary_lines(d),
           "seen": rid in reportmod.seen_runs()}
    if reportmod.has_problem(d):
        raise CliError("daily_problem", out["lines"][0], exit=FAILED, data=out,
                       hint="马上告诉用户出了什么事（maapilot-recovery / maapilot-daily），然后 report seen，再挂一个 daily watch")
    return out
