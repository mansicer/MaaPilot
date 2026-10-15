"""定时日常与 `daily status`。装、读、删定时条目经 oscompat.sched 的后端（macOS / Linux 是 crontab；Windows 的任务计划程序还没接上）；
这里只管什么时间、执行什么。crontab 里本项目自己的行带标记 `# maapilot-v2 <仓库路径>`，只增删这些行。"""
from __future__ import annotations

import os
import re
import shutil
from datetime import timedelta
from pathlib import Path

from maapilot import config, paths
from maapilot.cliutil import BAD_REQUEST, ENVIRONMENT, CliError
from maapilot.flows import report as reportmod
from maapilot.flows._common import now
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效
from maapilot.oscompat import sched, venv

# --- 定时 ------------------------------------------------------------------------------------------------------------

TAG = sched.TAG
_OLD_MARKS = sched.OLD_MARKS
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_CRON_HM = sched._CRON_HM


def _crontab() -> str:
    return sched.CronBackend.program()


def read_crontab() -> str:
    return sched.CronBackend().read_text()


def write_crontab(text: str) -> None:
    sched.CronBackend().write_text(text)


_mine = sched.CronBackend.mine
_old = sched.CronBackend.old
_times_of = sched.CronBackend.times_of


def _entries(text: str) -> tuple[list[str], list[str]]:
    return sched.CronBackend().split(text)


def _parse_times(times: list[str]) -> list[tuple[int, int]]:
    out = []
    for t in times:
        m = _TIME.match(str(t).strip())
        if not m:
            raise CliError("bad_arguments", f"时间「{t}」要写成 HH:MM（24 小时制）", exit=BAD_REQUEST)
        out.append((int(m.group(1)), int(m.group(2))))
    if not out:
        raise CliError("bad_arguments", "至少要有一个时间点", exit=BAD_REQUEST)
    return sorted(set(out))


def _uses_codex(cfg: dict) -> bool:
    """定时日常要用到 codex：agent 是 codex，或者记过用户的 Codex 会话（跑完要 codex queue 推结果）。"""
    from maapilot import hosts
    try:
        if hosts.name(cfg) == "codex":
            return True
    except CliError:
        pass
    return bool((hosts.sessions().get("codex") or {}).get("thread"))


def _path_dirs(cfg: dict) -> str:
    """定时任务的 PATH：项目 .venv 的程序目录（叫起来的 agent 直接用 maapilot）、maa、agent（claude / codex）、adb 所在目录
    （要用到 codex 时也带上它的目录）+ 系统目录。"""
    from maapilot import hosts
    dirs = [str(venv.venv_bin())]
    found_all = [shutil.which(str(prog)) for prog in _exec_paths(cfg).values()]
    if _uses_codex(cfg):
        found_all.append(hosts.codex_path(cfg))
    dirs += [str(Path(found).parent) for found in found_all if found]
    return venv.join_path(dirs + venv.sys_path_dirs())


def command(cfg: dict) -> sched.Command:
    """定时要执行的命令：项目 .venv 里的 maapilot daily run --trigger schedule，输出追加到 private/log/cron.log。"""
    env = {"PATH": _path_dirs(cfg), "PYTHONUTF8": "1"}
    if os.environ.get("MAAPILOT_PRIVATE"):
        env["MAAPILOT_PRIVATE"] = str(paths.PRIVATE)
    if os.environ.get("CODEX_HOME"):                    # Codex 的设置（信任这个文件夹）放在别处时，定时日常也要找得到
        env["CODEX_HOME"] = os.environ["CODEX_HOME"]
    return sched.Command([str(venv.venv_exe("maapilot")), "daily", "run", "--trigger", "schedule"], env,
                         paths.LOGS / "cron.log")


def cron_lines(times: list[tuple[int, int]], cfg: dict) -> list[str]:
    return sched.CronBackend.render(times, command(cfg))


def schedule(times: str | None, replace_old: bool, dry_run: bool) -> dict:
    cfg = config.load()
    hm = _parse_times(times.split(",") if times else list(cfg.get("schedule") or []))
    r = sched.backend().install(hm, command(cfg), replace_old=replace_old, dry_run=dry_run)
    out = {"times": [f"{h:02d}:{m:02d}" for h, m in hm], "installed": r["installed"], "replaced": r["replaced"],
           "old_entries": r["old_entries"], "removed_old": r["removed_old"], "changed": r["changed"], "dry_run": dry_run}
    if dry_run:
        out["crontab"] = r["preview"]
    return out


def unschedule(dry_run: bool) -> dict:
    r = sched.backend().remove(dry_run=dry_run)
    out = {"removed": r["removed"], "old_entries": r["old_entries"], "dry_run": dry_run}
    if dry_run:
        out["crontab"] = r["preview"]
    return out


# --- 要执行的程序 ------------------------------------------------------------------------------------------------
# 定时日常会在用户不在时执行 config.yaml 里写的程序（tools.maa、tools.<agent>（claude / codex）、emulator.adb）。
# `daily schedule` 的输出带上它们的完整路径（`exec`），agent 在对话里告诉用户。

def _exec_paths(cfg: dict) -> dict:
    """{maa, <agent 宿主名>, adb}。"""
    from maapilot import hosts
    tools = cfg.get("tools") or {}
    host = hosts.name(cfg)
    return {"maa": str(tools.get("maa") or "maa"), host: str(tools.get(host) or host),
            "adb": str((cfg.get("emulator") or {}).get("adb") or "adb")}


def _resolved(ex: dict) -> dict:
    return {k: shutil.which(v) or v for k, v in ex.items()}


def schedule_cmd(times: str | None, replace_old: bool, dry_run: bool) -> dict:
    """`daily schedule`：输出里带上定时日常要执行的程序（`exec`，完整路径）。"""
    from maapilot import hosts
    from maapilot.tools import host_cmd
    cfg = config.load()
    if not dry_run and hosts.name(cfg) == "codex" and host_cmd.codex_trusted(paths.ROOT) is not True:
        # 不信任时 Codex 不加载项目的 .codex/rules/：叫起的 codex 用不了 maapilot，也拦不住直接跑 adb / maa
        raise CliError("codex_untrusted", "定时日常要用 Codex，但 Codex 还没信任这个文件夹，项目的命令规则不生效",
                       exit=ENVIRONMENT, hint="maapilot host install codex")
    return {**schedule(times, replace_old, dry_run), "exec": _resolved(_exec_paths(cfg))}


def unschedule_cmd(dry_run: bool) -> dict:
    return unschedule(dry_run)


def schedule_info(run: sched.Runner | None = None) -> dict:
    """本项目的定时（doctor 的 schedule 检查、--brief、daily status 用）：{installed, times, old_entries}；
    读不了时 installed 为 None 并带 error；这台电脑上没有可用的调度（Windows 暂时）时 installed 为 False 并带 unavailable。
    run：换掉跑子进程的方式（doctor 用它的 Probe）。"""
    b = sched.backend(run)
    ok, hint = b.available()
    if not ok:
        return {"installed": False, "times": [], "old_entries": [], "unavailable": hint}
    try:
        entries = b.read()
    except CliError as e:
        return {"installed": None, "times": [], "old_entries": [], "error": e.message}
    mine = [e for e in entries if e.mine]
    return {"installed": bool(mine), "times": sorted(t for e in mine for t in e.times),
            "old_entries": [e.ident for e in entries if e.old]}


def _next_at(times: list[str]) -> str | None:
    t = now()
    cands = []
    for s in times:
        h, m = map(int, s.split(":"))
        c = t.replace(hour=h, minute=m, second=0, microsecond=0)
        cands.append(c if c > t else c + timedelta(days=1))
    return min(cands).isoformat(timespec="minutes") if cands else None


def status() -> dict:
    sched = daily.schedule_info()
    return {"running": daily.running(), "last": reportmod.latest(), "schedule": sched,
            "next_at": _next_at(sched["times"]) if sched.get("installed") else None}
