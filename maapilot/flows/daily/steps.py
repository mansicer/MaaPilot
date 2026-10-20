"""`daily run` 的编排：开始（新开一次，或接着没收尾的那次）、各步（开始前 → MAA 项 → 识别练度 → 刷理智 / 交出目标部分 → award → 对账）、
结束时的出错与交接。agent 一直在场：要判断的事（某项失败、游戏要更新、有进行中的目标）都停下交回它。"""
from __future__ import annotations

import time

from maapilot import cliutil, config, paths
from maapilot.cliutil import BAD_REQUEST, BUSY, FAILED, NEEDS_USER, CliError
from maapilot.flows import goals as goalsmod
from maapilot.flows import report as reportmod
from maapilot.flows._common import journal, now
from maapilot.flows.daily.base import (BOX, BOX_TIMEOUT_MIN, FARM, UPDATE, _drop_marker, _err, _new_run_id, _Run,
                                       _Stop, _take_marker, _write_open, open_brief)
from maapilot.flows.daily.close import close_run
from maapilot.flows.daily.farm import farm_timeout, read_sanity_for_farm
from maapilot.flows.daily.preflight import _game_version, _goals_change, _preflight, goals_snapshot
from maapilot.flows.daily.tasks import (_annihilation_skip, _box_fresh, _box_from_skland,
                                        _depot_snapshot, _guard, _maa_task, depot_change)
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

STALE_ALERT = "这次日常开始后超过 {h} 小时 agent 都没有收尾（daily done），下次日常开始时程序自动收尾了"


def run(only: list[str] | None, trigger: str) -> dict:
    """跑一次日常。有没收尾的日常时：带 --only 接着写它（同一个运行编号、同一份汇报），不带 --only 拒绝；
    超过 OPEN_STALE_H 小时没收尾的算 agent 放弃了，先自动收尾再开新的。"""
    cfg = config.load()
    dcfg = cfg.get("daily") or {}
    tasks = [str(t) for t in (dcfg.get("tasks") or [])]
    if only:
        unknown = [t for t in only if t not in tasks and t not in (FARM, BOX)]
        if unknown:
            raise CliError("bad_arguments", f"--only 里有不在 daily.tasks 里的项：{', '.join(unknown)}",
                           hint=f"能写的：{', '.join(tasks + [BOX, FARM])}", exit=BAD_REQUEST)
    if (paths.GUARD / "stop.json").exists():
        raise CliError("guard_stop", "合成玉 / 源石对账停机中，日常不开始", exit=NEEDS_USER,
                       hint="先把停机的原因告诉用户（guard status），用户说明后再处理")
    cur = daily.running()
    if cur:
        raise CliError("daily_running", f"日常 {cur.get('run_id')} 正在跑（{cur.get('started_at', '')[:16]} 开始）",
                       hint="等它跑完；daily status 看进度", exit=BUSY, data={"running": cur})
    op = daily.open_daily()
    if op and op.get("stale"):
        from maapilot.flows.daily.base import OPEN_STALE_H
        close_run(op["run_id"], None, alert=STALE_ALERT.format(h=OPEN_STALE_H), by="auto")
        op = None
    if op and not only:
        raise CliError("daily_open", f"日常 {op['run_id']}（{str(op.get('started_at') or '')[:16]} 开始）还没收尾",
                       exit=BAD_REQUEST, data={"open": open_brief(op)},
                       hint="做完目标部分后 maapilot daily done --summary \"…\" 收尾；补跑没做成的项用 "
                            "maapilot daily run --only <项>（接着写同一份汇报）")
    run_id = op["run_id"] if op else _new_run_id()
    _take_marker(run_id, trigger)
    try:
        r = _Run(run_id, trigger)
        if op:
            _resume(r, op, only, trigger)
        else:
            op = {"run_id": run_id, "trigger": trigger, "started_at": r.data["started_at"]}
            try:
                op["goals_before"] = goals_snapshot()    # 最开头（到期挪走之前）：结束时比一比目标变了什么
            except Exception as e:                       # noqa: BLE001
                op["goals_before"] = None
                r.data["alerts"].append(f"没能记下日常开始时的目标（{_err(e)}）")
            _write_open(op)
    except BaseException:
        _drop_marker()
        raise
    return _run(r, op, cfg, tasks, only, trigger)


def _resume(r: _Run, op: dict, only: list[str] | None, trigger: str) -> None:
    """接着没收尾的那次：读回它的汇报；这次要重跑的项从 failed 里拿掉（再失败会再记上），之前没做成的那行标成 retried。"""
    try:
        old = reportmod.load(op["run_id"])
    except CliError as e:
        r.data["alerts"].append(f"没收尾的日常 {op['run_id']} 的汇报读不了（{e.message}），从头记")
        old = None
    if old:
        old.pop("lines", None)
        old.pop("next", None)
        old.pop("handoff", None)
        r.data = {**r.data, **old}
    redo = set(only or [])
    r.data.update(status="running", ok=None, finished_at=None, open=True)
    r.data["failed"] = [t for t in r.data.get("failed") or [] if t not in redo]
    for t in r.data.get("tasks") or []:
        if t.get("task") in redo and not t.get("ok"):
            t["retried"] = True
    r.data["segments"] = int(r.data.get("segments") or 1) + 1
    r.event("resume", trigger=trigger, only=only, segment=r.data["segments"])


def _run(r: _Run, op: dict, cfg: dict, tasks: list[str], only: list[str] | None, trigger: str) -> dict:
    run_id = r.data["run_id"]
    t0 = time.monotonic()
    code = 0
    started = False
    try:
        with cliutil.device("daily run", job="daily") as lk:
            started = True
            env = {**lk.child_env(), "MAAPILOT_RUN_ID": run_id}
            with lk.keepalive():
                _steps(r, op, cfg, tasks, only, env, lk)
    except BaseException as e:                          # 被占用、连不上、中途出错：汇报里写明没跑完
        code = e.exit if isinstance(e, CliError) else FAILED
        r.data.update(status="aborted", ok=False)
        r.data["alerts"].append(f"日常没跑完：{_err(e)}")
        if not started and r.data.get("segments") is None:   # 第一段就没拿到模拟器：什么都没做，不留"没收尾"
            r.data["open"] = False
            daily._clear_open(run_id)
        raise
    finally:
        r.data["finished_at"] = now().isoformat(timespec="seconds")
        if op.get("goals_before") is not None:
            _goals_change(r, op["goals_before"])
        if r.data["status"] == "running":
            r.data["status"] = "done"
        if op.get("depot_before"):
            ch = depot_change(op["depot_before"], _depot_snapshot())
            if ch is not None:
                r.data["depot_change"] = ch
        r.data["news"] = _news()
        r.data["lines"] = reportmod.summary_lines(r.data)   # 给 agent 转述：维护时段、到期和变动的目标……
        if r.data.get("open"):
            r.data["next"] = next_hint(r.data)
        r.save()
        r.event("end", status=r.data["status"], ok=r.data["ok"])
        journal("daily run", {"only": only, "trigger": trigger}, bool(r.data["ok"]),
                code, time.monotonic() - t0, run_id=run_id, depot_change=r.data.get("depot_change"))
        _drop_marker()
    return _finish(r)


def _news() -> dict | None:
    """官方日程（开头拉过的缓存）：正在 / 快要维护、活动，和还没告诉用户的时间点（due，说完 news told）。没有 → None。"""
    try:
        from maapilot.tools import doctor
        return doctor.news_brief()
    except Exception:                                    # noqa: BLE001 - 读不了不影响日常
        return None


def next_hint(d: dict) -> str:
    """还没收尾时给 agent 的下一步（只是程序的提示，做不做由 agent 判断）。"""
    tail = "最后 maapilot daily done --summary \"<做了什么、目标进度、花了什么、要用户决定的事>\" 收尾，再告诉用户"
    h = d.get("handoff")
    if d.get("status") == "stopped":
        return "合成玉 / 源石对账停下了：先告诉用户（maapilot-recovery「货币对账停机」）；" + tail
    if h and h.get("task") == UPDATE:
        return ("游戏要更新：按 maapilot-events 更新好、处理完第一次启动，再 maapilot daily run --only "
                + ",".join(h.get("rest") or []) + " 接着跑（同一份汇报）；" + tail)
    if h:
        return (f"按 maapilot-recovery 处理{reportmod.label(h.get('task') or '?')}，好了再 maapilot daily run --only "
                + ",".join(h.get("rest") or []) + " 接着跑（同一份汇报）；" + tail)
    if d.get("goals_pending"):
        more = "、maapilot maa award" if d.get("award_pending") else ""
        return f"按 maapilot-daily 做目标部分（按目标优先级），然后{more.lstrip('、')}{'、' if more else ''}maapilot guard check；" + tail
    if d.get("failed"):
        return "有没做成的项：处理后 maapilot daily run --only " + ",".join(d["failed"]) + " 补跑（同一份汇报）；" + tail
    return tail


def _finish(r: _Run) -> dict:
    d = r.data
    if d["status"] == "stopped":
        raise CliError("currency_decreased", d.get("stop_reason") or "合成玉 / 源石有没经同意的减少，日常停下了",
                       hint="把这笔减少告诉用户，等用户说明（guard status）；再 daily done 收尾", exit=NEEDS_USER, data=d)
    if d.get("handoff") and d["handoff"].get("task") == UPDATE:
        h = d["handoff"]
        raise CliError("handoff", f"{h['error']}，日常停在这里交给你",
                       hint="先 maapilot game update（可能超过 10 分钟；命令超时被停下就再跑一次，会接着下载），再 "
                            "maapilot maa startup，按 maapilot-recovery 处理第一次启动的下载提示和公告；用户协议 / 隐私政策"
                            "更新要先问用户。更新好再 maapilot daily run --only " + ",".join(h["rest"]) + " 接着跑",
                       exit=FAILED, data=d)
    if d.get("handoff"):
        h = d["handoff"]
        raise CliError("handoff", f"{reportmod.label(h['task'])}没做成，日常停在这里交给你：{h['error']}",
                       hint=f"按 maapilot-recovery 先看 handoff.evidence 的截图、maapilot maa why 看经过，处理好再 "
                            f"maapilot daily run --only {','.join(h['rest'])} 接着跑",
                       exit=FAILED, data=d)
    if d["failed"]:
        raise CliError("daily_incomplete", f"有 {len(d['failed'])} 项没做成：{'、'.join(map(reportmod.label, d['failed']))}",
                       hint="看 data.tasks 里的原因；截图看看、处理后 daily run --only <项> 补跑",
                       exit=FAILED, data=d)
    return d


def _steps(r: _Run, op: dict, cfg: dict, tasks: list[str], only: list[str] | None, env: dict, lk) -> None:
    dcfg = cfg.get("daily") or {}
    want = (lambda t: t in only) if only else (lambda t: True)
    r.event("start", trigger=r.data["trigger"], only=only)
    r.save()
    read_box = dcfg.get("read_box", True) is not False
    order = ([t for t in tasks if t == "startup"] + [t for t in tasks if t not in ("award", "startup")]
             + ([BOX] if read_box else []) + [FARM] + [t for t in tasks if t == "award"])
    order = [t for t in order if want(t)]

    def handoff(row: dict) -> None:
        """第一项失败就停下，把这一项和后面没跑的交给在场的 agent。"""
        if row.get("ok") or row.get("code") == "busy":
            return
        t = row["task"]
        r.data["handoff"] = {"task": t, "error": row.get("error"), "log": row.get("log"),
                             "evidence": row.get("evidence"),
                             "rest": order[order.index(t):] if t in order else [t]}
        r.data["status"] = "handed_off"
        raise _Stop()

    daily._set_step("preflight")
    if _preflight(r):                                   # 官方停机维护中：不跑 MAA、不对账
        r.save()
        return
    try:
        _game_version(r, order)
        if "startup" in tasks and want("startup"):      # 先把游戏开到首页：读仓库（对账）要在游戏里
            handoff(_maa_task(r, cfg, "startup", ["startup"], env, lk))
        daily._set_step("guard")
        _guard(r, "snapshot", env, "上次对账")
        if not op.get("depot_before"):                  # 这次日常第一份仓库：接着跑的几段都和它比
            snap = _depot_snapshot()
            if snap:
                op["depot_before"] = snap
                _write_open(op)
        for t in tasks:
            if t not in ("award", "startup") and want(t):
                why = _annihilation_skip() if t == "annihilation" else None
                if why:
                    row = {"task": t, "ok": True, "skipped": why}
                    r.event("task", **row)
                    r.data["tasks"].append(row)
                    continue
                row = _maa_task(r, cfg, t, [t], env, lk)
                handoff(row)                            # 剿灭没打成也一样交回：原因（不能代理、找不到入口、掉线）由 agent 判断
        if read_box and want(BOX):                     # 刷理智、目标部分之前：让它们看到今天的练度
            fresh = _box_fresh()
            if fresh:
                row = {"task": BOX, "ok": True, "skipped": fresh}
                r.event("task", **row)
                r.data["tasks"].append(row)
            elif not _box_from_skland(r, env):            # 接了森空岛就从那里读（快、不碰模拟器），不行再用 MAA 识别
                bt = float(dcfg.get("box_timeout_min") or BOX_TIMEOUT_MIN) * 60
                handoff(_maa_task(r, cfg, BOX, [], env, lk, timeout_s=bt, cli=["state", "box"]))
        if want(FARM):
            active = goalsmod.active_goals()
            r.data["goals_active"] = len(active)
            r.data["goals_pending"] = bool(active)      # 有目标：刷理智交给在场的 agent 做目标部分
            if not active:
                stage = str(dcfg.get("default_stage") or "1-7")
                daily._set_step("farm:sanity")
                sanity, source = read_sanity_for_farm(env)
                ft, finfo = farm_timeout(cfg, stage, sanity, source)
                r.event("farm_timeout", **finfo)
                cliutil.log(f"[daily] 刷理智最长 {finfo['min']} 分钟（{finfo['basis']}）")
                row = _maa_task(r, cfg, FARM, ["fight", stage], env, lk, timeout_s=ft)
                row["timeout"] = finfo
                handoff(row)
                r.data["farm"] = row.get("result") or {"stage": stage, "ok": False}
        if "award" in tasks and want("award"):
            if r.data.get("goals_pending"):              # award 要在刷理智之后：交给在场的 agent 做完目标再领
                r.data["award_pending"] = True
            else:
                r.data["award_pending"] = False
                handoff(_maa_task(r, cfg, "award", ["award"], env, lk))
        daily._set_step("guard")
        _guard(r, "check", env, "日常")
        r.data["ok"] = not r.data["failed"]
    except _Stop:
        r.data["ok"] = False
        r.event("stopped", reason=r.data.get("stop_reason"))
