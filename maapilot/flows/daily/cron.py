"""定时日常与 `daily status`。装、读、删定时条目经 oscompat.sched 的后端（macOS / Linux 是 crontab，Windows 是任务计划程序）；
这里只管什么时间、执行什么（启动器 `python -m maapilot.oscompat.launch …`，环境变量和日志都在它的参数里）、装之前查要执行的程序。
crontab 里本项目自己的行带标记 `# maapilot-v2 <仓库路径>`，只增删这些行；任务计划程序里是 `\\MaaPilot\\daily-<哈希>` 一个任务。"""
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
LOG_NAME = "schedule.log"
LAUNCH = ("-m", "maapilot.oscompat.launch")
RUN = ("daily", "run", "--trigger", "schedule")


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
    """定时任务要放到 PATH 前面的目录：项目 .venv 的程序目录（叫起来的 agent 直接用 maapilot）、maa、agent（claude / codex）、
    adb 所在目录（要用到 codex 时也带上它的目录）+ 系统目录。"""
    from maapilot import hosts
    dirs = [str(venv.venv_bin())]
    found_all = [shutil.which(str(prog)) for prog in _exec_paths(cfg).values()]
    if _uses_codex(cfg):
        found_all.append(hosts.codex_path(cfg))
    dirs += [str(Path(found).parent) for found in found_all if found]
    return venv.join_path(dirs + venv.sys_path_dirs())


def launch_env(cfg: dict) -> list[str]:
    """启动器的 --env：PATH+=<目录>、设定时时环境里有的 MAAPILOT_PRIVATE / CODEX_HOME（Codex 的设置放在别处时，
    定时日常也要找得到对这个文件夹的信任）、PYTHONUTF8=1。"""
    env = [f"PATH+={_path_dirs(cfg)}"]
    if os.environ.get("MAAPILOT_PRIVATE"):
        env.append(f"MAAPILOT_PRIVATE={paths.PRIVATE}")
    if os.environ.get("CODEX_HOME"):
        env.append(f"CODEX_HOME={os.environ['CODEX_HOME']}")
    env.append("PYTHONUTF8=1")
    return env


def command(cfg: dict) -> sched.Command:
    """定时要执行的命令：项目 .venv 的 python -X utf8 -m maapilot.oscompat.launch --env … --log private/log/schedule.log
    -- daily run --trigger schedule。schedule_options 交给后端（只有任务计划程序用）。"""
    opts = config.schedule_options(cfg)
    log = paths.LOGS / LOG_NAME
    argv = [str(venv.venv_exe("python")), "-X", "utf8", *LAUNCH]
    for e in launch_env(cfg):
        argv += ["--env", e]
    argv += ["--log", str(log), "--", *RUN]
    return sched.Command(argv, log, run_if_missed=opts["run_if_missed"], wake=opts["wake_computer"])


def cron_lines(times: list[tuple[int, int]], cfg: dict) -> list[str]:
    return sched.CronBackend.render(times, command(cfg))


# --- 装之前查一遍 ----------------------------------------------------------------------------------------------------
# 定时任务在用户不在时执行这些程序：找不到的、装在 Codex（Windows 应用）隔离目录里的，到点一定跑不成，当场拒绝。
# Codex 的 Windows 应用把用户目录重定向到 `…\AppData\Local\Packages\<包名>\LocalCache\…`，在里面装的程序和仓库，
# 任务计划程序（在 Codex 外面运行）看不到。

def isolated(path) -> bool:
    from maapilot.oscompat import exe
    return exe.in_msix_cache(path)


def _real(p: str) -> str:
    try:
        return os.path.realpath(p)
    except (OSError, ValueError):
        return p


def preflight(cfg: dict, cmd: sched.Command, which=None) -> list[dict]:
    """定时任务要执行的程序（.venv 的 python、maa、agent、adb，要用 codex 时还有 codex）和仓库目录：
    [{what, path, problem: "missing" | "isolated"}]，没问题是空列表。which：找程序（doctor 换成它的 Probe）。"""
    from maapilot import hosts
    which = which or shutil.which
    progs = {"python": cmd.argv[0], **_exec_paths(cfg, which)}
    if _uses_codex(cfg) and "codex" not in progs:
        progs["codex"] = hosts.codex_path(cfg) or "codex"
    out = []
    for what, prog in progs.items():
        found = which(str(prog))
        if not found:
            out.append({"what": what, "path": str(prog), "problem": "missing"})
        elif isolated(found) or isolated(_real(found)):
            out.append({"what": what, "path": found if isolated(found) else _real(found), "problem": "isolated"})
    root = str(paths.ROOT)
    if isolated(root) or isolated(_real(root)):
        out.append({"what": "项目文件夹", "path": root if isolated(root) else _real(root), "problem": "isolated"})
    return out


def preflight_error(problems: list[dict]) -> CliError:
    iso = [p for p in problems if p["problem"] == "isolated"]
    missing = [p for p in problems if p["problem"] == "missing"]
    if iso:
        return CliError("isolated_install", "这些装在 Codex 应用的隔离目录里（…\\Packages\\…\\LocalCache\\…），"
                        "Windows 任务计划程序看不到，定时日常跑不起来：" + "；".join(f"{p['what']} {p['path']}" for p in iso),
                        exit=ENVIRONMENT, data={"problems": problems},
                        hint="在普通的 PowerShell（不是 Codex 里）把它们和项目文件夹装到普通目录（例如 C:\\Users\\<你>\\ 下），"
                             "改好 config.yaml 的 tools / emulator.adb 再设定时")
    return CliError("program_missing", "定时日常要执行的程序找不到：" + "；".join(f"{p['what']}（{p['path']}）" for p in missing),
                    exit=ENVIRONMENT, data={"problems": problems},
                    hint="maapilot doctor；config.yaml 的 tools / emulator.adb 写成绝对路径，.venv 没装就先装运行环境")


def schedule(times: str | None, replace_old: bool, dry_run: bool) -> dict:
    cfg = config.load()
    hm = _parse_times(times.split(",") if times else list(cfg.get("schedule") or []))
    cmd = command(cfg)
    b = sched.backend()
    problems = preflight(cfg, cmd) if b.available()[0] else []
    if problems and not dry_run:
        raise preflight_error(problems)
    r = b.install(hm, cmd, replace_old=replace_old, dry_run=dry_run)
    want = [f"{h:02d}:{m:02d}" for h, m in hm]
    out = {"backend": b.name, "times": want, "installed": r["installed"], "replaced": r["replaced"],
           "old_entries": r["old_entries"], "removed_old": r["removed_old"], "changed": r["changed"], "dry_run": dry_run,
           "options": {"run_if_missed": cmd.run_if_missed, "wake_computer": cmd.wake}}
    if dry_run:
        out["preview"] = r["preview"]
        if problems:
            out["problems"] = problems
        return out
    # 读回来核对：系统里真的是这几个时间、任务启用着
    info = daily.schedule_info()
    out["schedule"] = info
    out["next_at"] = _next_at(info["times"]) if info.get("installed") else None
    if info.get("installed") is not True or info["times"] != want or info.get("enabled") is False:
        raise CliError("schedule_not_applied", f"定时设了，但读回来不对：要的是 {'、'.join(want)}，"
                       f"系统里是 {'、'.join(info['times']) or '没有'}"
                       + ("（任务被停用了）" if info.get("enabled") is False else "")
                       + (f"；{info['error']}" if info.get("error") else ""),
                       exit=ENVIRONMENT, data=out, hint="maapilot daily status；再跑一次 maapilot daily schedule")
    return out


def unschedule(dry_run: bool) -> dict:
    b = sched.backend()
    r = b.remove(dry_run=dry_run)
    out = {"backend": b.name, "removed": r["removed"], "old_entries": r["old_entries"], "dry_run": dry_run}
    if dry_run:
        out["preview"] = r["preview"]
    return out


# --- 要执行的程序 ------------------------------------------------------------------------------------------------
# 定时日常会在用户不在时执行 config.yaml 里写的程序（tools.maa、tools.<agent>（claude / codex）、emulator.adb）。
# `daily schedule` 的输出带上它们的完整路径（`exec`），agent 在对话里告诉用户。

def _exec_paths(cfg: dict, which=shutil.which) -> dict:
    """{maa, <agent 宿主名>, adb}。agent 按无头运行时的找法（hosts.resolve）。"""
    from maapilot import hosts
    tools = cfg.get("tools") or {}
    host = hosts.name(cfg)
    return {"maa": str(tools.get("maa") or "maa"), host: hosts.resolve(cfg, host, which=which) or str(tools.get(host) or host),
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
    """本项目的定时（doctor 的 schedule 检查、--brief、daily status、daily schedule 读回来核对用）：
    {installed, times, old_entries, backend}；任务计划程序还带 task（任务名）、settings（{run_if_missed, wake}）；
    任务被停用了带 enabled: false。读不了时 installed 为 None 并带 error；这台电脑上没有可用的调度时 installed 为 False
    并带 unavailable。run：换掉跑子进程的方式（doctor 用它的 Probe）。"""
    b = sched.backend(run)
    ok, hint = b.available()
    if not ok:
        return {"installed": False, "times": [], "old_entries": [], "backend": b.name, "unavailable": hint}
    try:
        entries = b.read()
    except CliError as e:
        return {"installed": None, "times": [], "old_entries": [], "backend": b.name, "error": e.message}
    mine = [e for e in entries if e.mine]
    out = {"installed": bool(mine), "times": sorted(t for e in mine for t in e.times),
           "old_entries": [e.ident for e in entries if e.old], "backend": b.name}
    if any(e.enabled is False for e in mine):
        out["enabled"] = False
    if b.name == "schtasks" and mine:
        out["task"] = mine[0].ident
        out["settings"] = mine[0].settings
    return out


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
    on = sched.get("installed") and sched.get("enabled") is not False
    return {"running": daily.running(), "last": reportmod.latest(), "schedule": sched,
            "next_at": _next_at(sched["times"]) if on else None}
