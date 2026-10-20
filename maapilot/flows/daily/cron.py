"""v0.1 留下的系统定时（`daily unschedule`、doctor 的 legacy_schedule）与 `daily status`。
v0.2.0 起定时日常由 harness 的会话内定时在用户会话里叫醒 agent，项目不再自己定时；这里只认、只删旧的条目
（经 oscompat.sched：macOS / Linux 是 crontab 里带 `# maapilot-v2 <仓库路径>` 的行，Windows 是任务计划程序里的 `\\MaaPilot\\daily-<哈希>`）。"""
from __future__ import annotations

from maapilot.cliutil import CliError
from maapilot.flows import report as reportmod
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效
from maapilot.oscompat import sched


def legacy_schedule(run: sched.Runner | None = None) -> dict:
    """系统里还留着的 v0.1 定时：{installed, times, entries, old_entries, backend}。installed：本仓库的条目还在
    （`daily unschedule` 删的就是它们）；old_entries：更旧的 MaaPilot 的条目（cron：`maapilot job tick`、`bin/daily.sh`；
    Windows：`\\MaaPilot\\` 下别的目录的任务），只列出来、不删。读不了时 installed 为 None 并带 error；
    这台电脑上没有可用的调度时 installed 为 False 并带 unavailable。run：换掉跑子进程的方式（doctor 用它的 Probe）。"""
    b = sched.backend(run)
    ok, hint = b.available()
    if not ok:
        return {"installed": False, "times": [], "entries": [], "old_entries": [], "backend": b.name, "unavailable": hint}
    try:
        entries = b.read()
    except CliError as e:
        return {"installed": None, "times": [], "entries": [], "old_entries": [], "backend": b.name, "error": e.message}
    mine = [e for e in entries if e.mine]
    return {"installed": bool(mine), "times": sorted(t for e in mine for t in e.times),
            "entries": [e.ident for e in mine], "old_entries": [e.ident for e in entries if e.old], "backend": b.name}


def unschedule(dry_run: bool) -> dict:
    """删掉本仓库在系统里的 v0.1 定时（别的行、别的任务一律不动）。"""
    b = sched.backend()
    r = b.remove(dry_run=dry_run)
    out = {"backend": b.name, "removed": r["removed"], "old_entries": r["old_entries"], "dry_run": dry_run}
    if dry_run:
        out["preview"] = r["preview"]
    return out


def unschedule_cmd(dry_run: bool) -> dict:
    return unschedule(dry_run)


def status() -> dict:
    """有没有在跑、有没有没收尾的日常、上次日常。"""
    run = daily.running()
    op = daily.open_brief()
    if op and run and str(run.get("run_id")) == str(op.get("run_id")):
        op["step"] = run.get("step")
    return {"running": run, "open": op, "last": reportmod.latest()}
