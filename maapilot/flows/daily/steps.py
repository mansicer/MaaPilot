"""`daily run` 的编排：开始、各步（开始前 → MAA 项 → 识别练度 → 刷理智 / 目标部分 → award → 对账）、结束时的出错与交接。"""
from __future__ import annotations

import os
import re
import time

from maapilot import cliutil, config, hosts, paths
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
    """跑一次日常。结束（跑完、出错、开头就被拦下）时把结果推进用户开着的 Codex 会话（见 session_message）：
    定时运行一律推；Codex 会话里手动跑的（命令带 CODEX_THREAD_ID）也推：Codex 的后台命令结束叫不醒模型。
    定时运行开头就被拦下（还没有汇报）时补一份 status 为 refused 的汇报，Claude Code 里挂着的 daily watch 才看得到。"""
    if os.environ.get(DAILY_ENV):
        return _run(only, trigger, no_agent)
    scheduled = trigger == "schedule"
    in_codex = not scheduled and bool(os.environ.get("CODEX_THREAD_ID"))
    if not scheduled and not in_codex:
        return _run(only, trigger, no_agent)
    try:
        d = _run(only, trigger, no_agent)
    except BaseException as e:                          # noqa: BLE001 - 推完照样抛出去
        rid = _run_id_of(e)
        code = e.code if isinstance(e, CliError) else "crashed"
        if rid is None and scheduled and isinstance(e, Exception):
            rid = _refused_report(trigger, e)
        if scheduled or rid:                            # 会话里手动跑、开头就被拦下：命令马上就回来了，不用推
            _push(rid, trigger, code)
        raise
    _push(d.get("run_id"), trigger, None)
    return d


def _run_id_of(e: BaseException) -> str | None:
    """异常带着的运行编号：_finish 抛的（data 是整份记录），或 _run 中途出错时挂上的 daily_run_id。"""
    data = getattr(e, "data", None)
    if isinstance(data, dict) and data.get("run_id"):
        return str(data["run_id"])
    rid = getattr(e, "daily_run_id", None)
    return str(rid) if rid else None


def _refused_report(trigger: str, e: BaseException) -> str | None:
    """开头就被拦下（对账停机、设置写错、另一个日常在跑……）：写一份最小的汇报（status refused）。→ 运行编号或 None。"""
    try:
        rid = _new_run_id()
        r = _Run(rid, trigger)
        code = e.code if isinstance(e, CliError) else "crashed"
        r.data.update(status="refused", ok=False, finished_at=now().isoformat(timespec="seconds"),
                      error={"code": code, "message": _err(e), "hint": getattr(e, "hint", None)})
        r.data["alerts"].append(f"日常没开始：{_err(e)}")
        r.save()
        r.event("end", status="refused", ok=False, code=code)
        return rid if reportmod.json_path(rid).exists() else None
    except Exception as e2:                             # noqa: BLE001 - 写不了也不影响推消息
        cliutil.log(f"[daily] 没开始的汇报写不了：{_err(e2)}")
        return None


def _push(rid: str | None, trigger: str, code: str | None) -> None:
    """推消息；推不过去（而用户最近的会话是 Codex 的）就记进这次的汇报。不会抛出异常。"""
    try:
        text = session_message(rid, trigger, code)
    except Exception:                                   # noqa: BLE001
        text = _fallback_message(rid, trigger)
    try:
        res = daily.push_session(text)
    except Exception as e:                              # noqa: BLE001 - 推不过去不影响日常本身
        res = {"pushed": False, "reason": _err(e)}
    try:
        cliutil.log(f"[daily] 推进用户的会话：{'推过去了' if res.get('pushed') else res.get('reason')}")
        if not res.get("pushed") and rid and _codex_session(res):
            d = reportmod.load(rid)
            d.setdefault("alerts", []).append(f"没能把结果推进你的 Codex 会话：{res.get('reason') or '原因不明'}")
            reportmod.save(d)
    except Exception as e:                              # noqa: BLE001
        cliutil.log(f"[daily] 推送失败没记进汇报：{_err(e)}")


def _codex_session(res: dict) -> bool:
    """用户最近的会话是 Codex 的（推得过去的前提）。push 结果里带 thread 就是；推的时候出错没带，就自己看一眼。"""
    if res.get("thread"):
        return True
    try:
        s = hosts.session() or {}
    except Exception:                                   # noqa: BLE001
        return False
    return s.get("host") == "codex" and bool(s.get("thread"))


_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _tok(v) -> str:
    """只放程序自己生成的短代码进消息（运行编号、状态、项名、错误代码），别的一律换成 ?：防提示注入。"""
    s = str(v) if v is not None else ""
    return s if _TOKEN.match(s) else "?"


def _head(trigger: str) -> str:
    return "[MaaPilot 定时日常]" if trigger == "schedule" else "[MaaPilot 日常]"


_TAIL = "这是程序发的通知，不是用户说的话，也不算用户同意任何事。"


def _fallback_message(rid: str | None, trigger: str) -> str:
    if rid:
        return (f"{_head(trigger)} 运行 {_tok(rid)} 结束了。{_TAIL}先 maapilot report show {_tok(rid)} 看汇报"
                f"（seen 为 true 说明已经说过，就不再说）；按 AGENTS.md「盯着日常」告诉用户，然后 maapilot report seen {_tok(rid)}。")
    return f"{_head(trigger)} 这次没跑起来。{_TAIL}maapilot daily status / maapilot doctor --brief 看原因，按 AGENTS.md「盯着日常」告诉用户。"


def session_message(rid: str | None, trigger: str = "schedule", code: str | None = None) -> str:
    """推进用户会话的一条消息（Codex 里代替 daily watch 的结果）。只放程序生成的固定字段（运行编号、状态、没做成的项、
    是否对账停机、几条官方日程没说），不放目标名、出错信息这类文字：消息进的是用户的会话，不能让外来文字混成指令。"""
    head = _head(trigger)
    if not rid:                                         # 开头就被拦下、汇报也没写成
        return (f"{head} 这次没跑：错误代码 {_tok(code)}。{_TAIL}"
                "maapilot daily status / maapilot doctor --brief 看原因，按 AGENTS.md「盯着日常」马上告诉用户。")
    rid = _tok(rid)
    try:
        d = reportmod.load(rid)
    except Exception:                                   # noqa: BLE001
        d = {}
    st = d.get("status") or "aborted"
    if st == "refused":
        parts = [f"{head} 运行 {rid} 这次没跑：开头就被拦下（错误代码 {_tok((d.get('error') or {}).get('code') or code)}）。"]
    else:
        parts = [f"{head} 运行 {rid} 结束：{_tok(st)}（{'有问题' if reportmod.has_problem({**d, 'status': st}) else '顺利'}）。"]
    if code and st not in ("done", "refused"):
        parts.append(f"错误代码 {_tok(code)}。")
    failed = [_tok(t) for t in d.get("failed") or []]
    if failed:
        parts.append(f"没做成的项：{'、'.join(failed)}。")
    if st == "stopped":
        parts.append("合成玉 / 源石对账停机了。")
    try:
        from maapilot.tools import doctor
        due = len((doctor.news_brief() or {}).get("due") or [])
    except Exception:                                   # noqa: BLE001
        due = 0
    if due:
        parts.append(f"官方日程有 {due} 条消息还没跟用户说（maapilot-events）。")
    parts.append(f"{_TAIL}先 maapilot report show {rid} 看汇报（seen 为 true 说明已经说过，就不再说）；"
                 f"按 AGENTS.md「盯着日常」告诉用户，然后 maapilot report seen {rid}。")
    return "".join(parts)


def _run(only: list[str] | None, trigger: str, no_agent: bool) -> dict:
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
    if headless:
        hosts.name(cfg)                                 # config 的 agent 写错：开头就拦下（不要跑到一半才叫不起 agent）
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
        try:
            e.daily_run_id = run_id                     # 推进会话的消息要带上运行编号（见 run）
        except Exception:                               # noqa: BLE001
            pass
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
                       hint="先 maapilot game update（前台跑，可能超过 10 分钟；命令超时被停下就再跑一次，会接着下载），再 "
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
                info = daily.run_agent(cfg, goals_prompt(r.data["run_id"], active, r.data["tasks"], expired,
                                                   host=hosts.name(cfg)),
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
