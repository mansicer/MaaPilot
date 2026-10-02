"""`maapilot maa why`：一个 MAA 任务为什么失败 —— 从 MAA 自己的详细日志（asst.log）里摘出那段时间的经过。

只读：不碰模拟器、不拿锁。时间段默认取最近一次失败的 `maapilot maa …`（private/log/<日期>.jsonl 的记录：
结束时间 `at`、时长 `duration_s`；定时日常里跑的每一项也记在这里）。摘要与推断见 maapilot.crew.maalog。
契约见 docs/cli.md「maa why」。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, CliError
from maapilot.tools import common

LOOKBACK_DAYS = 7
PAD_BEFORE_S = 3          # 时间段往前多读几秒（maa 启动、连接）
PAD_AFTER_S = 5


def _records():
    """最近几天的命令记录，新的在前。"""
    files = sorted(paths.LOGS.glob("????-??-??.jsonl"), reverse=True)[:LOOKBACK_DAYS]
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def find_record(task: str | None, last: bool) -> dict | None:
    """最近一次（失败的，除非 last）`maa <任务>` 的记录。"""
    for row in _records():
        cmd = str(row.get("command") or "")
        if not cmd.startswith("maa ") or cmd == "maa why":
            continue
        if task and cmd != f"maa {task}":
            continue
        if not last and row.get("ok"):
            continue
        return row
    return None


def _parse_time(s: str, what: str) -> datetime:
    t = common.parse_iso(s)
    if t is None:
        try:
            t = datetime.strptime(s, "%H:%M").replace(year=common.now().year, month=common.now().month,
                                                     day=common.now().day).astimezone()
        except ValueError:
            raise CliError("bad_arguments", f"{what} 不是时间：{s}（写 2026-10-02T16:01:00 或 16:01）",
                           exit=BAD_REQUEST) from None
    return t


def maa_log_for(task: str, start: datetime, end: datetime) -> Path | None:
    """private/log/maa/ 下这一次的 maa-cli 日志（文件名里的开始时间落在时间段里）。"""
    d = paths.LOGS / "maa"
    best = None
    for p in d.glob(f"*-{task}.log") if d.is_dir() else []:
        try:
            t = datetime.strptime(p.name[:15], "%Y%m%d-%H%M%S").astimezone()
        except ValueError:
            continue
        if start - timedelta(seconds=10) <= t <= end:
            best = p if best is None or p.name > best.name else best
    return best


def _maa_log_tail(p: Path | None, n: int = 6) -> list[str]:
    if p is None:
        return []
    try:
        lines = [l.strip() for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except OSError:
        return []
    return lines[-n:]


def explain(task: str | None = None, since: str | None = None, until: str | None = None,
            last: bool = False) -> dict:
    from maapilot.crew import maalog
    rec = None
    if since:
        start = _parse_time(since, "--since")
        end = _parse_time(until, "--until") if until else common.now()
        if end <= start:
            raise CliError("bad_arguments", "--until 要晚于 --since", exit=BAD_REQUEST)
    else:
        if until:
            raise CliError("bad_arguments", "--until 要和 --since 一起用", exit=BAD_REQUEST)
        rec = find_record(task, last)
        if rec is None:
            what = f"maa {task}" if task else "maa 任务"
            raise CliError("not_found", f"最近 {LOOKBACK_DAYS} 天没有{'' if last else '失败的'} {what} 记录",
                           hint="用 --since/--until 指定时间段", exit=BAD_REQUEST)
        end = common.parse_iso(rec.get("at"))
        if end is None:
            raise CliError("not_found", "记录里没有时间", exit=BAD_REQUEST)
        start = end - timedelta(seconds=float(rec.get("duration_s") or 0))
        task = task or str(rec["command"]).split(" ", 1)[1]
    w_start, w_end = start - timedelta(seconds=PAD_BEFORE_S), end + timedelta(seconds=PAD_AFTER_S)
    out: dict = {"task": task, "window": {"start": common.iso(w_start), "end": common.iso(w_end)}}
    if rec is not None:
        out["record"] = {k: rec.get(k) for k in ("at", "command", "ok", "exit", "error", "duration_s", "run_id")
                         if rec.get(k) is not None}
        if rec.get("shot_now"):
            out["shot_now"] = rec["shot_now"]
    if task:
        p = maa_log_for(task, start, end)
        out["maa_cli_log"] = common.rel(p) if p else None
        out["maa_cli_log_tail"] = _maa_log_tail(p)
    out.update(maalog.why(w_start, w_end))
    if out["log"]["dir"] is None:
        out["hint"] = "找不到 MAA 的日志目录（maa dir log）；先 maapilot doctor"
    return out


def cmd(args):
    return explain(args.task_key, args.since, args.until, args.last)


def register(gs) -> None:
    p = gs.add_parser("why", help="查一个 MAA 任务为什么失败（读 MAA 的详细日志，只读）")
    p.add_argument("--task", dest="task_key", metavar="任务", help="哪一项（如 infrast）；默认最近失败的那项")
    p.add_argument("--since", metavar="时间", help="自己指定时间段的开始（ISO 时间或 HH:MM）")
    p.add_argument("--until", metavar="时间", help="时间段的结束（默认现在）")
    p.add_argument("--last", action="store_true", help="取最近一次（不管成没成）")
    p.set_defaults(func=cmd, command="maa why")
