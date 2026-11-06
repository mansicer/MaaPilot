"""`maapilot update check / told / apply`：MaaPilot 自己、maa-cli、MaaCore 的更新。契约见 docs/cli.md「update」。

- check：查有没有新版本（`maapilot.knowledge.updates`），顺带说清 MaaPilot 现在能不能直接更新（blocked）。
- told：这些新版本告诉过用户了。
- apply：更新一项。MaaPilot、maa-cli、MaaCore 要 agent 先问过用户，资源不用。日常在跑或没收尾时不做；拿模拟器锁。

MaaPilot 只走 git：本地 main 快进到发布标签，绝不覆盖用户改过的文件；依赖变了同步环境；新代码跑不起来就退回原来的提交，
让用户手里始终是一份能用、能继续收到更新的程序。
"""
from __future__ import annotations

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
    """MaaPilot 现在不能直接更新的原因：{code, message, hint}；能 → None。每种都有出路，用户不会卡死在旧版本上。"""
    try:
        if not (paths.ROOT / ".git").exists():
            return {"code": "not_git", "message": "这份 MaaPilot 不是用 git 下载的",
                    "hint": "重新 git clone 一份，把 private/ 和 .tools/ 挪过去"}
        if _git("rev-parse", "--git-dir").returncode != 0:
            return {"code": "git_failed", "message": "git 读不了这个文件夹", "hint": "先修好 git（doctor）"}
    except (OSError, subprocess.SubprocessError):
        return {"code": "no_git", "message": "这台电脑上跑不了 git", "hint": "先装 git"}
    branch = _out("symbolic-ref", "--short", "HEAD")
    if branch != BRANCH:
        return {"code": "not_on_main", "message": f"现在不在 {BRANCH} 分支（{branch or '没有分支'}）",
                "hint": f"问用户后 git switch {BRANCH}"}
    from maapilot.tools import doctor
    doctor.keep_skills_link()                          # 不然 Windows 的目录联接在 git 眼里是"文件被删了"
    dirty = (_git("status", "--porcelain", "--untracked-files=no").stdout or "").splitlines()
    if dirty:
        return {"code": "dirty", "message": "项目文件被改过：" + "、".join(l[3:] for l in dirty[:5]),
                "hint": "看改了什么、告诉用户；要留着就先另存，用户同意后 git restore 这些文件再更新"}
    return None


def check(refresh: bool = False) -> dict:
    d = updates.load()
    if refresh or updates.stale(d):
        d = updates.refresh()
    st = updates.status(d)
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


def apply_maapilot() -> dict:
    st = check(refresh=True)
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
    _fetch(tag)
    if _git("merge-base", "--is-ancestor", "HEAD", tag).returncode != 0:
        raise CliError("diverged", f"{BRANCH} 上有 {tag} 里没有的提交", exit=NEEDS_USER, data={"commit": prev[:7]},
                       hint="告诉用户这些提交是什么（git log），由用户决定怎么合并")
    changed = _out("diff", "--name-only", prev, tag).splitlines()
    sync = any(f in ENV_FILES for f in changed)
    m = _git("merge", "--ff-only", "--quiet", tag)
    if m.returncode != 0:
        raise CliError("update_failed", f"更新不了：{(m.stderr or '').strip()[-300:]}", exit=FAILED,
                       hint="多半是有没入库的文件挡路：看错误里的文件，告诉用户")
    try:
        if sync:
            _sync_env()
        _verify(p["latest"])
    except (OSError, subprocess.SubprocessError) as e:
        _git("reset", "--keep", prev)
        if sync:
            try:
                _sync_env()
            except (OSError, subprocess.SubprocessError):
                pass
        raise CliError("update_failed", f"{e}，已经退回 {__version__}", exit=FAILED, data={"commit": prev[:7]},
                       hint="程序照旧能用；告诉用户更新没成，把原因报给开发者") from None
    updates.told(["maapilot"])
    return {"what": "maapilot", "updated": True, "from": __version__, "to": p["latest"], "commit": _out("rev-parse", "--short", "HEAD"),
            "env_synced": sync, "url": p["url"], "notes": p["notes"],
            "reread": ["AGENTS.md", "你所在 harness 的说明文件"], "next": "maapilot update check --refresh（新版本可能要求新的 maa-cli）"}


def apply_maa_cli() -> dict:
    from maapilot.tools import tools_cmd
    st = updates.status()
    e = st["maa-cli"]
    if not e["update"]:
        return {"what": "maa-cli", "updated": False, "version": e["current"], "latest": e["latest"], "note": e.get("note")}
    r = tools_cmd.install("maa-cli")
    updates.reread_local()
    return {"what": "maa-cli", "updated": True, "from": e["current"], "to": r["version"], "core": r.get("core")}


def apply_maa_core() -> dict:
    from maapilot import config
    from maapilot.crew import maa as crew_maa
    st = check(refresh=True)
    e = st["maa-core"]
    if not e["update"]:
        return {"what": "maa-core", "updated": False, "version": e["current"], "latest": e["latest"]}
    with holding("update apply maa-core", job="update apply"):
        p = procs.run_text([config.tool("maa"), "update"], timeout=MAA_UPDATE_TIMEOUT_S, env=crew_maa.env())
    if p.returncode != 0:
        raise CliError("maa_update_failed", f"maa update 没成功（退出码 {p.returncode}）：{(p.stderr or p.stdout or '').strip()[-300:]}",
                       exit=FAILED, hint="网络能上 GitHub 吗？原来的版本还在，再试一次或下次再更新")
    updates.reread_local()
    cur = (updates.load().get("local") or {}).get("maa_core")
    return {"what": "maa-core", "updated": True, "from": e["current"], "to": cur, "latest": e["latest"]}


def apply_resource() -> dict:
    """资源的更新不问用户（日常开头自己做），这里给 agent 手动重试。"""
    with holding("update apply maa-resource", job="update apply"):
        r = updates.hot_update()
    if r.get("error"):
        raise CliError("resource_update_failed", f"MAA 资源没更新成：{r['error']}", exit=FAILED, data=r,
                       hint="网络能上 GitHub 吗？旧资源照样能用，下次日常会再试")
    return {"what": "maa-resource", "updated": True, **r}


def cmd_check(args) -> dict:
    return check(args.refresh)


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
    p = gs.add_parser("check", help="有没有新版本（缓存旧了联网重查）")
    p.add_argument("--refresh", action="store_true", help="不管缓存，重查一次")
    p.set_defaults(func=cmd_check, command="update check")
    p = gs.add_parser("told", help="这些新版本告诉过用户了（过一阵子还没更新会再列出来）")
    p.add_argument("what", nargs="+", choices=updates.WHATS)
    p.set_defaults(func=cmd_told, command="update told")
    p = gs.add_parser("apply", help="更新一项（MaaPilot、maa-cli、MaaCore 要用户同意；资源不用）")
    p.add_argument("what", choices=(*updates.WHATS, "maa-resource"))
    p.set_defaults(func=cmd_apply, command="update apply")
