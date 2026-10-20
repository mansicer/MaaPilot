"""日常项：对账（guard）、仓库差、识别干员练度（森空岛 / MAA）、跑一个 MAA 项、剿灭。"""
from __future__ import annotations

import json
import time

from maapilot import cliutil, paths
from maapilot.cliutil import NEEDS_USER
from maapilot.flows import report as reportmod
from maapilot.flows.daily.calls import _stop_leftover_maa
from maapilot.flows.daily.base import BOX, FARM, TASK_TIMEOUT_MIN, _Run, _Stop
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

# --- 仓库差 ----------------------------------------------------------------------------------------------------------

def _depot_snapshot() -> dict | None:
    try:
        d = json.loads((paths.STATE / "depot.json").read_text(encoding="utf-8"))
        return d if isinstance(d.get("items"), dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def depot_change(before: dict | None, after: dict | None) -> list[dict] | None:
    """两份仓库快照的差（按变化量从大到小的绝对值排）；少一份或是同一份 → None。"""
    if not before or not after or before.get("at") == after.get("at"):
        return None
    try:
        from maapilot.knowledge.items import display_name
    except Exception:                                    # noqa: BLE001
        display_name = str
    a, b = before["items"], after["items"]
    out = []
    for k in sorted(set(a) | set(b)):
        delta = int(b.get(k, 0)) - int(a.get(k, 0))
        if delta:
            try:
                name = display_name(str(k))
            except Exception:                            # noqa: BLE001 - 没有数据表时用 id
                name = str(k)
            out.append({"id": str(k), "name": name, "delta": delta})
    return sorted(out, key=lambda c: -abs(c["delta"]))


# --- 对账 ------------------------------------------------------------------------------------------------------------

def _guard(r: _Run, which: str, env: dict, after: str) -> None:
    """guard snapshot / check。check 不过 → 记下并 _Stop；命令本身没做成 → 告警、记 guard_unreadable（算问题，
    要告诉用户），接着跑。之后有一次 check 做成了（以上一次的记录为准对上了账）就去掉 guard_unreadable。"""
    code, env_ = daily.maapilot_call(["guard", which], env, timeout_s=15 * 60)
    r.event(f"guard_{which}", after=after, exit=code, error=env_.get("error"))
    if which == "snapshot" and code not in (0, NEEDS_USER):   # 读仓库没成（10-12 04:40：换日后首页多半有弹窗）：
        daily.maapilot_call(["maa", "startup"], env, timeout_s=10 * 60)  # 回首页、关掉弹窗，再读一次
        code, env_ = daily.maapilot_call(["guard", which], env, timeout_s=15 * 60)
        r.event(f"guard_{which}", after=after, exit=code, error=env_.get("error"), retry=True)
    if which == "snapshot" and code != NEEDS_USER:       # 退出码 3：上次之后有人动过，改做的对账没过
        if code != 0:
            r.data["guard_unreadable"] = True
            r.data["alerts"].append(f"开始前没能记下合成玉、源石（{(env_.get('error') or {}).get('message')}），"
                                    "这次对账以上一次的记录为准")
        return
    if code in (0, NEEDS_USER):
        r.data.pop("guard_unreadable", None)
    if code == NEEDS_USER:
        g = env_.get("data") or {}
        r.data["guard"] = {"ok": False, "after": after, **{k: g.get(k) for k in ("before", "after", "unexplained")}}
        r.data["stop_reason"] = f"{after}之后发现合成玉 / 源石有没经同意的减少"
        r.data["status"] = "stopped"
        raise _Stop()
    if code == 0:
        r.data["guard"] = {"ok": True}
    else:
        r.data["guard"] = {"ok": None, "error": (env_.get("error") or {}).get("message")}
        r.data["guard_unreadable"] = True


def _box_fresh() -> str | None:
    """box.json 已经是这个游戏日（04:00 换日）读的 → 跳过的原因；否则 None。"""
    from maapilot.knowledge import calendar
    from maapilot.tools import common
    snap = common.read_json(paths.STATE / "box.json") or {}
    at = common.parse_iso(snap.get("at"))
    if at is not None and calendar.game_day(at) == calendar.game_day(daily.now()):
        return f"今天（游戏日）已经读过（{at:%H:%M}）"
    return None


SKLAND_TIMEOUT_S = 180


def _box_from_skland(r: _Run, env: dict) -> bool:
    """接了森空岛（private/skland.json）就 `state skland` 读练度。成了记一行、返回 True；
    没成记一条告警（不算失败），返回 False，由 MAA 识别接着做。"""
    from maapilot.tools import skland_cmd
    if not skland_cmd.configured():
        return False
    daily._set_step("box:skland")
    t0 = time.monotonic()
    code, env_ = daily.maapilot_call(["state", "skland"], env, timeout_s=SKLAND_TIMEOUT_S)
    if code == 0:
        d = env_.get("data") or {}
        row = {"task": BOX, "ok": True, "source": "skland", "duration_s": round(time.monotonic() - t0, 1),
               "result": {k: d.get(k) for k in ("operators", "with_modules", "mastered")}}
        r.event("task", **row)
        r.data["tasks"].append(row)
        r.save()
        return True
    err = env_.get("error") or {}
    r.data["alerts"].append(f"森空岛读练度没成（{err.get('message') or err.get('code') or f'退出码 {code}'}），"
                            "改用 MAA 识别" + ("；token 失效了，要用户重新运行 maapilot skland token"
                                              if err.get("code") == "token_invalid" else ""))
    r.event("skland_failed", code=err.get("code"), message=err.get("message"))
    return False


# --- 跑一个日常项 ----------------------------------------------------------------------------------------------------

def _maa_task(r: _Run, cfg: dict, task: str, args: list[str], env: dict, lk,
              timeout_s: float | None = None, cli: list[str] | None = None) -> dict:
    """跑一个日常项：默认 `maapilot maa <args>`；cli 给了就跑那条 maapilot 命令（识别干员练度：state box）。"""
    cli = cli or ["maa", *args]
    if timeout_s is None:
        timeout_s = float((cfg.get("daily") or {}).get("task_timeout_min") or TASK_TIMEOUT_MIN) * 60
    daily._set_step(task)
    lk.heartbeat(step=task)
    cliutil.log(f"[daily] {reportmod.label(task)} …")
    t0 = time.monotonic()
    code, env_ = daily.maapilot_call(cli, env, timeout_s=timeout_s)
    err = env_.get("error") or {}
    row = {"task": task, "ok": code == 0, "duration_s": round(time.monotonic() - t0, 1)}
    data = env_.get("data") if isinstance(env_.get("data"), dict) else None
    if code == 0:
        if task == FARM:
            row["result"] = {k: (data or {}).get(k) for k in ("stage", "times_done", "sanity_used", "medicine_used",
                                                               "drops", "proxy_failed")}
    else:
        row.update(error=err.get("message") or err.get("code") or f"退出码 {code}", code=err.get("code"),
                   log=(data or {}).get("log"))
        if (data or {}).get("evidence"):
            row["evidence"] = data["evidence"]
    r.event("task", **row)
    r.data["tasks"].append(row)
    if err.get("code") == "timeout":                  # 杀了 maapilot 命令；它会带走 maa，万一没带走，这里补一刀
        _stop_leftover_maa(r, daily.TIMEOUT_LEFTOVER_WAIT_S, f"{reportmod.label(task)}超时停下后")
    if code != 0:
        cliutil.log(f"[daily] {reportmod.label(task)} 没做成：{row['error']}")
        r.data["failed"].append(task)
    r.save()
    return row


# --- 剿灭 ------------------------------------------------------------------------------------------------------------

def _annihilation_skip() -> str | None:
    """这周不用 / 不该再打剿灭的原因；该打 → None。"""
    try:
        from maapilot.tools.maa_cmd import annihilation_week
        w = annihilation_week()
    except Exception:
        return None
    if not w.get("capped"):
        try:                                            # 接了森空岛就问一下游戏里的本周进度（自己累加的数会漏）
            from maapilot.tools.skland_cmd import annihilation_progress
            if annihilation_progress() is not None:
                w = annihilation_week()
        except Exception:                               # noqa: BLE001 - 读不到就按自己的记录
            pass
    if w.get("capped"):
        p = w.get("progress") or {}
        return "本周剿灭的合成玉已经拿满" + (f"（{p['current']}/{p['total']}）" if p.get("total") else "")
    return None
