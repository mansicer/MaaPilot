"""`maapilot` 命令入口：只做分发。每个命令组在自己的模块里，模块提供

    def register(sub: argparse._SubParsersAction) -> None

在 sub 上加自己的子命令，并用 `set_defaults(func=handler, command="组 子命令")` 指定处理函数。
处理函数见 maapilot.cliutil（返回 data，出错抛 CliError）。命令的契约见 docs/cli.md。
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
import traceback

from maapilot import cliutil
from maapilot.cliutil import BAD_REQUEST, FAILED, CliError

# 命令组 → 模块。tools/ 是工具（builder A），flows/ 是流程（builder B）。
GROUPS = [
    "maapilot.tools.doctor",
    "maapilot.tools.screen",
    "maapilot.tools.input",
    "maapilot.tools.maa_cmd",
    "maapilot.tools.state",
    "maapilot.tools.data",
    "maapilot.tools.calc",
    "maapilot.tools.lock_cmd",
    "maapilot.tools.guard",
    "maapilot.tools.skland_cmd",
    "maapilot.tools.game",
    "maapilot.tools.copilot_cmd",
    "maapilot.tools.news_cmd",
    "maapilot.tools.host_cmd",
    "maapilot.tools.dev_cmd",            # 开发用（dev review），不给用户的 agent
    "maapilot.flows.goals",
    "maapilot.flows.daily",
    "maapilot.flows.op",
    "maapilot.flows.craft",
    "maapilot.flows.report",
    "maapilot.flows.reset",
]


class _Parser(argparse.ArgumentParser):
    """参数错误也输出 JSON 信封（退出码 2），不打印 argparse 的用法文字到 stdout。"""

    def error(self, message):
        raise CliError("bad_arguments", message, hint=f"看 `{self.prog} --help`", exit=BAD_REQUEST)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="maapilot", description="明日方舟账号助手的命令行工具（输出一律 JSON）")
    p.add_argument("--version", action="store_true", help="MaaPilot 的版本（和当前提交）")
    sub = p.add_subparsers(dest="group", metavar="<命令>", parser_class=_Parser)
    for mod in GROUPS:
        importlib.import_module(mod).register(sub)
    return p


def _utf8_stdio() -> None:
    """一律 UTF-8：子进程继承 PYTHONUTF8；本进程的 stdout / stderr 不按系统代码页（Windows 上是 GBK 等）编码。"""
    os.environ.setdefault("PYTHONUTF8", "1")
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):   # 测试里被换成 StringIO 等
            pass


def _divert_stdout() -> tuple[int, object] | None:
    """MaaFramework、MaaCore 的原生日志直接写文件描述符 1，会弄脏 stdout 的 JSON。
    把描述符 1 指到 stderr，返回 (原来的 stdout, Windows 上原来的 STD_OUTPUT_HANDLE)，信封写到原来的 stdout。
    Windows 上原生代码可能直接用 GetStdHandle 拿 Win32 句柄、不经过 C 运行库的描述符 1，所以句柄也一起换。
    stdout 不是真的描述符 1（测试里被捕获）时不动。"""
    try:
        if sys.stdout.fileno() != 1:
            return None
    except (AttributeError, OSError, ValueError):
        return None
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    return saved, _swap_std_handle()


def _swap_std_handle(handle=None):
    """Windows：把 Win32 的 STD_OUTPUT_HANDLE 换成 stderr 的（handle=None）或换回 handle，返回换之前的句柄。
    别的系统什么都不做，返回 None。"""
    from maapilot.oscompat import WINDOWS
    if not WINDOWS:
        return None
    try:
        import ctypes
        import msvcrt
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetStdHandle.restype = ctypes.c_void_p
        k32.GetStdHandle.argtypes = [ctypes.c_ulong]
        k32.SetStdHandle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
        STD_OUTPUT_HANDLE = ctypes.c_ulong(-11 & 0xFFFFFFFF)
        old = k32.GetStdHandle(STD_OUTPUT_HANDLE)
        new = msvcrt.get_osfhandle(2) if handle is None else handle
        k32.SetStdHandle(STD_OUTPUT_HANDLE, new)
        return old
    except (ImportError, OSError, AttributeError):
        return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _utf8_stdio()
    diverted = _divert_stdout()
    out_fd = diverted[0] if diverted else None
    try:
        return _main(argv, out_fd)
    finally:
        if diverted:
            if diverted[1] is not None:
                _swap_std_handle(diverted[1])
            os.close(out_fd)


def _main(argv: list[str], out_fd: int | None) -> int:
    command = " ".join(a for a in argv[:2] if not a.startswith("-")) or "maapilot"
    t0 = time.monotonic()
    args = None
    data = None
    from maapilot.tools import common
    common._recorded.clear()
    try:
        parser = build_parser()
        args, extra = parser.parse_known_args(argv)
        if extra and not getattr(args, "accepts_extra", False):
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
        if getattr(args, "version", False) and not getattr(args, "func", None):
            from maapilot import version
            args.func, args.command = (lambda _a: version.info()), "version"
        if not getattr(args, "func", None):
            raise CliError("bad_arguments", "缺少子命令", hint="看 `maapilot --help`", exit=BAD_REQUEST)
        command = getattr(args, "command", None) or command
        data = args.func(args)
        env, code = cliutil.envelope(command, data=data), 0
    except cliutil.RawOutput as e:
        env, code = e.payload, 0
    except CliError as e:
        env, code = cliutil.envelope(command, data=e.data, error=e), e.exit
        data = e.data
    except KeyboardInterrupt:
        env, code = cliutil.envelope(command, error=CliError("interrupted", "被中断", exit=FAILED)), FAILED
    except SystemExit as e:              # --help 等：argparse 自己打印（到 stderr）后退出
        return int(e.code or 0)
    except Exception as e:               # 未料到的错误：信封里给简短原因，栈打到 stderr
        traceback.print_exc(file=sys.stderr)
        err = CliError("internal_error", f"{type(e).__name__}: {e}", exit=FAILED)
        env, code = cliutil.envelope(command, error=err), FAILED
    _journal(command, argv, args, env, code, data, time.monotonic() - t0)
    if out_fd is None:
        cliutil.emit(env)
    else:
        with os.fdopen(os.dup(out_fd), "w", encoding="utf-8") as out:
            cliutil.emit(env, out)
    return code


def _journal(command: str, argv: list[str], args, env: dict, code: int, data, duration_s: float) -> None:
    """命令自己没记（没包 `@logged`）时补记一行：失败的（包括参数写错、退出码 2）都记，成功的只记有摘要的
    （calc need、goals 改动等）。参数写错时连同命令行（去掉 token 等）一起记，事后能看出 agent 哪里用错了。"""
    from maapilot.tools import common
    from maapilot.tools.log_summary import safe_argv, summarize
    if not argv or common.recorded(command) or command.split(" ")[0] == "dev":
        return
    result = summarize(command, data) if code == 0 else None
    if code == 0 and not result:
        return
    err = env.get("error") if isinstance(env, dict) else None
    extra = {"result": result}
    if err:
        extra.update(error=err.get("code"), message=err.get("message"))
    if code == cliutil.BAD_REQUEST or args is None:
        extra["argv"] = safe_argv(argv)
    try:
        args_d = common._args_dict(args) if args is not None else {}
    except TypeError:
        args_d = {}
    if extra.get("message") is not None:
        from maapilot.tools.log_summary import safe_text
        extra["message"] = safe_text(command, extra["message"], argv)
    common.record(command, args_d, ok=code == 0, exit=code, duration_s=duration_s, **extra)


if __name__ == "__main__":
    sys.exit(main())
