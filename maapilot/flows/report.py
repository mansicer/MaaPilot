"""`maapilot report`：日常汇报 private/reports/<YYYY-MM-DD-HHMM>.md / .json。契约见 docs/cli.md。

.json 是 `daily run` 的 data（加上 `report write` 追加的 appendix、`daily done` 收尾时写的 goals_summary）；
.md 每次都由 .json 重新生成，给人看。
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, CliError
from maapilot.flows._common import now, rel
from maapilot.oscompat import fs

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


def save(data: dict) -> str:
    """写 .json 与 .md；保留磁盘上已有的 appendix（agent 在运行中途 report write 追加的）。返回 .md 的路径（相对仓库）。"""
    rid = data["run_id"]
    try:
        old = json.loads(fs.read_text_utf8(json_path(rid)))
    except (OSError, ValueError):
        old = {}
    data["appendix"] = old.get("appendix") or data.get("appendix") or []
    data["report"] = rel(md_path(rid))
    fs.atomic_write_text(json_path(rid), json.dumps(data, ensure_ascii=False, indent=1, default=str))
    fs.atomic_write_text(md_path(rid), render(data))
    return data["report"]


def load(run_id: str) -> dict:
    try:
        return json.loads(fs.read_text_utf8(json_path(run_id)))
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
                "trigger": d.get("trigger"), "open": bool(d.get("open")), "report": d.get("report")}
    return None


def has_problem(d: dict) -> bool:
    """跑完了但不顺利：没做成的项、对账停下、没能对账（读不到仓库）、没跑完、交给 agent 的。还在跑的不算。"""
    st = d.get("status")
    if st in (None, "running", "maintenance"):         # 停机维护没跑：不算问题（启动检查会单独提维护）
        return False
    return st != "done" or d.get("ok") is False or bool(d.get("failed")) or bool(d.get("guard_unreadable"))


def source_alerts(d: dict) -> list[str]:
    """这次日常期间（started_at 到 finished_at，还在跑就到现在）记下的外部数据格式问题（private/state/sources.json）。
    日常本身不用管：各命令在解析外部数据时自己记，这里按时间段取出来放进"要注意"。"""
    try:
        from maapilot.knowledge import sources
        return [f"外部数据：{x}（要开发者修）" for x in sources.errors_between(d.get("started_at"), d.get("finished_at"))]
    except Exception:                                    # noqa: BLE001 - 读不了不影响汇报
        return []


def problem_brief(d: dict) -> dict:
    """一次运行的要点（summary_lines 用）。"""
    tasks = {t.get("task"): t for t in d.get("tasks") or []}
    failed = [{"task": t, "label": label(t), "error": (tasks.get(t) or {}).get("error"),
               "code": (tasks.get(t) or {}).get("code")} for t in d.get("failed") or []]
    out = {"run_id": d.get("run_id"), "trigger": d.get("trigger"), "started_at": d.get("started_at"),
           "finished_at": d.get("finished_at"), "status": d.get("status"), "ok": d.get("ok"), "failed": failed,
           "alerts": [*(d.get("alerts") or []), *source_alerts(d)], "report": d.get("report")}
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
                "update_failed": "因为游戏没更新成停下了", "refused": "开头就被拦下，没开始"}


def summary_lines(d: dict) -> list[str]:
    """给 agent 转述用的几句中文。"""
    who = "定时日常" if d.get("trigger") == "schedule" else "日常"
    lines = [f"{_when(d.get('started_at'))} 的{who}{STATUS_WORDS.get(d.get('status'), d.get('status') or '?')}"
             + ("。" if d.get("status") == "maintenance" else "，都做成了。" if not has_problem(d) else "，有问题。")]
    if d.get("maintenance"):
        lines.append(maintenance_line(d["maintenance"]))
    lines += [f"目标{x}" for x in goals_change_lines(d)]       # 不算问题，但要告诉用户
    if d.get("goals_expired"):
        lines.append("有限时目标到期被挪走了：看看还有没有没做完的（例如活动商店没换完），告诉用户")
    b = problem_brief(d)
    for f in b["failed"]:
        lines.append(f"没做成：{f['label']}" + (f"（{f['error']}）" if f.get("error") else ""))
    if b.get("guard_stop"):
        u = b["guard_stop"].get("unexplained") or {}
        names = {"orundum": "合成玉", "originite": "源石"}
        lines.append(f"对账：{b['guard_stop'].get('reason') or '停下了'}"
                     + ("（" + "、".join(f"{names.get(k, k)} {v:+}" for k, v in u.items()) + "）" if u else ""))
    if d.get("guard_unreadable") and not b.get("guard_stop"):
        lines.append("没能核对合成玉 / 源石（读不到仓库），这次的花费没有对账")
    if b.get("handoff"):
        lines.append(f"停在：{label(b['handoff'].get('task') or '?')}（{b['handoff'].get('error')}）")
    lines += [f"告警：{a}" for a in b["alerts"]]
    due = (d.get("news") or {}).get("due") or []
    if due:
        lines.append(f"官方日程有 {len(due)} 条消息还没跟用户说（新公告 / 维护结束 / 活动开始，maapilot-events；说完 news told）")
    return lines


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


_OUTCOME_TAIL = re.compile(r"\n?[ \t>*_-]*结果[：:][^\n]*\s*$")


def agent_text(a: dict | None) -> str:
    """agent 的总结，去掉最后一行「结果：完成 / 没做成」（程序自己用，不给人看）。"""
    s = ((a or {}).get("summary") or "").strip()
    return _OUTCOME_TAIL.sub("", s).rstrip()


def _lines(text: str) -> list[str]:
    """多行文字 → 行，连续空行并成一行。"""
    out: list[str] = []
    for ln in text.splitlines():
        ln = ln.rstrip()
        if ln or (out and out[-1]):
            out.append(ln)
    while out and not out[-1]:
        out.pop()
    return out


def _item(head: str, text: str, indent: str = "  ", sep: str = "——") -> list[str]:
    """列表的一项：head 后面接文字的第一行，其余行缩进到这一项下面（不让总结里的列表跑出去）。"""
    ls = _lines(text)
    if not ls:
        return [head]
    return [f"{head}{sep}{ls[0]}"] + [indent + ln if ln else "" for ln in ls[1:]]


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


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s or "")


def same_text(a: str, b: str, threshold: float = 0.7) -> bool:
    """两段文字（几乎）是同一段话：去掉空白、标点后，较短那段的大部分字按顺序出现在另一段里。"""
    from difflib import SequenceMatcher
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x in y or y in x:
        return True
    m = sum(blk.size for blk in SequenceMatcher(None, x, y, autojunk=False).get_matching_blocks())
    return m / min(len(x), len(y)) >= threshold


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
        out.append(f"{(d.get('handoff') or {}).get('error') or '游戏客户端要更新'}：停下交给 agent 更新，更新完接着跑后面的。")
    elif status == "handed_off":
        h = d.get("handoff") or {}
        out.append(f"停在{label(h.get('task', ''))}：没做成，交给 agent 处理，处理完接着跑后面的。")
    elif status == "refused":
        out.append("**开头就被拦下，没开始：" + ((d.get("error") or {}).get("message") or "原因不明") + "。**")
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
    if d.get("open") and status != "running":
        out.append("（这次日常还没收尾：agent 做完后用 daily done 收尾。）")
    out += ["", "## 做了什么", ""]
    for t in d.get("tasks") or []:
        name = label(t.get("task", "?"))
        if t.get("skipped"):
            out.append(f"- {name}：没做（{t['skipped']}）")
        elif t.get("task") == "farm" and t.get("ok"):
            f = t.get("result") or {}
            bits = [f"{f.get('stage', '?')} 打了 {f.get('times_done', '?')} 次"]
            if f.get("sanity_used") is not None:
                bits.append(f"用了 {f['sanity_used']} 理智")
            if f.get("proxy_failed"):
                bits.append(f"其中 {f['proxy_failed']} 次代理失败（理智退回了）")
            if f.get("medicine_used"):
                bits.append(f"用了 {f['medicine_used']} 瓶理智液")
            out.append(f"- 刷理智：{'，'.join(bits)}")
        elif t.get("ok"):
            out.append(f"- {name}：完成（{_dur(t.get('duration_s'))}）")
        else:
            out.append(f"- {name}：没做成——{t.get('error') or '原因不明'}" + ("（后面重跑了）" if t.get("retried") else ""))
    gs = d.get("goals_summary") or {}
    if d.get("goals_pending"):
        out.append(f"- 目标：有 {d.get('goals_active', '?')} 个进行中的目标，"
                   + ("见下面「目标部分」" if gs.get("text") else "留给 agent 接着做"))
    if d.get("award_pending"):
        out.append("- 领奖励：等目标部分刷完理智再领（agent 做）")
    text = agent_text({"summary": gs.get("text")})
    if text:
        out += ["", "## 目标部分", ""] + _lines(text)
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
    alerts = [*(d.get("alerts") or []), *source_alerts(d)]
    if g.get("ok") is False:
        alerts.insert(0, "合成玉 / 源石有没经同意的减少：" + json.dumps(g.get("unexplained") or {}, ensure_ascii=False)
                      + "。日常暂停，等用户说明。")
    elif g.get("ok") is None and status not in ("running", "maintenance"):
        alerts.append("这次没能核对合成玉、源石（对账没做成）。")
    if alerts:
        out += ["", "## 要注意", ""] + [f"- {a}" for a in alerts]
    summaries = [text] if text else []
    for a in d.get("appendix") or []:
        extra = (a.get("text") or "").strip()
        if any(same_text(extra, s_) for s_ in summaries):        # agent 又追加了一遍自己的总结：只留一份
            continue
        out += ["", f"## 补充（{_when(a.get('at'))}）", ""] + _lines(extra)
    return "\n".join(out).rstrip() + "\n"


# --- 命令 ------------------------------------------------------------------------------------------------------------

def show(run_id: str | None) -> dict:
    rid = run_id or (runs() or [None])[0]
    if rid is None:
        raise CliError("no_report", "还没有日常汇报", hint="跑一次日常：daily run", exit=BAD_REQUEST)
    d = load(rid)
    try:
        text = fs.read_text_utf8(md_path(rid))
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
    fs.atomic_write_text(json_path(run_id), json.dumps(d, ensure_ascii=False, indent=1, default=str))
    fs.atomic_write_text(md_path(run_id), render(d))
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

    p = gs.add_parser("write", help="在一次运行的汇报后面追加一段「补充」")
    p.add_argument("run_id")
    p.add_argument("--append", required=True)
    p.set_defaults(func=lambda a: write(a.run_id, a.append), command="report write")
