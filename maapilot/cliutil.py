"""命令行的公共部分：JSON 信封、退出码、错误、拿模拟器锁。

每个命令的处理函数签名是 `handler(args) -> dict | list | None`：返回值放进信封的 data；
出错抛 CliError（带 code / message / hint / exit）。cli.main 统一打印信封、设退出码。
stdout 只有一个 JSON 信封；进度、日志一律写 stderr（`log()`）。
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager

# 退出码
OK = 0            # 成功
FAILED = 1        # 执行了但失败
BAD_REQUEST = 2   # 请求不能执行（参数错、文件不对），原样重试没用
NEEDS_USER = 3    # 要用户处理（例如货币对账不过）
BUSY = 4          # 模拟器被占用
ENVIRONMENT = 5   # 环境问题（adb、maa-cli、模拟器、配置缺失）→ 先跑 doctor


class CliError(Exception):
    def __init__(self, code: str, message: str, *, hint: str | None = None, exit: int = FAILED,
                 data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.exit = exit
        self.data = data          # 失败时也想带回的数据（例如对账的差额），放进信封的 data


def not_implemented(command: str) -> CliError:
    return CliError("not_implemented", f"`{command}` 还没实现", exit=BAD_REQUEST)


def envelope(command: str, *, data=None, error: CliError | None = None) -> dict:
    return {
        "ok": error is None,
        "command": command,
        "data": data,
        "error": None if error is None else {"code": error.code, "message": error.message, "hint": error.hint},
    }


class RawOutput(Exception):
    """处理函数要原样输出一个 JSON（不包信封）时抛它：只给 `doctor --hook` 用。退出码 0。"""

    def __init__(self, payload: dict):
        super().__init__("raw output")
        self.payload = payload


def emit(env: dict, stream=None) -> None:
    stream = stream or sys.stdout
    stream.write(json.dumps(env, ensure_ascii=False, indent=1, default=str) + "\n")
    stream.flush()


def log(*parts) -> None:
    """给人看的进度写 stderr。"""
    print(*parts, file=sys.stderr, flush=True)


@contextmanager
def device(owner: str, *, wait_s: float = 0, job: str | None = None):
    """碰模拟器的命令都包在这里：读配置、配置设备连接、拿锁（占用 → BUSY）。yield Lock。
    子进程继承锁见 maapilot.crew.lock。"""
    from maapilot import config
    from maapilot.crew import device as dev, lock

    conn = config.connection()          # 没配置 → CliError(ENVIRONMENT)
    dev.configure(conn)
    try:
        lk = lock.acquire(owner, job=job, wait_s=wait_s)
    except lock.LockBusy as e:
        try:
            holder = lock.holder()
            holder = None if holder is None else {k: v for k, v in holder.items() if k != "token"}
        except Exception:
            holder = None
        raise CliError("busy", str(e), hint="稍后再试，或等正在跑的任务结束（lock status 看谁占着）", exit=BUSY,
                       data={"holder": holder}) from None
    try:
        from maapilot.tools import guard
        guard.mark_dirty(owner)             # 可能花东西的命令：下次 guard snapshot 先对账
    except Exception:
        pass
    try:
        yield lk
    finally:
        lk.release()


@contextmanager
def device_readonly():
    """只读的命令（截图、识字）用：读配置、配置设备连接，不拿锁、不记"可能花了东西"。
    连接时用 Game(readonly=True)（不开输入服务），不会打扰正在跑的 MAA 或拿着锁的命令。"""
    from maapilot import config
    from maapilot.crew import device as dev

    dev.configure(config.connection())   # 没配置 → CliError(ENVIRONMENT)
    yield


def register_stub(sub, name: str, help: str, subcommands: list[str] | None = None) -> None:
    """占位：注册一个还没实现的命令组，任何参数都回 not_implemented。实现时整个 register() 换掉。"""
    import argparse

    def handler(args):
        raise not_implemented(args.command)

    g = sub.add_parser(name, help=help)
    if not subcommands:
        g.add_argument("rest", nargs=argparse.REMAINDER)
        g.set_defaults(func=handler, command=name, accepts_extra=True)
        return
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    for s in subcommands:
        p = gs.add_parser(s)
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=handler, command=f"{name} {s}", accepts_extra=True)
