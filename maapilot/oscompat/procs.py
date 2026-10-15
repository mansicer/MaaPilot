"""进程：起子进程、结束整棵进程树、查父进程链 / 子孙进程、按名字找进程、UTF-8 地跑子进程。

macOS / Linux：子进程自成进程组（start_new_session），结束时整组先 TERM 再 KILL。
Windows：没有进程组和 SIGTERM。子进程用 CREATE_NEW_PROCESS_GROUP 起（能单独收到 Ctrl+Break），再放进一个
Job Object（KILL_ON_JOB_CLOSE：我们自己死了它也跟着走）；结束时先发 Ctrl+Break、等一会儿，再整个 Job 结束掉。
进程表一律用 psutil（三个系统一套代码，create_time 防 pid 被复用）。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Iterable

from maapilot.oscompat import WINDOWS

# 叫停 maapilot 的信号：Windows 没有 SIGHUP，有 SIGBREAK（Ctrl+Break，CREATE_NEW_PROCESS_GROUP 起的子进程只收得到它）
STOP_SIGNALS: tuple[signal.Signals, ...] = tuple(
    s for s in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None),
                getattr(signal, "SIGBREAK", None) if sys.platform == "win32" else None) if s is not None)

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _psutil():
    import psutil                       # 用到才导入：maacore_runner 之类的小进程不用它
    return psutil


def child_env(extra: dict | None = None) -> dict:
    """子进程的环境：本进程的环境 + PYTHONUTF8=1（子进程里的 Python 读写一律 UTF-8）+ extra。"""
    return {**os.environ, "PYTHONUTF8": "1", **(extra or {})}


def has_console() -> bool:
    """本进程有没有控制台窗口（Windows 上 pythonw / 任务计划程序起的没有）。macOS / Linux 一律 True。"""
    if not WINDOWS:
        return True
    import ctypes                                               # pragma: no cover - Windows only
    return bool(ctypes.windll.kernel32.GetConsoleWindow())      # pragma: no cover


def _creationflags(group: bool) -> int:
    flags = CREATE_NEW_PROCESS_GROUP if group else 0
    if not has_console():
        flags |= CREATE_NO_WINDOW                    # 不然每个控制台程序都弹一个黑窗口
    return flags


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
        kw["creationflags"] = kw.get("creationflags", 0) | _creationflags(group)
    else:
        kw.setdefault("start_new_session", group)
    proc = subprocess.Popen(argv, **kw)
    proc._maapilot_group = group
    proc._maapilot_job = _job_for(proc) if WINDOWS else None
    return proc


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
    最后子孙的组一律再 KILL 一次（主进程收到 TERM 就走了也一样）。Windows：Ctrl+Break → 等 grace_s → 结束整个 Job → 等。
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
    left = []
    for pid, (_, cmd) in seen.items():                # 先拿住 Process 对象：之后 kill 时 psutil 会核对 pid 没被复用
        try:
            p = psutil.Process(pid)
            if _cmdline(p) == cmd:
                left.append(p)
        except (psutil.Error, OSError):
            pass
    job = getattr(proc, "_maapilot_job", None)
    if proc.poll() is None and getattr(proc, "_maapilot_group", True):
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            proc.wait(timeout=grace_s)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if job is not None:
        _job_terminate(job)
    elif proc.poll() is None:
        proc.kill()
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
    return k32


def _job_terminate(job) -> None:                       # pragma: no cover - Windows only
    _k32().TerminateJobObject(job, 1)


def _job_close(job) -> None:                           # pragma: no cover - Windows only
    _k32().CloseHandle(job)
