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
          "annihilation": "剿灭", "award": "领任务奖励与邮件", "farm": "刷理智", "goals": "目标",
          "closedown": "关游戏"}
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


def render(d: dict) -> str:
    trig = {"schedule": "定时", "manual": "手动"}.get(d.get("trigger"), d.get("trigger") or "")
    out = [f"# 日常汇报：{_when(d.get('started_at'))}（{trig}）", ""]
    status = d.get("status")
    failed = d.get("failed") or []
    if status == "running":
        out.append("还在跑。")
    elif status == "aborted":
        out.append("没跑完（原因见下面「要注意」）。")
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
        elif t.get("task") == "goals":
            out.append(f"- 目标：{_agent_line(t.get('agent')) or t.get('note', '')}")
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
    out += ["", "## 仓库变化", ""]
    change = d.get("depot_change")
    if change:
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
    g = d.get("guard") or {}
    alerts = list(d.get("alerts") or [])
    if g.get("ok") is False:
        alerts.insert(0, "合成玉 / 源石有没经同意的减少：" + json.dumps(g.get("unexplained") or {}, ensure_ascii=False)
                      + "。日常暂停，等用户说明。")
    elif g.get("ok") is None and status != "running":
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

    p = gs.add_parser("write", help="在一次运行的汇报后面追加一段")
    p.add_argument("run_id")
    p.add_argument("--append", required=True)
    p.set_defaults(func=lambda a: write(a.run_id, a.append), command="report write")
