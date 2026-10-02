"""`maapilot reset [--dry-run] [--memory]`：从头设置 —— 把 private/ 整个挪成备份、删掉本项目的定时任务。

什么都不删：private/ 改名成同目录下的 `private.bak-<YYYYmmdd-HHMMSS>`（配置、setup.json、目标、快照、日志、汇报、截图、对账都在里面），
恢复就是把它改回来。之后 `doctor --brief` 的 `next` 是 `setup`，下次会话重新设置。
留着：.venv、data/、resource/、maa-cli 本身，以及 maa-cli 配置目录里装好的配置与任务模板（从备份复制回新的 private/maa-config/）。
crontab 只删以 `# maapilot-v2 <本仓库>` 结尾的行（复用 daily unschedule），别的行不动。
`--memory`：Claude Code 给这个项目目录记的自动记忆（`<claude 配置目录>/projects/<路径里非字母数字换成 ->/memory/`）也挪成旁边的备份。

日常在跑、模拟器锁被占着、在日常叫起来的 agent 里（环境里有 MAAPILOT_DAILY_RUN 或继承的锁）→ 不做。权限是"询问"。契约见 docs/cli.md「reset」。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, BUSY, CliError
from maapilot.tools import common

PROJECTS_ENV = "MAAPILOT_CLAUDE_PROJECTS_DIR"     # 测试用：Claude Code 的 projects 目录
MAA_CONFIG = "maa-config"


def claude_projects_dir() -> Path:
    env = os.environ.get(PROJECTS_ENV)
    if env:
        return Path(env).expanduser()
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(cfg).expanduser() if cfg else Path.home() / ".claude") / "projects"


def project_slug(root: Path) -> str:
    """Claude Code 给项目目录起的名字：绝对路径里每个非字母数字的字符换成 `-`（/Users/a/MaaPilot → -Users-a-MaaPilot）。"""
    return re.sub(r"[^A-Za-z0-9]", "-", str(root))


def memory_dir(root: Path | None = None) -> Path:
    return claude_projects_dir() / project_slug(root or paths.ROOT) / "memory"


def _refuse_if_busy() -> None:
    from maapilot.crew import lock
    from maapilot.flows import daily
    if os.environ.get(daily.DAILY_ENV) or os.environ.get(lock.TOKEN_ENV):
        raise CliError("not_allowed", "这是日常叫起来的 agent（或继承了模拟器锁），不能重置",
                       hint="重置只在用户在场、亲口要求时做", exit=BAD_REQUEST)
    run = daily.running()
    if run:
        raise CliError("daily_running", f"日常 {run.get('run_id')} 正在跑（{run.get('step') or '?'}），先等它跑完",
                       hint="maapilot daily status", exit=BUSY, data={"running": run})
    holder = lock.holder()
    if holder:
        raise CliError("busy", "模拟器正被占用（有程序或接管的 agent 在操作），先等它结束",
                       hint="maapilot lock status", exit=BUSY, data={"holder": common.public_lock(holder)})


def _backup_path(p: Path, stamp: str) -> Path:
    b = p.with_name(f"{p.name}.bak-{stamp}")
    n = 2
    while b.exists():
        b = p.with_name(f"{p.name}.bak-{stamp}-{n}")
        n += 1
    return b


def reset(dry_run: bool, memory: bool) -> dict:
    from maapilot.flows import daily
    _refuse_if_busy()
    stamp = common.now().strftime("%Y%m%d-%H%M%S")
    priv = paths.PRIVATE
    out: dict = {"dry_run": dry_run}

    # 1. 定时任务：只算本项目的行（先做：写不了 crontab 就什么都没动）
    cron = daily.unschedule(dry_run=True)
    out["crontab"] = {"remove": cron["removed"], "other_lines_kept": True}

    # 2. private/ → 备份
    if priv.exists():
        bak = _backup_path(priv, stamp)
        entries = sorted(p.name for p in priv.iterdir())
        keep = priv / MAA_CONFIG
        out["private"] = {"move": str(priv), "to": str(bak), "entries": entries,
                          "restore": f"mv {bak} {priv}（先挪走新建的 {priv.name}）",
                          "keep_maa_config": str(keep) if keep.is_dir() else None}
    else:
        bak = None
        out["private"] = None

    # 3. 自动记忆（--memory 才动）
    mem = memory_dir()
    if memory:
        if mem.is_dir():
            mbak = _backup_path(mem, stamp)
            out["memory"] = {"move": str(mem), "to": str(mbak), "entries": sorted(p.name for p in mem.iterdir())}
        else:
            mbak = None
            out["memory"] = {"move": None, "note": f"没有自动记忆目录 {mem}"}
    else:
        mbak = None
        out["memory"] = {"move": None, "note": "没带 --memory：自动记忆不动"}

    out["keep"] = [str(paths.ROOT / ".venv"), str(paths.DATA), str(paths.RESOURCE), "maa-cli（及它的资源）"]
    if dry_run:
        return out

    if cron["removed"]:
        out["crontab"]["removed"] = daily.unschedule(dry_run=False)["removed"]
    if bak is not None:
        os.rename(priv, bak)
        src = bak / MAA_CONFIG
        if src.is_dir():
            shutil.copytree(src, priv / MAA_CONFIG)
    if memory and mbak is not None:
        os.rename(mem, mbak)
    out["next"] = "setup"
    return out


def cmd(args):
    return reset(args.dry_run, args.memory)


def register(sub) -> None:
    p = sub.add_parser("reset", help="从头设置：private/ 挪成备份、删本项目的定时任务（询问）")
    p.add_argument("--dry-run", action="store_true", help="只列出会挪走、删掉什么，不动")
    p.add_argument("--memory", action="store_true", help="同时把 Claude Code 给这个项目的自动记忆挪成备份")
    p.set_defaults(func=cmd, command="reset")
