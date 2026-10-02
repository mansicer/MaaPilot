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
    sub = p.add_subparsers(dest="group", metavar="<命令>", parser_class=_Parser)
    for mod in GROUPS:
        importlib.import_module(mod).register(sub)
    return p


def _divert_stdout() -> int | None:
    """MaaFramework、MaaCore 的原生日志直接写文件描述符 1，会弄脏 stdout 的 JSON。
    把描述符 1 指到 stderr，返回原来的 stdout（信封写到那里）。stdout 不是真的描述符 1（测试里被捕获）时不动。"""
    try:
        if sys.stdout.fileno() != 1:
            return None
    except (AttributeError, OSError, ValueError):
        return None
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    return saved


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out_fd = _divert_stdout()
    try:
        return _main(argv, out_fd)
    finally:
        if out_fd is not None:
            os.close(out_fd)


def _main(argv: list[str], out_fd: int | None) -> int:
    command = " ".join(a for a in argv[:2] if not a.startswith("-")) or "maapilot"
    try:
        parser = build_parser()
        args, extra = parser.parse_known_args(argv)
        if extra and not getattr(args, "accepts_extra", False):
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
        if not getattr(args, "func", None):
            raise CliError("bad_arguments", "缺少子命令", hint="看 `maapilot --help`", exit=BAD_REQUEST)
        command = getattr(args, "command", None) or command
        data = args.func(args)
        env, code = cliutil.envelope(command, data=data), 0
    except cliutil.RawOutput as e:
        env, code = e.payload, 0
    except CliError as e:
        env, code = cliutil.envelope(command, data=e.data, error=e), e.exit
    except KeyboardInterrupt:
        env, code = cliutil.envelope(command, error=CliError("interrupted", "被中断", exit=FAILED)), FAILED
    except SystemExit as e:              # --help 等：argparse 自己打印（到 stderr）后退出
        return int(e.code or 0)
    except Exception as e:               # 未料到的错误：信封里给简短原因，栈打到 stderr
        traceback.print_exc(file=sys.stderr)
        err = CliError("internal_error", f"{type(e).__name__}: {e}", exit=FAILED)
        env, code = cliutil.envelope(command, error=err), FAILED
    if out_fd is None:
        cliutil.emit(env)
    else:
        with os.fdopen(os.dup(out_fd), "w", encoding="utf-8") as out:
            cliutil.emit(env, out)
    return code


if __name__ == "__main__":
    sys.exit(main())
