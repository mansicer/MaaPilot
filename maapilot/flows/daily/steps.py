"""`daily run` 的编排：开始、各步（开始前 → MAA 项 → 识别练度 → 刷理智 / 目标部分 → award → 对账）、结束时的出错与交接。"""
from __future__ import annotations

import os
import time

from maapilot import cliutil, config, paths
from maapilot.cliutil import BAD_REQUEST, FAILED, NEEDS_USER, CliError
from maapilot.flows import goals as goalsmod
from maapilot.flows import report as reportmod
from maapilot.flows._common import journal, now
from maapilot.flows.daily.agent import _after_agent
from maapilot.flows.daily.base import (BOX, BOX_TIMEOUT_MIN, DAILY_ENV, FARM, UPDATE, _drop_marker, _err,
                                       _new_run_id, _Run, _Stop, _take_marker)
from maapilot.flows.daily.farm import farm_timeout, read_sanity_for_farm
from maapilot.flows.daily.preflight import (_expired_agent, _game_version, _goals_change, _maintenance_agent,
                                            _preflight, goals_snapshot)
from maapilot.flows.daily.prompts import goals_prompt
from maapilot.flows.daily.tasks import (_annihilation_failed, _annihilation_skip, _box_fresh, _box_from_skland,
                                        _depot_snapshot, _guard, _maa_task, depot_change)
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

def run(only: list[str] | None, trigger: str, no_agent: bool) -> dict:
    if os.environ.get(DAILY_ENV):
        raise CliError("nested_daily", f"这是日常 {os.environ[DAILY_ENV]} 叫起来的 agent，不能再跑一次日常",
                       hint="只处理交给你的那一项", exit=BAD_REQUEST)
    cfg = config.load()
    dcfg = cfg.get("daily") or {}
    tasks = [str(t) for t in (dcfg.get("tasks") or [])]
    if only:
        unknown = [t for t in only if t not in tasks and t not in (FARM, BOX)]
        if unknown:
            raise CliError("bad_arguments", f"--only 里有不在 daily.tasks 里的项：{', '.join(unknown)}",
                           hint=f"能写的：{', '.join(tasks + [BOX, FARM])}", exit=BAD_REQUEST)
    stop_file = paths.GUARD / "stop.json"
    if stop_file.exists():
        raise CliError("guard_stop", "合成玉 / 源石对账停机中，日常不开始", exit=NEEDS_USER,
                       hint="先把停机的原因告诉用户（guard status），用户说明后再处理")
    run_id = _new_run_id()
    headless = trigger == "schedule" and not no_agent
    _take_marker(run_id, trigger)
    t0 = time.monotonic()
    r = _Run(run_id, trigger)
    try:
        goals_before = goals_snapshot()                  # 最开头（到期挪走之前）：结束时比一比目标变了什么
    except Exception as e:                               # noqa: BLE001
        goals_before = None
        r.data["alerts"].append(f"没能记下日常开始时的目标（{_err(e)}）")
    code = 0
    try:
        with cliutil.device("daily run", job="daily") as lk:
            env = {**lk.child_env(), DAILY_ENV: run_id, "MAAPILOT_RUN_ID": run_id}
            with lk.keepalive():
                _steps(r, cfg, tasks, only, headless, env, lk)
    except BaseException as e:                          # 被占用、连不上、中途出错：汇报里写明没跑完
        code = e.exit if isinstance(e, CliError) else FAILED
        r.data.update(status="aborted", ok=False)
        msg = e.message if isinstance(e, CliError) else f"{type(e).__name__}: {e}"
        r.data["alerts"].append(f"日常没跑完：{msg}")
        raise
    finally:
        r.data["finished_at"] = now().isoformat(timespec="seconds")
        if goals_before is not None:
            _goals_change(r, goals_before)
        if r.data["status"] == "running":
            r.data["status"] = "done"
        r.save()
        r.event("end", status=r.data["status"], ok=r.data["ok"])
        journal("daily run", {"only": only, "trigger": trigger, "no_agent": no_agent}, bool(r.data["ok"]),
                code, time.monotonic() - t0, run_id=run_id, depot_change=r.data.get("depot_change"))
        _drop_marker()
    return _finish(r)


def _finish(r: _Run) -> dict:
    d = r.data
    d["lines"] = reportmod.summary_lines(d)          # 给 agent 转述：维护时段、到期和变动的目标……
    if d["status"] == "stopped":
        raise CliError("currency_decreased", d.get("stop_reason") or "合成玉 / 源石有没经同意的减少，日常停下了",
                       hint="把这笔减少告诉用户，等用户说明（guard status）", exit=NEEDS_USER, data=d)
    if d.get("handoff") and d["handoff"].get("task") == UPDATE:
        h = d["handoff"]
        raise CliError("handoff", f"{h['error']}，日常停在这里交给你",
                       hint="先 maapilot game update（前台跑，可能超过 10 分钟；Bash 超时就再跑一次，会接着下载），再 "
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
                       hint="看 data.tasks 里的原因；在场的话截图看看、处理后 daily run --only <项> 补跑",
                       exit=FAILED, data=d)
    return d


def _steps(r: _Run, cfg: dict, tasks: list[str], only: list[str] | None, headless: bool, env: dict, lk) -> None:
    dcfg = cfg.get("daily") or {}
    want = (lambda t: t in only) if only else (lambda t: True)
    r.event("start", trigger=r.data["trigger"], only=only, headless=headless)
    r.save()
    read_box = dcfg.get("read_box", True) is not False
    order = ([t for t in tasks if t == "startup"] + [t for t in tasks if t not in ("award", "startup")]
             + ([BOX] if read_box else []) + [FARM] + [t for t in tasks if t == "award"])
    order = [t for t in order if want(t)]

    def handoff(row: dict) -> None:
        """会话里跑（不是定时）：第一项失败就停下，把这一项和后面没跑的交给在场的 agent。"""
        if headless or row.get("ok") or row.get("code") == "busy":
            return
        t = row["task"]
        r.data["handoff"] = {"task": t, "error": row.get("error"), "log": row.get("log"),
                             "evidence": row.get("evidence"),
                             "rest": order[order.index(t):] if t in order else [t]}
        r.data["status"] = "handed_off"
        raise _Stop()

    daily._set_step("preflight")
    if _preflight(r):                                   # 官方停机维护中：不跑 MAA、不对账
        if headless:                                    # 定时运行：叫 agent 知道这件事、想想对目标的影响
            _maintenance_agent(r, cfg, env, lk)
        r.save()
        return
    try:
        _game_version(r, cfg, headless, env, lk, order)
        if "startup" in tasks and want("startup"):      # 先把游戏开到首页：读仓库（对账）要在游戏里
            handoff(_maa_task(r, cfg, "startup", ["startup"], headless, env, lk))
        daily._set_step("guard")
        _guard(r, "snapshot", env, "上次对账")
        before = _depot_snapshot()
        for t in tasks:
            if t not in ("award", "startup") and want(t):
                why = _annihilation_skip() if t == "annihilation" else None
                if why:
                    row = {"task": t, "ok": True, "skipped": why}
                    r.event("task", **row)
                    r.data["tasks"].append(row)
                    continue
                row = _maa_task(r, cfg, t, [t], headless, env, lk)
                handoff(row)
                if t == "annihilation" and not row["ok"] and not row.get("recovered") \
                        and row.get("code") not in ("offline", "busy"):
                    _annihilation_failed(r)
        if read_box and want(BOX):                     # 刷理智、目标部分之前：让它们看到今天的练度
            fresh = _box_fresh()
            if fresh:
                row = {"task": BOX, "ok": True, "skipped": fresh}
                r.event("task", **row)
                r.data["tasks"].append(row)
            elif not _box_from_skland(r, env):            # 接了森空岛就从那里读（快、不碰模拟器），不行再用 MAA 识别
                bt = float(dcfg.get("box_timeout_min") or BOX_TIMEOUT_MIN) * 60
                handoff(_maa_task(r, cfg, BOX, [], headless, env, lk, timeout_s=bt, cli=["state", "box"]))
        expired = r.data.get("goals_expired") if headless else None
        if want(FARM):
            active = goalsmod.active_goals()
            r.data["goals_active"] = len(active)
            if active and not headless:
                r.data["goals_pending"] = True           # 在场的 agent 接着做目标部分
            elif active:
                daily._set_step("goals:agent")
                r.data["agents"] += 1
                info = daily.run_agent(cfg, goals_prompt(r.data["run_id"], active, r.data["tasks"], expired),
                                       env, paths.LOGS / f"agent-{r.data['run_id']}-{r.data['agents']}-goals.jsonl")
                expired = None                           # 到期的目标它一起看过了
                _after_agent(r, env, lk)
                row = {"task": "goals", "ok": bool(info.get("ok")), "agent": info}
                r.data["tasks"].append(row)
                r.event("agent", task="goals", **{k: info.get(k) for k in ("ok", "recovered", "timed_out", "summary")})
                if not info.get("ok"):
                    r.data["alerts"].append("目标部分的 agent 没有正常结束：" + (info.get("error") or (
                        "超时被停下" if info.get("timed_out") else "看它的记录")))
                r.save()
                _guard(r, "check", env, "目标部分")
            else:
                stage = str(dcfg.get("default_stage") or "1-7")
                daily._set_step("farm:sanity")
                sanity, source = read_sanity_for_farm(env)
                ft, finfo = farm_timeout(cfg, stage, sanity, source)
                r.event("farm_timeout", **finfo)
                cliutil.log(f"[daily] 刷理智最长 {finfo['min']} 分钟（{finfo['basis']}）")
                row = _maa_task(r, cfg, FARM, ["fight", stage], headless, env, lk, timeout_s=ft)
                row["timeout"] = finfo
                handoff(row)
                r.data["farm"] = row.get("result") or {"stage": stage, "ok": False}
        if expired:                                     # 没有目标 agent 可以顺带看：单独叫一个短的
            _expired_agent(r, cfg, env, lk, expired)
        if "award" in tasks and want("award"):
            if r.data["goals_pending"]:                  # award 要在刷理智之后：交给在场的 agent 做完目标再领
                r.data["award_pending"] = True
            else:
                handoff(_maa_task(r, cfg, "award", ["award"], headless, env, lk))
        daily._set_step("guard")
        _guard(r, "check", env, "日常")
        r.data["depot_change"] = depot_change(before, _depot_snapshot())
        r.data["ok"] = not r.data["failed"]
    except _Stop:
        r.data["ok"] = False
        r.event("stopped", reason=r.data.get("stop_reason"))
