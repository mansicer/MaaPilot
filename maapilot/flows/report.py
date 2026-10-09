"""`maapilot report`：日常汇报 private/reports/<YYYY-MM-DD-HHMM>.md / .json。契约见 docs/cli.md。

.json 是 `daily run` 的 data（加上 `report write` 追加的 appendix）；.md 每次都由 .json 重新生成，给人看。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, CliError
from maapilot.flows._common import now, rel

LABELS = {"startup": "启动游戏", "recruit": "公开招募", "infrast": "基建", "mall": "信用商店与访问好友",
          "annihilation": "剿灭", "award": "领任务奖励与邮件", "farm": "刷理智", "box": "识别干员练度", "goals": "目标",
          "closedown": "关游戏", "game_update": "更新游戏",
          "maintenance": "停机维护"}
_RUN_ID = re.compile(r"^(\d{4})(\d{2})(\d{2})-(\d{4,6})$")


def label(task: str) -> str:
    return LABELS.get(task, task)


def stem(run_id: str) -> str:
    """20260930-0440 → 2026-09-30-0440（汇报文件名）。"""
    m = _RUN_ID.match(run_id)
    if not m:
        raise CliError("unknown_run", f"运行编号「{run_id}」的写法不对（应像 20260930-0440）", exit=BAD_REQUEST)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}-{m.group(4)}"


def run_id_of(stem_: str) -> str:
    y, mo, d, hm = stem_.split("-", 3)
    return f"{y}{mo}{d}-{hm}"


def json_path(run_id: str):
    return paths.REPORTS / f"{stem(run_id)}.json"


def md_path(run_id: str):
    return paths.REPORTS / f"{stem(run_id)}.md"


def _atomic(p, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def save(data: dict) -> str:
    """写 .json 与 .md；保留磁盘上已有的 appendix（无头 agent 在运行中途追加的）。返回 .md 的路径（相对仓库）。"""
    rid = data["run_id"]
    try:
        old = json.loads(json_path(rid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    data["appendix"] = old.get("appendix") or data.get("appendix") or []
    data["report"] = rel(md_path(rid))
    _atomic(json_path(rid), json.dumps(data, ensure_ascii=False, indent=1, default=str))
    _atomic(md_path(rid), render(data))
    return data["report"]


def load(run_id: str) -> dict:
    try:
        return json.loads(json_path(run_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CliError("unknown_run", f"没有运行 {run_id} 的汇报", hint="report list 看有哪些", exit=BAD_REQUEST) \
            from None
    except ValueError as e:
        raise CliError("invalid_report", f"汇报 {run_id} 读不了：{e}", exit=BAD_REQUEST) from None


def runs() -> list[str]:
    """有汇报的运行编号，新的在前。"""
    if not paths.REPORTS.exists():
        return []
    out = []
    for p in paths.REPORTS.glob("*.json"):
        try:
            out.append(run_id_of(p.stem))
        except ValueError:
            continue
    return sorted((r for r in out if _RUN_ID.match(r)), reverse=True)


def latest() -> dict | None:
    """最近一次日常的摘要（doctor --brief 的 last_daily 用）：{run_id, at, ok, status, report}；没有 → None。"""
    for rid in runs():
        try:
            d = load(rid)
        except CliError:
            continue
        return {"run_id": rid, "at": d.get("started_at"), "ok": d.get("ok"), "status": d.get("status"),
                "trigger": d.get("trigger"), "report": d.get("report")}
    return None


# --- 告诉过用户没有（report seen） ----------------------------------------------------------------------------------

UNSEEN_DAYS = 7          # 只看最近几天的汇报
UNSEEN_MAX = 5


def seen_file():
    return paths.REPORTS / "seen.json"


def seen_runs() -> dict:
    """{run_id: 什么时候告诉用户的}。"""
    try:
        d = json.loads(seen_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def mark_seen(run_id: str | None) -> dict:
    rid = run_id or (runs() or [None])[0]
    if rid is None:
        raise CliError("no_report", "还没有日常汇报", exit=BAD_REQUEST)
    load(rid)                                           # 不存在 → unknown_run
    d = seen_runs()
    d[rid] = now().isoformat(timespec="seconds")
    _atomic(seen_file(), json.dumps(d, ensure_ascii=False, indent=1))
    return {"run_id": rid, "seen_at": d[rid]}


def has_problem(d: dict) -> bool:
    """跑完了但不顺利：没做成的项、对账停下、没跑完、交给 agent 的。还在跑的不算。"""
    st = d.get("status")
    if st in (None, "running", "maintenance"):         # 停机维护没跑：不算问题（启动检查会单独提维护）
        return False
    return st != "done" or d.get("ok") is False or bool(d.get("failed"))


def problem_brief(d: dict) -> dict:
    """一次有问题的运行的要点（doctor --brief、daily watch 用）。"""
    tasks = {t.get("task"): t for t in d.get("tasks") or []}
    failed = [{"task": t, "label": label(t), "error": (tasks.get(t) or {}).get("error"),
               "code": (tasks.get(t) or {}).get("code")} for t in d.get("failed") or []]
    out = {"run_id": d.get("run_id"), "trigger": d.get("trigger"), "started_at": d.get("started_at"),
           "finished_at": d.get("finished_at"), "status": d.get("status"), "ok": d.get("ok"), "failed": failed,
           "alerts": d.get("alerts") or [], "report": d.get("report")}
    if d.get("status") == "stopped":
        out["guard_stop"] = {"reason": d.get("stop_reason"), **{k: (d.get("guard") or {}).get(k)
                                                                for k in ("before", "after", "unexplained")}}
    if d.get("handoff"):
        out["handoff"] = {k: d["handoff"].get(k) for k in ("task", "error", "rest")}
    if d.get("maintenance"):
        out["maintenance"] = d["maintenance"]
    return out


STATUS_WORDS = {"done": "跑完了", "stopped": "因为货币对账停下了", "aborted": "没跑完", "handed_off": "停下交给了 agent",
                "running": "还在跑", "maintenance": "赶上官方停机维护，没跑",
                "update_failed": "因为游戏没更新成停下了"}


def summary_lines(d: dict) -> list[str]:
    """给 agent 转述用的几句中文。"""
    who = "定时日常" if d.get("trigger") == "schedule" else "日常"
    lines = [f"{_when(d.get('started_at'))} 的{who}{STATUS_WORDS.get(d.get('status'), d.get('status') or '?')}"
             + ("。" if d.get("status") == "maintenance" else "，都做成了。" if not has_problem(d) else "，有问题。")]
    if d.get("maintenance"):
        lines.append(maintenance_line(d["maintenance"]))
    lines += [f"目标{x}" for x in goals_change_lines(d)]       # 不算问题，但要告诉用户
    if d.get("goals_expired") and d.get("trigger") != "schedule":  # 定时运行里 agent 已经看过、写进总结了
        lines.append("有限时目标到期被挪走了：看看还有没有没做完的（例如活动商店没换完），告诉用户")
    b = problem_brief(d)
    for f in b["failed"]:
        lines.append(f"没做成：{f['label']}" + (f"（{f['error']}）" if f.get("error") else ""))
    if b.get("guard_stop"):
        u = b["guard_stop"].get("unexplained") or {}
        names = {"orundum": "合成玉", "originite": "源石"}
        lines.append(f"对账：{b['guard_stop'].get('reason') or '停下了'}"
                     + ("（" + "、".join(f"{names.get(k, k)} {v:+}" for k, v in u.items()) + "）" if u else ""))
    if b.get("handoff"):
        lines.append(f"停在：{label(b['handoff'].get('task') or '?')}（{b['handoff'].get('error')}）")
    lines += [f"告警：{a}" for a in b["alerts"]]
    return lines


def unseen_problems(days: int = UNSEEN_DAYS, limit: int = UNSEEN_MAX) -> list[dict]:
    """最近 days 天里有问题、还没告诉过用户（report seen）的运行，新的在前。"""
    seen = seen_runs()
    cutoff = now().timestamp() - days * 86400
    out = []
    for rid in runs()[:30]:
        if rid in seen:
            continue
        try:
            d = load(rid)
        except CliError:
            continue
        t = d.get("started_at")
        try:
            if t and datetime.fromisoformat(t).timestamp() < cutoff:
                break
        except ValueError:
            pass
        if has_problem(d):
            out.append({**problem_brief(d), "lines": summary_lines(d)})
            if len(out) >= limit:
                break
    return out


# --- 渲染 ------------------------------------------------------------------------------------------------------------

def _dur(s) -> str:
    if s is None:
        return ""
    s = int(round(s))
    return f"{s // 60} 分 {s % 60} 秒" if s >= 60 else f"{s} 秒"


def _when(iso: str | None) -> str:
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or "?"
    return f"{t.month} 月 {t.day} 日 {t:%H:%M}"


def _num(n: int) -> str:
    sign = "+" if n > 0 else "−" if n < 0 else ""
    return f"{sign}{abs(n):,}"


def _agent_line(a: dict) -> str:
    if not a:
        return ""
    if a.get("error"):
        return f"叫 agent 处理没叫起来：{a['error']}"
    how = {True: "处理好了", False: "没处理好"}.get(a.get("recovered"), "结果不明")
    if a.get("timed_out"):
        how = "超时被停下"
    s = (a.get("summary") or "（没有留下总结）").strip()
    return f"agent 接手（{how}）：{s}"


def _gtitle(g) -> str:
    return str(g.get("title") if isinstance(g, dict) else g)


GOAL_FIELDS = {"status": "状态", "object": "对象", "said": "原话", "approved": "同意", "premium": "花合成玉/源石",
               "deadline": "期限"}


def _done_how(entry) -> str:
    """goals-done.md 里那一段 → "到期，期限 … 已过，自动结束" 这类说明；没有那一段 → 没经过 goals remove 就没了。"""
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return "已完成清单里没有它：不是通过移除目标挪走的"
    ended = entry.get("ended") or entry.get("完成") or ""
    status = entry.get("status") or entry.get("状态") or ""
    if ended:                                           # "2026-10-09 到期，期限 … 已过" → 去掉日期
        return re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", str(ended))
    return str(status)


def goals_change_lines(d: dict) -> list[str]:
    """这次日常里目标的变动（汇报「目标变动」、summary_lines 用）。没有 goals_change 时用开头到期挪走的。"""
    ch = d.get("goals_change")
    if not ch and d.get("goals_expired"):
        ch = {"removed": [{"title": _gtitle(g), "done_entry": {"status": "到期"}} for g in d["goals_expired"]]}
    if not ch:
        return []
    out = []
    for g in ch.get("removed") or []:
        how = _done_how(g.get("done_entry") if isinstance(g, dict) else None)
        out.append(f"移除：{_gtitle(g)}" + (f"（{how}）" if how else ""))
    for g in ch.get("added") or []:
        out.append(f"新加：{_gtitle(g)}")
    for c in ch.get("changed") or []:
        f = GOAL_FIELDS.get(c.get("field"), c.get("field"))
        out.append(f"改了：{c.get('title')} 的{f}：{c.get('before') or '（空）'} → {c.get('after') or '（空）'}")
    return out


def maintenance_line(m: dict) -> str:
    """{start, end, forced_update} → "官方停机维护中（10月9日 06:00–12:00），这次没跑。"。"""
    try:
        s, e = datetime.fromisoformat(m["start"]), datetime.fromisoformat(m["end"])
        end = f"{e:%H:%M}" if e.date() == s.date() else f"{e.month}月{e.day}日 {e:%H:%M}"
        when = f"（{s.month}月{s.day}日 {s:%H:%M}–{end}）"
    except (KeyError, TypeError, ValueError):
        when = ""
    return f"官方停机维护中{when}，这次没跑。" + ("维护后要强制更新游戏。" if m.get("forced_update") else "")


def render(d: dict) -> str:
    trig = {"schedule": "定时", "manual": "手动"}.get(d.get("trigger"), d.get("trigger") or "")
    out = [f"# 日常汇报：{_when(d.get('started_at'))}（{trig}）", ""]
    status = d.get("status")
    failed = d.get("failed") or []
    if status == "running":
        out.append("还在跑。")
    elif status == "aborted":
        out.append("没跑完（原因见下面「要注意」）。")
    elif status == "maintenance":
        out.append(maintenance_line(d.get("maintenance") or {}))
    elif status == "update_failed":
        out.append("游戏客户端要更新，没更新成，后面的都没跑（旧版本进不了游戏）。")
    elif status == "handed_off" and (d.get("handoff") or {}).get("task") == "game_update":
        out.append(f"{(d.get('handoff') or {}).get('error') or '游戏客户端要更新'}：停下交给在场的 agent 更新，更新完接着跑后面的。")
    elif status == "handed_off":
        h = d.get("handoff") or {}
        out.append(f"停在{label(h.get('task', ''))}：没做成，交给在场的 agent 处理，处理完接着跑后面的。")
    elif status == "stopped":
        out.append("**停下了：" + (d.get("stop_reason") or "合成玉或源石有没经同意的减少") + "。**")
    elif failed:
        out.append(f"做完了，有 {len(failed)} 项没做成：{'、'.join(label(t) for t in failed)}。")
    else:
        out.append("全部做完。")
    if d.get("finished_at") and d.get("started_at"):
        try:
            secs = (datetime.fromisoformat(d["finished_at"]) - datetime.fromisoformat(d["started_at"])).total_seconds()
            out[-1] += f"用时 {_dur(secs)}。"
        except ValueError:
            pass
    out += ["", "## 做了什么", ""]
    for t in d.get("tasks") or []:
        name = label(t.get("task", "?"))
        if t.get("skipped"):
            out.append(f"- {name}：没做（{t['skipped']}）")
            continue
        if t.get("task") == "farm" and t.get("ok"):
            f = t.get("result") or {}
            bits = [f"{f.get('stage', '?')} 打了 {f.get('times_done', '?')} 次"]
            if f.get("sanity_used") is not None:
                bits.append(f"用了 {f['sanity_used']} 理智")
            if f.get("medicine_used"):
                bits.append(f"用了 {f['medicine_used']} 瓶理智液")
            out.append(f"- 刷理智：{'，'.join(bits)}")
        elif t.get("task") in ("goals", "maintenance"):
            out.append(f"- {name}：{_agent_line(t.get('agent')) or t.get('note', '')}")
            continue
        elif t.get("ok"):
            out.append(f"- {name}：完成（{_dur(t.get('duration_s'))}）")
        else:
            out.append(f"- {name}：没做成——{t.get('error') or '原因不明'}")
        if t.get("agent"):
            out.append(f"  - {_agent_line(t['agent'])}")
    if d.get("goals_pending"):
        out.append(f"- 目标：有 {d.get('goals_active', '?')} 个进行中的目标，留给会话里的 agent 接着做")
    if d.get("award_pending"):
        out.append("- 领奖励：等目标部分刷完理智再领（会话里的 agent 做）")
    change = d.get("depot_change")
    if status != "maintenance":                         # 维护没跑：不写仓库变化
        out += ["", "## 仓库变化", ""]
    if status == "maintenance":
        pass
    elif change:
        spent = [c for c in change if c["delta"] < 0]
        got = [c for c in change if c["delta"] > 0]
        if spent:
            out.append("花掉：" + "、".join(f"{c['name']} {_num(c['delta'])}" for c in spent))
        if got:
            out.append("得到：" + "、".join(f"{c['name']} {_num(c['delta'])}" for c in got[:30])
                       + ("……" if len(got) > 30 else ""))
        out.append("（含刷图掉落；龙门币只精确到千）")
    elif change == []:
        out.append("没有变化。")
    else:
        out.append("这次没读到前后的仓库，算不出花了多少。")
    gl = goals_change_lines(d)
    if gl:
        out += ["", "## 目标变动", ""] + [f"- {x}" for x in gl]
    g = d.get("guard") or {}
    alerts = list(d.get("alerts") or [])
    if g.get("ok") is False:
        alerts.insert(0, "合成玉 / 源石有没经同意的减少：" + json.dumps(g.get("unexplained") or {}, ensure_ascii=False)
                      + "。日常暂停，等用户说明。")
    elif g.get("ok") is None and status not in ("running", "maintenance"):
        alerts.append("这次没能核对合成玉、源石（对账没做成）。")
    if alerts:
        out += ["", "## 要注意", ""] + [f"- {a}" for a in alerts]
    for a in d.get("appendix") or []:
        out += ["", f"## 补充（{_when(a.get('at'))}）", "", a.get("text", "").strip()]
    return "\n".join(out).rstrip() + "\n"


# --- 命令 ------------------------------------------------------------------------------------------------------------

def show(run_id: str | None) -> dict:
    rid = run_id or (runs() or [None])[0]
    if rid is None:
        raise CliError("no_report", "还没有日常汇报", hint="跑一次日常：daily run", exit=BAD_REQUEST)
    d = load(rid)
    try:
        text = md_path(rid).read_text(encoding="utf-8")
    except OSError:
        text = render(d)
    return {"run_id": rid, "markdown": text, "summary": d}


def list_reports(limit: int) -> dict:
    out = []
    for rid in runs()[:limit]:
        try:
            d = load(rid)
        except CliError:
            continue
        out.append({"run_id": rid, "at": d.get("started_at"), "trigger": d.get("trigger"), "ok": d.get("ok"),
                    "status": d.get("status"), "failed": d.get("failed") or [], "report": d.get("report")})
    return {"reports": out}


def write(run_id: str, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise CliError("bad_arguments", "--append 不能是空的", exit=BAD_REQUEST)
    d = load(run_id)
    d.setdefault("appendix", []).append({"at": now().isoformat(timespec="seconds"), "text": text})
    _atomic(json_path(run_id), json.dumps(d, ensure_ascii=False, indent=1, default=str))
    _atomic(md_path(run_id), render(d))
    return {"run_id": run_id, "report": rel(md_path(run_id)), "appendix": len(d["appendix"])}


def register(sub) -> None:
    g = sub.add_parser("report", help="日常汇报")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")

    p = gs.add_parser("show", help="汇报全文与摘要（默认最近一次）")
    p.add_argument("run_id", nargs="?")
    p.add_argument("--last", action="store_true", help="最近一次（默认）")
    p.set_defaults(func=lambda a: show(None if a.last else a.run_id), command="report show")

    p = gs.add_parser("latest", help="同 show --last")
    p.set_defaults(func=lambda a: show(None), command="report latest")

    p = gs.add_parser("list", help="最近的汇报")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=lambda a: list_reports(a.limit), command="report list")

    p = gs.add_parser("seen", help="记下这次运行的结果已经告诉过用户（默认最近一次）")
    p.add_argument("run_id", nargs="?")
    p.set_defaults(func=lambda a: mark_seen(a.run_id), command="report seen")

    p = gs.add_parser("write", help="在一次运行的汇报后面追加一段")
    p.add_argument("run_id")
    p.add_argument("--append", required=True)
    p.set_defaults(func=lambda a: write(a.run_id, a.append), command="report write")
