"""`maapilot lock status / release`：模拟器锁（private/lock.json，见 maapilot.crew.lock）。契约见 docs/cli.md「lock」。"""
from __future__ import annotations

from maapilot import paths
from maapilot.cliutil import BUSY, CliError


def _public(d):
    if d is None:
        return None
    return {k: v for k, v in d.items() if k != "token"}


def cmd_status(args):
    from maapilot.crew import lock
    d = lock.current()
    stale = lock.stale_reason(d) if d else None
    import os
    tok = os.environ.get(lock.TOKEN_ENV)
    return {"holder": None if stale else _public(lock.holder()), "stale": _public(d) if stale else None,
            "stale_reason": stale,
            # 这个进程带着持锁者给的 token：它的 maapilot 命令能直接用模拟器（定时日常叫起的 agent 应当是 true）
            "inherited": bool(tok and d and not stale and d.get("token") == tok)}


def cmd_release(args):
    """只删残留的锁；有效的锁退出码 4。"""
    from maapilot.crew import lock
    d = lock.current()
    if d is None:
        return {"released": False, "detail": "没有锁"}
    why = lock.stale_reason(d)
    if why is None:
        raise CliError("busy", f"锁还有效：{d.get('job') or d.get('owner')}（pid {d.get('pid')}）在用模拟器",
                       hint="等它结束；bin/maapilot lock status 看是谁", exit=BUSY, data={"holder": _public(d)})
    try:
        paths.LOCK_FILE.unlink()
    except FileNotFoundError:
        pass
    return {"released": True, "detail": why, "was": _public(d)}


def register(sub) -> None:
    g = sub.add_parser("lock", help="模拟器锁")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("status", help="谁占着模拟器")
    p.set_defaults(func=cmd_status, command="lock status")
    p = gs.add_parser("release", help="删掉残留的锁（持有进程不在了、心跳过期）")
    p.set_defaults(func=cmd_release, command="lock release")
