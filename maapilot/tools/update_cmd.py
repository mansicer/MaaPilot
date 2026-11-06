"""`maapilot update check / told / apply`：MaaPilot 自己、maa-cli、MaaCore 的更新。契约见 docs/cli.md「update」。

- check：查有没有新版本（`maapilot.knowledge.updates`），顺带说清 MaaPilot 现在能不能直接更新（blocked）。
- told：这些新版本告诉过用户了。
- apply：更新一项。MaaPilot、maa-cli、MaaCore 要 agent 先问过用户，资源不用。日常在跑或没收尾时不做；拿模拟器锁。

MaaPilot 只走 git：本地 main 快进到发布标签，绝不覆盖用户改过的文件；依赖变了同步环境；新代码跑不起来就退回原来的提交，
让用户手里始终是一份能用、能继续收到更新的程序。什么状态都尽量走通：改过的文件新版本没碰就留着照常更新，
不在 main 上但没有自己的提交就切回 main。只有会丢用户东西的才停下。
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from pathlib import Path

from maapilot import __version__, paths
from maapilot.cliutil import BUSY, FAILED, NEEDS_USER, CliError, holding
from maapilot.knowledge import updates
from maapilot.oscompat import WINDOWS, fs, procs
from maapilot.tools import common

BRANCH = "main"
GIT_TIMEOUT_S = 120
SYNC_TIMEOUT_S = 600
MAA_UPDATE_TIMEOUT_S = 1800
ENV_FILES = ("pyproject.toml", "uv.lock")


def _git(*args: str, timeout: float = GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    return procs.run_text(["git", "-C", str(paths.ROOT), *args], timeout=timeout)


def _out(*args: str) -> str:
    p = _git(*args)
    return (p.stdout or "").strip() if p.returncode == 0 else ""


def blocked() -> dict | None:
    """这份 MaaPilot 根本更新不了的原因：{code, message, hint}；能 → None。每种都有出路，用户不会卡死在旧版本上。
    别的状态（改过文件、不在 main）都尽量走通，要拉到新版本才知道走不走得通，见 apply_maapilot。"""
    try:
        if not (paths.ROOT / ".git").exists():
            return {"code": "not_git", "message": "这份 MaaPilot 不是用 git 下载的",
                    "hint": "重新 git clone 一份，把 private/ 和 .tools/ 挪过去"}
        if _git("rev-parse", "--git-dir").returncode != 0:
            return {"code": "git_failed", "message": "git 读不了这个文件夹", "hint": "先修好 git（doctor）"}
    except (OSError, subprocess.SubprocessError):
        return {"code": "no_git", "message": "这台电脑上跑不了 git", "hint": "先装 git"}
    from maapilot.tools import doctor
    doctor.keep_skills_link()                          # 不然 Windows 的目录联接在 git 眼里是"文件被删了"
    return None


def check() -> dict:
    st = updates.status(updates.refresh())
    if st["maapilot"]["update"]:
        st["maapilot"]["blocked"] = blocked()
    return st


def _idle() -> None:
    from maapilot.flows import daily
    run = daily.running() or daily.open_daily()
    if run:
        raise CliError("daily_open", f"日常 {run.get('run_id')} 还没结束，不更新", exit=BUSY,
                       hint="等日常收尾（daily done）后再更新", data={"run_id": run.get("run_id")})


def _uv() -> str | None:
    """uv 的路径：不在 PATH 里时看官方安装脚本的默认位置（setup 可能就是这样装的）。"""
    import shutil
    home = Path.home() / ".local" / "bin" / ("uv.exe" if WINDOWS else "uv")
    return shutil.which("uv") or (str(home) if home.is_file() else None)


def _pip_argv() -> list[str]:
    """用 pip 装；uv 建的环境里默认没有 pip，先用 Python 自带的 ensurepip 补上。"""
    python = sys.executable
    if procs.run_text([python, "-m", "pip", "--version"], timeout=60).returncode != 0:
        p = procs.run_text([python, "-m", "ensurepip"], timeout=SYNC_TIMEOUT_S)
        if p.returncode != 0:
            raise OSError("这个运行环境没有 pip，也找不到 uv，装不了新依赖：先装 uv 再更新")
    return [python, "-m", "pip", "install", "-e", "."]


def _sync_env() -> None:
    """新版本的依赖装进当前环境（setup 时是 uv 或 pip 建的）。"""
    uv = _uv()
    argv = [uv, "pip", "install", "--python", sys.executable, "-e", "."] if uv else _pip_argv()
    # 重装会换掉入口程序；Windows 上它就是正在跑的这个，删不掉
    fs.release_running_exe(Path(sys.executable).with_name("maapilot.exe"))
    p = procs.run_text(argv, timeout=SYNC_TIMEOUT_S, cwd=str(paths.ROOT))
    if p.returncode != 0:
        raise OSError((p.stderr or p.stdout or "装依赖失败").strip()[-300:])


def _verify(want: str) -> None:
    """用新代码起一个进程，确认版本对、命令都能载入（包括 update 自己，下一次更新还得靠它）。"""
    code = ("import maapilot, maapilot.cli; p = maapilot.cli.build_parser(); "
            "p.parse_args(['update', 'check']); print(maapilot.__version__)")
    p = procs.run_text([sys.executable, "-c", code], timeout=60, cwd=str(paths.ROOT))
    got = (p.stdout or "").strip()
    if p.returncode != 0 or got != want:
        raise OSError(f"新版本跑不起来（{(p.stderr or got).strip()[-300:]}）")


def _fetch(tag: str) -> None:
    p = _git("fetch", "--quiet", "--no-tags", "--force", updates.MAAPILOT_GIT, f"refs/tags/{tag}:refs/tags/{tag}")
    if p.returncode != 0:
        raise CliError("network", f"拉不到 {tag}：{(p.stderr or '').strip()[-200:]}", exit=FAILED,
                       hint="网络能上 GitHub 吗？要代理就设 HTTPS_PROXY 再试")


def _names(cmd: str, *args: str) -> list[str]:
    """git 列出的文件路径。--no-renames：改名算一删一加，两个名字都列出来，别漏了旧名字。"""
    flags = ["--name-only", "--no-renames"] if cmd == "diff" else []
    p = _git("-c", "core.quotePath=false", cmd, *flags, *args)
    return (p.stdout or "").splitlines()


def _check_mergeable(tag: str, branch: str, old_main: str) -> None:
    """走不通的才拦：有 tag 里没有的提交（用户自己的工作）、改过的文件新版本也改了。"""
    if _git("merge-base", "--is-ancestor", "HEAD", tag).returncode != 0:
        if branch == BRANCH:
            raise CliError("diverged", f"{BRANCH} 上有 {tag} 里没有的提交", exit=NEEDS_USER, data={"commit": _out("rev-parse", "--short", "HEAD")},
                           hint="告诉用户这些提交是什么（git log），由用户决定怎么合并")
        raise CliError("not_on_main", f"现在在 {branch or '一个提交'} 上，上面有 {tag} 里没有的提交", exit=NEEDS_USER,
                       hint=f"告诉用户这些提交是什么（git log），由用户决定保留还是切回 {BRANCH}")
    if old_main and _git("merge-base", "--is-ancestor", old_main, tag).returncode != 0:
        raise CliError("diverged", f"{BRANCH} 上有 {tag} 里没有的提交", exit=NEEDS_USER, data={"commit": old_main[:7]},
                       hint="告诉用户这些提交是什么（git log），由用户决定怎么合并")
    edited = set(_names("diff", "HEAD"))
    both = sorted(edited & set(_names("diff", "HEAD", tag)))
    if both:
        raise CliError("dirty", "这些文件改过，新版本也改了：" + "、".join(both[:5]), exit=NEEDS_USER,
                       hint="看改了什么、告诉用户；要留着就先另存，用户同意后 git restore 这些文件再更新")
    added = set(_names("diff", "--diff-filter=A", "HEAD", tag))
    blocking = sorted(added & set(_names("ls-files", "--others", "--exclude-standard")))
    if blocking:
        raise CliError("dirty", "这些没入库的文件和新版本的文件同名：" + "、".join(blocking[:5]), exit=NEEDS_USER,
                       hint="告诉用户；用户同意后把这些文件挪走再更新")


def _go_back(prev: str, branch: str, old_main: str) -> bool:
    """更新没成：回到更新前的提交和分支，改过的文件留着。→ 回去了没有。"""
    if branch == BRANCH:
        return _git("reset", "--keep", prev).returncode == 0
    if _git("checkout", "-q", branch or prev).returncode != 0:
        return False
    fix = _git("branch", "-f", BRANCH, old_main) if old_main else _git("branch", "-D", BRANCH)
    return fix.returncode == 0


def apply_maapilot() -> dict:
    st = check()
    p = st["maapilot"]
    if not p["latest"]:
        raise CliError("network", "查不到 MaaPilot 的最新版本", exit=FAILED, data={"errors": st["errors"]},
                       hint="网络能上 GitHub 吗？要代理就设 HTTPS_PROXY 再试")
    if not p["update"]:
        return {"what": "maapilot", "updated": False, "version": __version__, "latest": p["latest"]}
    if p.get("blocked"):
        b = p["blocked"]
        raise CliError(b["code"], b["message"], exit=NEEDS_USER, hint=b["hint"])
    tag, prev = f"v{p['latest']}", _out("rev-parse", "HEAD")
    branch = _out("symbolic-ref", "--short", "HEAD")     # 分离 HEAD 时是空的
    _fetch(tag)
    old_main = prev if branch == BRANCH else _out("rev-parse", "--verify", "-q", f"refs/heads/{BRANCH}")
    _check_mergeable(tag, branch, old_main)
    changed = _names("diff", prev, tag)
    sync = any(f in ENV_FILES for f in changed)
    m = _git("merge", "--ff-only", "--quiet", tag) if branch == BRANCH else _git("checkout", "-q", "-B", BRANCH, tag)
    if m.returncode != 0:
        raise CliError("merge_refused", f"git 没让更新：{(m.stderr or '').strip()[-300:]}", exit=FAILED,
                       hint="什么都没动；看错误原文告诉用户（比如 main 在别的 worktree 里、正在合并）")
    try:
        if sync:
            _sync_env()
        _verify(p["latest"])
    except (OSError, subprocess.SubprocessError) as e:
        back = _go_back(prev, branch, old_main)
        if sync:
            try:
                _sync_env()
            except (OSError, subprocess.SubprocessError):
                pass
        if not back:
            raise CliError("rollback_failed", f"{e}，而且没退回 {__version__}：新版本可能跑不起来", exit=FAILED,
                           data={"commit": _out("rev-parse", "--short", "HEAD"), "want": prev[:7]},
                           hint=f"马上告诉用户；git status 看卡在哪，用户同意后回到 {prev[:7]}") from None
        raise CliError("update_failed", f"{e}，已经退回 {__version__}", exit=FAILED, data={"commit": prev[:7]},
                       hint="程序照旧能用；告诉用户更新没成，把原因报给开发者") from None
    updates.told(["maapilot"])
    return {"what": "maapilot", "updated": True, "from": __version__, "to": p["latest"], "commit": _out("rev-parse", "--short", "HEAD"),
            "env_synced": sync, "url": p["url"], "notes": p["notes"], "switched_from": None if branch == BRANCH else (branch or prev[:7]),
            "reread": ["AGENTS.md", "你所在 harness 的说明文件"], "next": "maapilot update check（新版本可能要求新的 maa-cli）"}


def apply_maa_cli() -> dict:
    from maapilot.tools import tools_cmd
    st = updates.status()
    e = st["maa-cli"]
    if not e["update"]:
        return {"what": "maa-cli", "updated": False, "version": e["current"], "latest": e["latest"], "note": e.get("note")}
    r = tools_cmd.install("maa-cli")
    updates.reread_local()
    return {"what": "maa-cli", "updated": True, "from": e["current"], "to": r["version"], "core": r.get("core")}


def _maa_install(*args: str) -> str | None:
    """跑 maa update 或 maa install，再确认 MaaCore 真能用。→ 没成的原因；成了 → None。"""
    from maapilot import config
    from maapilot.crew import maa as crew_maa, maacore
    name = f"maa {args[0]}"
    try:
        p = procs.run_text([config.tool("maa"), *args], timeout=MAA_UPDATE_TIMEOUT_S, env=crew_maa.env())
    except (OSError, subprocess.SubprocessError) as e:
        return f"{name} 跑不了：{e}"
    if p.returncode != 0:
        return f"{name} 没成功（退出码 {p.returncode}）：{(p.stderr or p.stdout or '').strip()[-300:]}"
    return None if maacore.cli_core() else f"{name} 跑完了，但读不出 MaaCore"


def _core_broken(why: str, saved: dict) -> CliError:
    return CliError("maa_core_broken", f"{why}，MAA 可能用不了", exit=FAILED, data={"backup": saved},
                    hint="日常可能也跑不了，马上告诉用户；再跑一次 update apply maa-core 会接着恢复，备份在 data.backup 里")


def _recover_core() -> bool:
    """上次更新没走完（被杀了、恢复失败）留下的备份先恢复，不能被新备份盖掉。→ 恢复了没有。"""
    from maapilot.crew import maacore
    saved = maacore.saved_backup()
    if saved is None:
        return False
    if not maacore.restore_core(saved):
        raise _core_broken("上次留下的备份没有恢复干净", saved)
    maacore.drop_backup()
    updates.reread_local()
    return True


def apply_maa_core() -> dict:
    from maapilot.crew import maacore
    with holding("update apply maa-core", job="update apply"):
        recovered = _recover_core()
    st = check()
    e = st["maa-core"]
    broken = maacore.cli_core() is None or not maacore.resources_ok()   # 坏了就重装，不管有没有新版本
    if not e["update"] and not broken:
        return {"what": "maa-core", "updated": False, "version": e["current"], "latest": e["latest"], "recovered": recovered}
    with holding("update apply maa-core", job="update apply"):
        saved = {}
        if not broken:
            try:
                saved = maacore.backup_core()
            except OSError as err:
                raise CliError("maa_update_failed", f"备份不了原来的 MaaCore，没有更新：{err}", exit=FAILED, data={"version": e["current"]},
                               hint="原来的版本没动；看看磁盘空间和文件权限") from None
        err = _maa_install("install", "--force") if broken else _maa_install("update")
        restored = err is None or (not broken and maacore.restore_core(saved))
        if restored:
            maacore.drop_backup()
    updates.reread_local()                               # 成没成都重读：别留着已经不对的版本
    cur = (updates.load().get("local") or {}).get("maa_core")
    if not restored:
        raise _core_broken(f"{err}；备份没有恢复干净" if saved else str(err), saved)
    if err:
        raise CliError("maa_update_failed", f"{err}；已恢复原来的版本 {cur}", exit=FAILED, data={"version": cur},
                       hint="MAA 照旧能用，把原因告诉用户（不一定是网络问题，看错误原文）")
    return {"what": "maa-core", "updated": True, "from": e["current"], "to": cur, "latest": e["latest"], "recovered": recovered}


def apply_resource() -> dict:
    """资源的更新不问用户（日常开头自己做），这里给 agent 手动重试。"""
    with holding("update apply maa-resource", job="update apply"):
        r = updates.hot_update()
    if r.get("error"):
        raise CliError("resource_update_failed", f"MAA 资源没更新成：{r['error']}", exit=FAILED, data=r,
                       hint="网络能上 GitHub 吗？旧资源照样能用，下次日常会再试")
    return {"what": "maa-resource", "updated": True, **r}


def cmd_check(args) -> dict:
    return check()


def cmd_told(args) -> dict:
    st = updates.told(args.what)
    return {"told": args.what, "due": st["due"]}


@common.logged
def cmd_apply(args) -> dict:
    _idle()
    if args.what == "maapilot":
        with holding("update apply maapilot", job="update apply"):
            return apply_maapilot()
    return {"maa-cli": apply_maa_cli, "maa-core": apply_maa_core, "maa-resource": apply_resource}[args.what]()


def register(sub) -> None:
    g = sub.add_parser("update", help="MaaPilot、maa-cli、MaaCore 的更新：查新版本、更新")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("check", help="有没有新版本（联网查，最多十几秒）")
    p.add_argument("--refresh", action="store_true", help=argparse.SUPPRESS)  # 现在每次都查；旧版说明里还写着它，留着别报错
    p.set_defaults(func=cmd_check, command="update check")
    p = gs.add_parser("told", help="这些新版本告诉过用户了（过一阵子还没更新会再列出来）")
    p.add_argument("what", nargs="+", choices=updates.WHATS)
    p.set_defaults(func=cmd_told, command="update told")
    p = gs.add_parser("apply", help="更新一项（MaaPilot、maa-cli、MaaCore 要用户同意；资源不用）")
    p.add_argument("what", choices=(*updates.WHATS, "maa-resource"))
    p.set_defaults(func=cmd_apply, command="update apply")
