"""定时日常的入口（cron 和 Windows 任务计划程序都执行它）：

    <.venv 的 python> -X utf8 -m maapilot.oscompat.launch --env K=V … --env PATH+=<目录…> --log <文件> -- daily run --trigger schedule

先设环境变量（`K=V` 设成 V；`PATH+=` 把这些目录（按 os.pathsep 分开）放到 PATH 最前面；PYTHONUTF8=1 一定设），
再把输出追加进日志（UTF-8；先写一行带时间的开始），然后交给 maapilot.cli.main，退出码照搬。
环境都在这里设：定时任务的环境很空（cron 的 PATH 只有 /usr/bin:/bin），任务计划程序也没有按任务设环境变量的地方。

设环境变量之前只 import 标准库：paths.PRIVATE 在 import 时就读 MAAPILOT_PRIVATE。
Windows 上由 pythonw.exe 执行：没有控制台，sys.stdout / sys.stderr 是 None。所以把描述符 1、2（和 Win32 的标准句柄）
都指到日志：maapilot 自己、MaaCore 的原生日志、没接管输出的子进程写的都进日志。
"""
from __future__ import annotations

import os
import sys
import time
import traceback


def parse(argv: list[str]) -> tuple[list[tuple[str, str]], list[str], str | None, list[str]]:
    """→ (要设的 [(K, V)], 要放到 PATH 前面的目录, 日志, `--` 之后的 maapilot 参数)。写错 → ValueError。"""
    sets: list[tuple[str, str]] = []
    prepend: list[str] = []
    log = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            return sets, prepend, log, argv[i + 1:]
        if a in ("--env", "--log"):
            if i + 1 >= len(argv):
                raise ValueError(f"{a} 后面缺值")
            v = argv[i + 1]
            if a == "--log":
                log = v
            elif v.startswith("PATH+="):
                prepend += [d for d in v[len("PATH+="):].split(os.pathsep) if d]
            elif "=" in v and v.index("=") > 0:
                k, _, val = v.partition("=")
                sets.append((k, val))
            else:
                raise ValueError(f"--env 要写成 K=V 或 PATH+=<目录>：{v}")
            i += 2
            continue
        raise ValueError(f"认不得的参数：{a}（maapilot 的参数写在 -- 后面）")
    raise ValueError("缺少 -- 和要执行的 maapilot 参数")


def apply_env(sets, prepend, environ=None) -> None:
    env = os.environ if environ is None else environ
    for k, v in sets:
        env[k] = v
    if prepend:
        seen, dirs = set(), []
        for d in prepend + [d for d in env.get("PATH", "").split(os.pathsep) if d]:
            key = os.path.normcase(os.path.normpath(d))
            if key not in seen:
                seen.add(key)
                dirs.append(d)
        env["PATH"] = os.pathsep.join(dirs)
    env["PYTHONUTF8"] = "1"                      # 子进程里的 Python 读写一律 UTF-8


def _set_std_handles() -> None:
    """Windows：Win32 的标准输出 / 错误句柄也指到描述符 1、2（pythonw 是窗口程序，C 运行库不替我们改），
    不接管输出的子进程才会写进日志、不弹窗。"""
    if sys.platform != "win32":
        return
    try:                                                              # pragma: no cover - Windows only
        import ctypes
        import msvcrt
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.SetStdHandle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
        for std, fd in ((-11, 1), (-12, 2)):                          # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            k32.SetStdHandle(ctypes.c_ulong(std & 0xFFFFFFFF), msvcrt.get_osfhandle(fd))
    except (ImportError, OSError, AttributeError):                    # pragma: no cover
        pass


def redirect(log: str) -> None:
    """stdout / stderr（描述符 1、2 和 sys.stdout / sys.stderr）追加进 log（UTF-8，按行刷）。"""
    os.makedirs(os.path.dirname(os.path.abspath(log)), exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    except OSError:                                  # 换不了描述符：至少 Python 自己写的进日志
        f = open(fd, "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = sys.stderr = f
        return
    if fd not in (1, 2):
        os.close(fd)
    sys.stdout = open(1, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)
    sys.stderr = open(2, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)
    _set_std_handles()


def _say(msg: str) -> None:
    if sys.stderr is not None:
        try:
            print(msg, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        sets, prepend, log, rest = parse(argv)
    except ValueError as e:
        _say(f"maapilot.oscompat.launch：{e}")
        return 2
    apply_env(sets, prepend)
    if log:
        try:
            redirect(log)
        except OSError as e:
            _say(f"maapilot.oscompat.launch：日志 {log} 打不开：{e}")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    _say(f"=== {stamp} 定时启动（pid {os.getpid()}）：maapilot {' '.join(rest)}")
    try:
        from maapilot import cli
        code = cli.main(rest)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except BaseException:                            # noqa: BLE001 - 写进日志，退出码 1
        _say(traceback.format_exc().rstrip())
        code = 1
    _say(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} 结束，退出码 {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
