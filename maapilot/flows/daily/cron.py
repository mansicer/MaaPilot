"""定时（crontab）与 `daily status`。crontab 里本项目自己的行带标记 `# maapilot-v2 <仓库路径>`，只增删这些行。"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from maapilot import config, paths
from maapilot.cliutil import BAD_REQUEST, ENVIRONMENT, CliError
from maapilot.flows import report as reportmod
from maapilot.flows._common import now
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

# --- 定时（crontab） -------------------------------------------------------------------------------------------------

TAG = "# maapilot-v2"
_OLD_MARKS = ("maapilot job tick", "bin/daily.sh")       # 旧版 MaaPilot 的定时任务
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_CRON_HM = re.compile(r"^\s*(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*\s")


def _crontab() -> str:
    """crontab 程序；测试用 MAAPILOT_CRONTAB 换成假的。"""
    return os.environ.get("MAAPILOT_CRONTAB") or "crontab"


def read_crontab() -> str:
    try:
        p = subprocess.run([_crontab(), "-l"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise CliError("no_crontab", f"读不了 crontab：{e}", exit=ENVIRONMENT) from None
    if p.returncode != 0:
        if "no crontab" in (p.stderr or "").lower():
            return ""
        raise CliError("no_crontab", f"读不了 crontab：{(p.stderr or '').strip()}", exit=ENVIRONMENT)
    return p.stdout


def write_crontab(text: str) -> None:
    try:
        p = subprocess.run([_crontab(), "-"], input=text, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise CliError("crontab_failed", f"写不了 crontab：{e}", exit=ENVIRONMENT) from None
    if p.returncode != 0:
        raise CliError("crontab_failed", f"写不了 crontab：{(p.stderr or '').strip()}",
                       hint="macOS 弹窗问终端能不能改 crontab 时要点允许", exit=ENVIRONMENT)


def _mine(line: str) -> bool:
    return TAG in line and line.rstrip().endswith(f"{TAG} {paths.ROOT}")


def _old(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("#") and TAG not in s and any(m in s for m in _OLD_MARKS)


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


def _path_dirs(cfg: dict) -> str:
    """定时任务的 PATH：项目的 .venv/bin（叫起来的 agent 直接用 maapilot）、maa、claude、adb 所在目录 + 系统目录。"""
    dirs = [str(paths.ROOT / ".venv" / "bin")]
    tools = cfg.get("tools") or {}
    for prog in (tools.get("maa") or "maa", tools.get("claude") or "claude",
                 (cfg.get("emulator") or {}).get("adb") or "adb"):
        found = shutil.which(str(prog))
        if found:
            d = str(Path(found).parent)
            if d not in dirs:
                dirs.append(d)
    for d in ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if d not in dirs:
            dirs.append(d)
    return ":".join(dirs)


def cron_lines(times: list[tuple[int, int]], cfg: dict) -> list[str]:
    def q(s) -> str:
        return shlex.quote(str(s)).replace("%", r"\%")    # crontab 里 % 是换行
    envs = f"PATH={q(_path_dirs(cfg))} PYTHONUTF8=1"
    if os.environ.get("MAAPILOT_PRIVATE"):
        envs += f" MAAPILOT_PRIVATE={q(paths.PRIVATE)}"
    cmd = (f"{envs} {q(paths.ROOT / '.venv' / 'bin' / 'maapilot')} daily run --trigger schedule"
           f" >> {q(paths.LOGS / 'cron.log')} 2>&1")
    return [f"{m} {h} * * * {cmd} {TAG} {paths.ROOT}" for h, m in times]


def _entries(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    return [ln for ln in lines if _mine(ln)], [ln for ln in lines if _old(ln)]


def _times_of(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        m = _CRON_HM.match(ln)
        if m:
            out.append(f"{int(m.group(2)):02d}:{int(m.group(1)):02d}")
    return sorted(out)


def schedule(times: str | None, replace_old: bool, dry_run: bool) -> dict:
    cfg = config.load()
    hm = _parse_times(times.split(",") if times else list(cfg.get("schedule") or []))
    cur = read_crontab()
    mine, old = _entries(cur)
    keep = [ln for ln in cur.splitlines() if not _mine(ln) and not (replace_old and _old(ln))]
    new_lines = cron_lines(hm, cfg)
    text = "\n".join(keep + new_lines) + "\n"
    changed = text != (cur if cur.endswith("\n") or not cur else cur + "\n")
    if not dry_run and changed:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        write_crontab(text)
    out = {"times": [f"{h:02d}:{m:02d}" for h, m in hm], "installed": new_lines, "replaced": mine,
           "old_entries": old, "removed_old": old if replace_old else [], "changed": changed, "dry_run": dry_run}
    if dry_run:
        out["crontab"] = text
    return out


def unschedule(dry_run: bool) -> dict:
    cur = read_crontab()
    mine, old = _entries(cur)
    keep = [ln for ln in cur.splitlines() if not _mine(ln)]
    text = "\n".join(keep) + ("\n" if keep else "")
    if mine and not dry_run:
        write_crontab(text)
    out = {"removed": mine, "old_entries": old, "dry_run": dry_run}
    if dry_run:
        out["crontab"] = text
    return out


def schedule_info() -> dict:
    """crontab 里本项目的定时（doctor 的 schedule 检查、--brief 用）：{installed, times, old_entries} 或带 error。"""
    try:
        mine, old = _entries(read_crontab())
    except CliError as e:
        return {"installed": None, "times": [], "old_entries": [], "error": e.message}
    return {"installed": bool(mine), "times": _times_of(mine), "old_entries": old}


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
