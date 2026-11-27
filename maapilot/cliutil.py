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


def envelope(command: str, *, data=None, error: CliError | None = None) -> dict:
    return {
        "ok": error is None,
        "command": command,
        "data": data,
        "error": None if error is None else {"code": error.code, "message": error.message, "hint": error.hint},
    }


class Stopped(SystemExit):
    """命令被信号叫停（SIGTERM / SIGINT / SIGHUP / SIGBREAK，例如日常超时杀掉它）。退出码 128+信号；
    cli.main 照样输出信封（`error.code` 是 `interrupted`）。不经过 cli.main 的地方当普通的 SystemExit。"""

    def __init__(self, signum: int):
        super().__init__(128 + int(signum))
        self.signum = int(signum)


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
def holding(owner: str, *, wait_s: float = 0, job: str | None = None):
    """拿模拟器锁（占用 → BUSY）。yield Lock。不碰模拟器、只是不能和别的命令同时跑的（换程序）用它。"""
    from maapilot.crew import lock

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
        yield lk
    finally:
        lk.release()


@contextmanager
def device(owner: str, *, wait_s: float = 0, job: str | None = None):
    """碰模拟器的命令都包在这里：读配置、配置设备连接、拿锁（占用 → BUSY）。yield Lock。
    子进程继承锁见 maapilot.crew.lock。"""
    from maapilot import config
    from maapilot.crew import device as dev

    conn = config.connection()          # 没配置 → CliError(ENVIRONMENT)
    dev.configure(conn)
    occluders: list[dict] = []
    if getattr(conn, "kind", "adb") == "win32":
        # PC 客户端：ScreenDC 截的是屏幕区域，被别的窗口压住就截到别人。
        # 拿锁期间把挡在前面的窗口（含 DSH 自己的界面）收起来，退出时还原。
        try:
            from maapilot.crew import win32
            w = win32.pick_window(getattr(conn, "window", None))
            if w:
                occluders = win32.hide_occluders(w["hwnd"])
        except Exception:                   # noqa: BLE001 - 收不起来也别拦着命令
            occluders = []
    try:
        with holding(owner, wait_s=wait_s, job=job) as lk:
            try:
                from maapilot.tools import guard
                guard.mark_dirty(owner)         # 可能花东西的命令：下次 guard snapshot 先对账
            except Exception:
                pass
            yield lk
    finally:
        if occluders:
            try:
                from maapilot.crew import win32
                win32.restore_occluders(occluders)
            except Exception:
                pass


@contextmanager
def device_readonly():
    """只读的命令（截图、识字）用：读配置、配置设备连接，不拿锁、不记"可能花了东西"。
    连接时用 Game(readonly=True)（不开输入服务），不会打扰正在跑的 MAA 或拿着锁的命令。"""
    from maapilot import config
    from maapilot.crew import device as dev

    conn = config.connection()           # 没配置 → CliError(ENVIRONMENT)
    dev.configure(conn)
    occluders: list[dict] = []
    if getattr(conn, "kind", "adb") == "win32":
        # 截图也要先把挡在游戏上的窗口收起来：ScreenDC 截的是屏幕区域，DSH 自己的界面盖着就截到它
        try:
            from maapilot.crew import win32
            w = win32.pick_window(getattr(conn, "window", None))
            if w:
                occluders = win32.hide_occluders(w["hwnd"])
        except Exception:                # noqa: BLE001
            occluders = []
    try:
        yield
    finally:
        if occluders:
            try:
                from maapilot.crew import win32
                win32.restore_occluders(occluders)
            except Exception:            # noqa: BLE001
                pass

