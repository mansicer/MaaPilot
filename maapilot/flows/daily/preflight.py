"""开始前：目标到期与目标快照、官方公告与停机维护、游戏版本（旧了叫 agent 更新或交给在场的 agent）。"""
from __future__ import annotations

import time

from maapilot import cliutil, paths
from maapilot.flows import goals as goalsmod
from maapilot.flows import report as reportmod
from maapilot.flows.daily.agent import _after_agent
from maapilot.flows.daily.base import EXPIRED_AGENT_MIN, MAINTENANCE_AGENT_MIN, UPDATE, _err, _Run, _Stop
from maapilot.flows.daily.prompts import _ver, expired_prompt, maintenance_prompt, update_prompt
from maapilot.flows.daily.tasks import _guard
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
    """官方公告缓存旧了就重拉。→ 拉了没有。"""
    from maapilot.knowledge import news
    if not news.stale():
        return False
    news.refresh()
    return True


def maintenance_now() -> dict | None:
    from maapilot.knowledge import news
    return news.maintenance_now()


def version_info() -> dict:
    """模拟器上装的游戏版本与官方最新版本（tools.game.version_info）。测试里替换掉。"""
    from maapilot.tools import game
    return game.version_info()


def _preflight(r: _Run) -> bool:
    """目标到期、公告、维护。→ True：正在维护，这次什么都不跑。"""
    try:
        moved = expire_goals()
        if moved:
            r.data["goals_expired"] = moved
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


def _maintenance_agent(r: _Run, cfg: dict, env: dict, lk) -> None:
    """停机维护中叫一个短的无头 agent（不碰模拟器）：它的总结写进汇报。起不来只记告警。"""
    daily._set_step("maintenance:agent")
    lk.heartbeat(step="maintenance")
    r.data["agents"] += 1
    try:
        info = daily.run_agent(cfg, maintenance_prompt(r.data["run_id"], r.data["maintenance"],
                                                       expired=r.data.get("goals_expired")), env,
                               paths.LOGS / f"agent-{r.data['run_id']}-{r.data['agents']}-maintenance.jsonl",
                               timeout_min=MAINTENANCE_AGENT_MIN)
        _after_agent(r, env, lk)
    except Exception as e:                               # noqa: BLE001
        r.data["alerts"].append(f"维护期间叫 agent 没叫起来（{_err(e)}）")
        return
    r.data["tasks"].append({"task": "maintenance", "ok": bool(info.get("ok")), "agent": info})
    r.event("agent", task="maintenance", **{k: info.get(k) for k in ("ok", "recovered", "timed_out", "summary")})
    if info.get("error"):
        r.data["alerts"].append(f"维护期间叫 agent 没叫起来：{info['error']}")


def _expired_agent(r: _Run, cfg: dict, env: dict, lk, expired: list) -> None:
    """开头有限时目标到期、又没有目标部分的 agent：叫一个短的 agent 看一眼还有没有没做完的，总结写进汇报。"""
    daily._set_step("goals_expired:agent")
    lk.heartbeat(step="goals_expired")
    r.data["agents"] += 1
    info = daily.run_agent(cfg, expired_prompt(r.data["run_id"], expired, r.data["tasks"]), env,
                           paths.LOGS / f"agent-{r.data['run_id']}-{r.data['agents']}-goals.jsonl",
                           timeout_min=EXPIRED_AGENT_MIN)
    _after_agent(r, env, lk)
    r.data["tasks"].append({"task": "goals", "ok": bool(info.get("ok")), "agent": info, "expired_only": True})
    r.event("agent", task="goals_expired", **{k: info.get(k) for k in ("ok", "recovered", "timed_out", "summary")})
    if info.get("error"):
        r.data["alerts"].append(f"叫 agent 看到期的目标没叫起来：{info['error']}")
    r.save()
    _guard(r, "check", env, "看到期的目标")


def _game_version(r: _Run, cfg: dict, headless: bool, env: dict, lk, order: list[str]) -> None:
    """游戏客户端旧了：定时运行当场叫 agent 更新（没更新成整个日常停下）；会话里交给在场的 agent。"""
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
    if not headless:
        r.data["handoff"] = {"task": UPDATE, "error": msg, "log": None, "evidence": None, "rest": list(order),
                             "version": r.data["game_version"]}
        r.data["status"] = "handed_off"
        raise _Stop()
    daily._set_step(f"{UPDATE}:agent")
    lk.heartbeat(step=UPDATE)
    r.data["agents"] += 1
    t0 = time.monotonic()
    agent = daily.run_agent(cfg, update_prompt(r.data["run_id"], info), env,
                            paths.LOGS / f"agent-{r.data['run_id']}-{r.data['agents']}-{UPDATE}.jsonl", recovery=True)
    _after_agent(r, env, lk)
    ok = bool(agent.get("recovered"))
    if ok:                                               # 它说更新好了：再查一次版本核实（查不了就信它）
        try:
            again = daily.version_info() or {}
            if again.get("update_needed") is True:
                ok = False
                r.data["alerts"].append("agent 说更新好了，但再查版本还是旧的")
            r.data["game_version"] = {k: again.get(k) for k in ("installed", "latest", "update_needed")}
        except Exception as e:                           # noqa: BLE001
            r.event("version_failed", error=_err(e), after=UPDATE)
    row = {"task": UPDATE, "ok": ok, "duration_s": round(time.monotonic() - t0, 1), "agent": agent,
           "version": {"from": _ver(info.get("installed")), "to": _ver(info.get("latest"))}}
    if not ok:
        row["error"] = msg + "，没更新成"
    r.data["tasks"].append(row)
    r.event("agent", task=UPDATE, **{k: agent.get(k) for k in ("ok", "recovered", "timed_out", "summary")})
    r.save()
    _guard(r, "check", env, "agent 更新游戏")
    if not ok:                                           # 旧客户端上什么都跑不了
        r.data["failed"].append(UPDATE)
        r.data["status"] = "update_failed"
        raise _Stop()
