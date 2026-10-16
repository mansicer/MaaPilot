"""进程：起子进程、结束整棵进程树、查父进程链 / 子孙进程、按名字找进程、UTF-8 地跑子进程。

macOS / Linux：子进程自成进程组（start_new_session），结束时整组先 TERM 再 KILL。
Windows：没有进程组和 SIGTERM。子进程用 CREATE_NEW_PROCESS_GROUP 起（能单独收到 Ctrl+Break），再放进一个
Job Object（KILL_ON_JOB_CLOSE：我们自己死了它也跟着走）；结束时先发 Ctrl+Break、等一会儿，再整个 Job 结束掉。
Ctrl+Break 只能发给同一个控制台上的进程：本进程连着控制台（看得见的、藏起来的都算）时子进程就用它，直接发；
本进程没有控制台（pythonw、任务计划程序）时子进程加 CREATE_NO_WINDOW（不弹黑窗口），各自有一个看不见的控制台，
发的时候先挂到子进程的控制台上（AttachConsole）再发。收不到 Ctrl+Break 的（GUI 程序、不理它的）等满 grace 后整个 Job 结束。
进程表一律用 psutil（三个系统一套代码，create_time 防 pid 被复用）。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from functools import cache
from typing import Callable, Iterable

from maapilot.oscompat import WINDOWS

# 叫停 maapilot 的信号：Windows 没有 SIGHUP，有 SIGBREAK（Ctrl+Break，CREATE_NEW_PROCESS_GROUP 起的子进程只收得到它）
STOP_SIGNALS: tuple[signal.Signals, ...] = tuple(
    s for s in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None),
                getattr(signal, "SIGBREAK", None) if sys.platform == "win32" else None) if s is not None)

DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _psutil():
    import psutil                       # 用到才导入：maacore_runner 之类的小进程不用它
    return psutil


def child_env(extra: dict | None = None) -> dict:
    """子进程的环境：本进程的环境 + PYTHONUTF8=1（子进程里的 Python 读写一律 UTF-8）+ extra。"""
    return {**os.environ, "PYTHONUTF8": "1", **(extra or {})}


@cache
def has_console() -> bool:
    """本进程连着控制台没有（窗口藏起来的也算，如 GitHub Actions、CREATE_NO_WINDOW 起的）。pythonw、任务计划程序
    起的没有：它的子进程要加 CREATE_NO_WINDOW，不然每个控制台程序都弹一个黑窗口。macOS / Linux 一律 True。
    不看 GetConsoleWindow：藏起来的控制台它也给 NULL，子进程就会被错放到自己的控制台上，收不到我们的 Ctrl+Break。"""
    if not WINDOWS:
        return True
    return _attached_to_console()                               # pragma: no cover - Windows only


def spawn_flags(group: bool, console: bool, extra: int = 0) -> int:
    """Windows 上起子进程的 creationflags：extra（调用方给的）+ 自成一组 + 本进程没控制台时 CREATE_NO_WINDOW
    （调用方已经指定了 DETACHED_PROCESS / CREATE_NEW_CONSOLE 就不加）。"""
    flags = extra | (CREATE_NEW_PROCESS_GROUP if group else 0)
    if not console and not flags & (DETACHED_PROCESS | CREATE_NEW_CONSOLE):
        flags |= CREATE_NO_WINDOW
    return flags


def break_route(flags: int, console: bool) -> str | None:
    """按 creationflags 看 Ctrl+Break 怎么送到子进程："ours" 它和本进程在同一个控制台上，直接发；
    "own" 它有自己的控制台，要先挂过去再发（只有本进程没控制台时挂得上）；None 送不到（没自成一组、没控制台）。"""
    if not flags & CREATE_NEW_PROCESS_GROUP or flags & DETACHED_PROCESS:
        return None
    if flags & (CREATE_NO_WINDOW | CREATE_NEW_CONSOLE):
        return "own"
    return "ours" if console else None


def _creationflags(group: bool) -> int:
    return spawn_flags(group, has_console())


def _text_kw(kw: dict, text: bool) -> dict:
    if text:
        kw["text"] = True
        kw["encoding"] = "utf-8"
        kw.setdefault("errors", "replace")
    env = kw.get("env")
    kw["env"] = child_env() if env is None else {"PYTHONUTF8": "1", **env}
    return kw


def spawn(argv, *, group: bool = True, text: bool = True, **kw) -> subprocess.Popen:
    """subprocess.Popen，另外：group=True 时子进程自成一组（kill_tree 能连子孙一起结束）；text=True 时一律 UTF-8；
    env 不给就用 child_env()。Windows 上还放进一个 Job Object（proc._maapilot_job）。"""
    kw = _text_kw(kw, text)
    if WINDOWS:                                                       # pragma: no cover - Windows only
        kw["creationflags"] = spawn_flags(group, has_console(), kw.get("creationflags", 0))
    else:
        kw.setdefault("start_new_session", group)
    proc = subprocess.Popen(argv, **kw)
    proc._maapilot_group = group
    proc._maapilot_break = break_route(kw["creationflags"], has_console()) if WINDOWS else None
    proc._maapilot_job = _job_for(proc) if WINDOWS else None
    return proc


@contextmanager
def on_console_stop(fn: Callable[[], None]):
    """Windows：在这段时间里本进程收到 Ctrl+Break / Ctrl+C / 关窗时，马上在系统另起的线程里跑 fn（例如先结束自己的
    子进程树）。主线程这时多半卡在等子进程上（Windows 上等进程、等锁都不会被信号打断，Python 的信号处理要等它返回才跑），
    所以 fn 不能指望主线程。跑 fn 之前先给主线程补上同一个信号（_thread.interrupt_main），等它醒来照常走信号处理
    （收尾、退出）。Python 没接这个信号就不拦，按系统默认走。macOS / Linux：什么都不做（信号会打断等待）。"""
    if not WINDOWS or not has_console():
        yield
        return
    handler = _add_ctrl_handler(fn)                                  # pragma: no cover - Windows only
    try:                                                             # pragma: no cover
        yield
    finally:                                                         # pragma: no cover
        _k32().SetConsoleCtrlHandler(handler, False)
        _CTRL_HANDLERS.remove(handler)


def is_batch(prog) -> bool:
    """Windows 上 prog 是不是 .cmd / .bat（npm 装的 claude、codex 就是）。这种程序由 cmd.exe 转一道：参数里换行后面的
    会被丢掉，双引号、%VAR%、& 也会被 cmd 改写。多行的长文字（agent 的提示）要走 stdin，单行的用 batch_arg 洗一遍。"""
    return WINDOWS and str(prog).lower().endswith((".cmd", ".bat"))


def batch_arg(s: str) -> str:
    """要经 cmd.exe 转的一个参数：换行并成空格，双引号换成单引号，% & | < > ^ 换成全角（不让 cmd 当变量、命令符号）。"""
    s = " ".join(str(s).split("\n")).replace("\r", " ").replace('"', "'")
    return s.translate(str.maketrans("%&|<>^", "％＆｜＜＞＾"))


def run_text(argv, timeout: float | None = None, *, input: str | None = None, **kw) -> subprocess.CompletedProcess:
    """subprocess.run 拿文本输出：UTF-8 解码（解不了的字替换掉，不抛错）、默认收下 stdout 和 stderr、
    不给 input 时 stdin 接空（不卡在等输入上）、env 不给就用 child_env()、Windows 上没控制台时不弹窗。"""
    kw = _text_kw(kw, True)
    if "stdout" not in kw and "stderr" not in kw:
        kw.setdefault("capture_output", True)
    if input is None:
        kw.setdefault("stdin", subprocess.DEVNULL)
    if WINDOWS:                                                       # pragma: no cover - Windows only
        kw["creationflags"] = kw.get("creationflags", 0) | _creationflags(False)
    return subprocess.run(argv, input=input, timeout=timeout, **kw)


# --- 结束进程 -------------------------------------------------------------------------------------------------------

def kill_tree(proc: subprocess.Popen, grace_s: float, *, kill_wait_s: float | None = None,
              also: dict | None = None) -> int | None:
    """结束 proc 和它的子孙：先礼后兵。macOS / Linux：TERM 整组（连同子孙另起的组）→ 等 grace_s → KILL → 等 kill_wait_s，
    最后子孙的组一律再 KILL 一次（主进程收到 TERM 就走了也一样）。Windows：Ctrl+Break → 等 grace_s（送不到就不等）→ 结束整个 Job
    （没放进 Job 就按 pid 结束它和子孙）→ 等。
    also：之前用 descendants() 记下的子孙（主进程一死就找不回来了），一起结束。→ proc 的退出码（没等到 → None）。"""
    kill_wait_s = grace_s if kill_wait_s is None else kill_wait_s
    seen = {**(also or {}), **_safe_descendants(proc.pid)}
    if WINDOWS:                                                       # pragma: no cover - Windows only
        return _kill_tree_win32(proc, grace_s, kill_wait_s, seen)
    group = getattr(proc, "_maapilot_group", True)
    groups = {g for g, _ in seen.values() if isinstance(g, int)} - {proc.pid, os.getpgrp()}
    for sig, wait in ((signal.SIGTERM, grace_s), (signal.SIGKILL, kill_wait_s)):
        _killpgs(groups, sig)
        try:
            if group:
                os.killpg(proc.pid, sig)
            else:
                proc.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=wait)
            break
        except subprocess.TimeoutExpired:
            continue
    _killpgs(groups, signal.SIGKILL)
    return proc.returncode


def _killpgs(groups, sig) -> None:
    for g in groups:
        try:
            os.killpg(g, sig)
        except OSError:
            pass


def _safe_descendants(pid: int) -> dict:
    try:
        return descendants(pid)
    except Exception:                    # noqa: BLE001 - 查不到就算了，主进程照样结束
        return {}


def _kill_tree_win32(proc, grace_s, kill_wait_s, seen) -> int | None:   # pragma: no cover - Windows only
    psutil = _psutil()
    left = _process_objs(seen)
    with proc.__dict__.setdefault("_maapilot_kill_lock", threading.Lock()):   # 关窗处理线程和主线程可能同时来结束
        job = getattr(proc, "_maapilot_job", None)
        if proc.poll() is None and _send_break(proc):
            try:
                proc.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                pass
        if job is None:                               # 没放进 Job（如本进程所在的 Job 不许嵌套）：子孙只能按 pid 一个个结束
            left += _process_objs(_safe_descendants(proc.pid))
            if proc.poll() is None:
                proc.kill()
        else:
            _job_terminate(job)
        try:
            proc.wait(timeout=kill_wait_s)
        except subprocess.TimeoutExpired:
            pass
        for p in left:
            try:
                p.kill()
            except (psutil.Error, OSError):
                pass
        if job is not None:
            _job_close(job)
            proc._maapilot_job = None
        return proc.returncode


def _process_objs(seen: dict) -> list:                # pragma: no cover - Windows only
    psutil = _psutil()
    out = []
    for pid, (_, cmd) in seen.items():                # 先拿住 Process 对象：之后 kill 时 psutil 会核对 pid 没被复用
        try:
            p = psutil.Process(pid)
            if _cmdline(p) == cmd:
                out.append(p)
        except (psutil.Error, OSError):
            pass
    return out


def _send_break(proc) -> bool:                         # pragma: no cover - Windows only
    """给 proc 的进程组发 Ctrl+Break（怎么送见 break_route）。→ 发出去了没有（没发出去就不用等 grace）。"""
    route = getattr(proc, "_maapilot_break", None)
    if route == "ours":
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            return True
        except OSError:
            return False
    if route == "own":
        return _break_via_attach(proc.pid)
    return False


def kill_pids(pids: Iterable[int], grace_s: float) -> None:
    """结束这些进程（不连子孙）：TERM（Windows 上就是结束）→ 等 grace_s → 还在的 KILL。不存在的跳过。"""
    psutil = _psutil()
    procs = []
    for pid in pids:
        try:
            procs.append(psutil.Process(pid))
        except (psutil.Error, OSError, ValueError):
            pass
    for p in procs:
        try:
            p.terminate()
        except (psutil.Error, OSError):
            pass
    _, alive = psutil.wait_procs(procs, timeout=grace_s)
    for p in alive:
        try:
            p.kill()
        except (psutil.Error, OSError):
            pass
    if alive:
        psutil.wait_procs(alive, timeout=min(grace_s, 5))


# --- 查进程 ---------------------------------------------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    """这台机器上 pid 还在不在。Windows 上 os.kill(pid, 0) 会直接结束那个进程，改用 OpenProcess + GetExitCodeProcess。"""
    if sys.platform == "win32":
        return _pid_alive_win32(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def _pid_alive_win32(pid: int) -> bool:          # pragma: no cover - exercised on Windows only
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE, ERROR_ACCESS_DENIED = 0x1000, 259, 5
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p                  # HANDLE：默认的 int 会截断 64 位句柄
    k32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED   # 打不开但存在（别的用户的进程）
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def ancestors(pid: int | None = None) -> list[int]:
    """pid（默认本进程）的父进程链，由近到远，不含它自己。父进程比子进程还新（pid 被复用了）就到此为止。查不到 → []。"""
    psutil = _psutil()
    try:
        return [p.pid for p in psutil.Process(os.getpid() if pid is None else pid).parents()]
    except (psutil.Error, OSError):
        return []


def _cmdline(p) -> str:
    """命令行（参数用空格连起来，同 ps 的 command 列）；读不到参数就给进程名。"""
    psutil = _psutil()
    try:
        args = p.cmdline()
        if args:
            return " ".join(args)
    except (psutil.Error, OSError):
        pass
    try:
        return p.name()
    except (psutil.Error, OSError):
        return ""


def _group_of(p):
    if WINDOWS:                                       # pragma: no cover - Windows 没有进程组，用 pid 自己
        return p.pid
    try:
        return os.getpgid(p.pid)
    except OSError:
        return None


def descendants(pid: int) -> dict[int, tuple[object, str]]:
    """pid 的子孙进程：pid → (进程组：macOS / Linux 是 pgid，Windows 是 pid 自己, 命令行)。pid 不在了 → {}。"""
    psutil = _psutil()
    try:
        kids = psutil.Process(pid).children(recursive=True)
    except (psutil.Error, OSError):
        return {}
    out = {}
    for p in kids:
        try:
            out[p.pid] = (_group_of(p), _cmdline(p))
        except (psutil.Error, OSError):
            pass
    return out


def cmdline(pid: int) -> str | None:
    """pid 现在的命令行（格式同 descendants）；进程不在了 → None。"""
    psutil = _psutil()
    try:
        p = psutil.Process(pid)
        if p.status() == psutil.STATUS_ZOMBIE:
            return None
        return _cmdline(p)
    except (psutil.Error, OSError):
        return None


def _bare(name: str) -> str:
    name = name.lower()
    return name[:-4] if name.endswith(".exe") else name


def find_by_name(name: str) -> list[int]:
    """进程名正好是 name 的进程（不分大小写、不管 .exe），不含本进程。查不了 → []。"""
    psutil = _psutil()
    want, me, out = _bare(name), os.getpid(), []
    try:
        for p in psutil.process_iter(["name"]):
            n = p.info.get("name")
            if n and _bare(n) == want and p.pid != me:
                out.append(p.pid)
    except (psutil.Error, OSError):
        return []
    return sorted(out)


# --- Windows Job Object（ctypes，不要 pywin32）---------------------------------------------------------------------

def _job_for(proc) -> int | None:                     # pragma: no cover - Windows only
    """新建一个 Job Object（KILL_ON_JOB_CLOSE），把 proc 放进去。做不到（如本进程所在的 Job 不许嵌套）→ None。
    Popen 不能让子进程先挂起，所以是子进程起来之后才放进去；我们的子进程都要过一会儿才起自己的子进程，赶得上。"""
    import ctypes
    from ctypes import wintypes
    k32 = _k32()

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    JobObjectExtendedLimitInformation, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 9, 0x2000
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
    if not ok or not k32.AssignProcessToJobObject(job, int(proc._handle)):
        k32.CloseHandle(job)
        return None
    return job


def _k32():                                            # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.GetConsoleProcessList.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD)
    k32.GetConsoleProcessList.restype = wintypes.DWORD
    k32.AttachConsole.argtypes = (wintypes.DWORD,)
    k32.GenerateConsoleCtrlEvent.argtypes = (wintypes.DWORD, wintypes.DWORD)
    k32.SetConsoleCtrlHandler.argtypes = (_handler_type(), wintypes.BOOL)
    k32.GetStdHandle.argtypes = (wintypes.DWORD,)
    k32.GetStdHandle.restype = wintypes.HANDLE
    k32.SetStdHandle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
    return k32


def _job_terminate(job) -> None:                       # pragma: no cover - Windows only
    _k32().TerminateJobObject(job, 1)


def _job_close(job) -> None:                           # pragma: no cover - Windows only
    _k32().CloseHandle(job)


# --- Windows 控制台：连着没有、挂到子进程的控制台上发 Ctrl+Break、收到 Ctrl+Break 时先跑一段 ---------------------------

CTRL_C, CTRL_BREAK, CTRL_CLOSE = 0, 1, 2
_STD_HANDLES = (-10, -11, -12)                         # STD_INPUT_HANDLE、STD_OUTPUT_HANDLE、STD_ERROR_HANDLE（DWORD 补码）
_ATTACH_LOCK = threading.Lock()
_CTRL_HANDLERS: list = []                              # ctypes 回调要一直引用着，否则会被回收


def _attached_to_console() -> bool:                    # pragma: no cover - Windows only
    from ctypes import wintypes
    buf = (wintypes.DWORD * 1)()
    return _k32().GetConsoleProcessList(buf, 1) != 0   # 没连着控制台时返回 0


def _handler_type():                                   # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes
    return ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)


def _break_via_attach(pid: int) -> bool:               # pragma: no cover - Windows only
    """本进程没有控制台、子进程有自己的（CREATE_NO_WINDOW）：先挂到子进程的控制台上，给它那一组发 Ctrl+Break，再离开。
    挂着的这一小会儿本进程不理控制台事件（万一也收到了）；标准句柄先记下、离开后放回（挂控制台会改掉它们，
    之后起的子进程要继承）。本进程自己有控制台时挂不上（一个进程只能连一个），→ False。"""
    k32 = _k32()
    swallow = _handler_type()(lambda _event: True)
    with _ATTACH_LOCK:
        if _attached_to_console():
            return False
        std = [k32.GetStdHandle(h & 0xFFFFFFFF) for h in _STD_HANDLES]
        if not k32.AttachConsole(pid):
            return False
        k32.SetConsoleCtrlHandler(swallow, True)
        try:
            return bool(k32.GenerateConsoleCtrlEvent(CTRL_BREAK, pid))
        finally:
            k32.FreeConsole()
            k32.SetConsoleCtrlHandler(swallow, False)
            for h, v in zip(_STD_HANDLES, std):
                k32.SetStdHandle(h & 0xFFFFFFFF, v)


def _add_ctrl_handler(fn):                             # pragma: no cover - Windows only
    import _thread

    def handler(event):
        signum = {CTRL_C: signal.SIGINT, CTRL_BREAK: signal.SIGBREAK, CTRL_CLOSE: signal.SIGBREAK}.get(event)
        if signum is None:
            return False
        handled = callable(signal.getsignal(signum))
        if handled:
            _thread.interrupt_main(signum)             # 主线程一醒就走它的信号处理（收尾、退出）
        try:
            fn()
        except Exception:                              # noqa: BLE001 - 这是系统起的线程，异常抛不出去
            pass
        return handled                                 # Python 没接这个信号：交给下一个处理（系统默认是结束本进程）

    h = _handler_type()(handler)
    _CTRL_HANDLERS.append(h)
    _k32().SetConsoleCtrlHandler(h, True)
    return h
