"""模拟器锁：同一时间只有一个 maa / maapilot 进程操作模拟器。文件是 maapilot.paths.LOCK_FILE（private/lock.json）。

lock.json = {owner, pid, host, job, run_id, step, token, acquired_at, heartbeat_at, ttl_s}。
残留判定：heartbeat_at + ttl_s 已过，或同 host 上 pid 已不存在 → 直接接管并警告。
另外空闲时若有 `maa` 进程在跑（用户手工 `maa run`），同样视为占用。
同一进程重复 acquire 返回一个不释放的嵌套锁。
子进程继承：持锁的进程（如 `daily run`）启动子进程（`claude -p` 接管，里面再调 `maapilot screen …`）时，
用 `Lock.child_env()` 作为子进程环境：MAAPILOT_LOCK_TOKEN 对上 lock.json 的 token → 子进程得到嵌套锁，不抢、不释放；
等子进程期间父进程用 `Lock.keepalive()` 继续心跳。
注意：`maa` 进程检查（external_maa_pids）对继承的锁不生效——子进程里 `maapilot maa …` 自己起的 maa 进程不算外部。
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from maapilot import paths
from maapilot.knowledge.calendar import GAME_TZ

POLL_S = 5.0
TOKEN_ENV = "MAAPILOT_LOCK_TOKEN"


class LockBusy(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(GAME_TZ)


def _host() -> str:
    return socket.gethostname()


def external_maa_pids() -> list[int]:
    """`pgrep -x maa` 找到的进程（测试里 monkeypatch 掉）。pgrep 不可用时视为没有。"""
    try:
        out = subprocess.run(["pgrep", "-x", "maa"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in out.split() if x.strip().isdigit()]


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


def _parse(s) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def current() -> dict | None:
    """当前 lock.json 内容；没有或读不了返回 None。"""
    try:
        return json.loads(paths.LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stale_reason(d: dict, now: datetime | None = None) -> str | None:
    """锁是否残留：返回原因文本，仍有效返回 None。"""
    now = now or _now()
    hb = _parse(d.get("heartbeat_at")) or _parse(d.get("acquired_at"))
    ttl = d.get("ttl_s") or 900
    if hb is None:
        return "锁文件没有时间戳"
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=GAME_TZ)
    if hb + timedelta(seconds=ttl) < now:
        return f"心跳已过期（{hb:%m-%d %H:%M} + {ttl}s）"
    if d.get("host") == _host() and d.get("pid") and not pid_alive(int(d["pid"])):
        return f"持有进程 {d['pid']} 已不存在"
    return None


def holder(now: datetime | None = None) -> dict | None:
    """现在谁占着模拟器（只读：不接管残留、不写文件、不打印）：有效的锁 → lock.json 内容；
    否则有外部 maa 进程 → {"external_maa_pids": [...]}；否则 None。"""
    d = current()
    if d is not None and stale_reason(d, now) is None:
        return d
    pids = external_maa_pids()
    return {"external_maa_pids": pids} if pids else None


def _describe(d: dict) -> str:
    what = d.get("job") or d.get("owner") or "?"
    step = f" 步骤 {d['step']}" if d.get("step") else ""
    return f"{what}{step}（pid {d.get('pid')}@{d.get('host')}，{d.get('acquired_at', '')[:16]} 起）"


def _write(data: dict, exclusive: bool) -> bool:
    paths.LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if exclusive:
        try:
            fd = os.open(paths.LOCK_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    tmp = paths.LOCK_FILE.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, paths.LOCK_FILE)
    return True


class Lock:
    def __init__(self, data: dict, nested: bool = False):
        self.data = data
        self.nested = nested
        self.released = False

    def _mine(self, d: dict | None) -> bool:
        return bool(d) and d.get("pid") == self.data["pid"] and d.get("host") == self.data["host"] \
            and d.get("acquired_at") == self.data["acquired_at"] and d.get("token") == self.data.get("token")

    def heartbeat(self, step: str | None = None) -> None:
        if self.released:
            return
        d = current()
        if not self._mine(d):
            if self.nested:
                return
            print(f"警告：锁已不在本进程手里（{_describe(d) if d else '锁文件不见了'}），重新写入", file=sys.stderr)
        self.data["heartbeat_at"] = _now().isoformat(timespec="seconds")
        if step is not None:
            self.data["step"] = step
        _write(self.data, exclusive=False)

    def release(self) -> None:
        if self.released or self.nested:
            self.released = True
            return
        self.released = True
        if self._mine(current()):
            try:
                paths.LOCK_FILE.unlink()
            except FileNotFoundError:
                pass

    def rotate_token(self) -> str:
        """换一个新 token 写进 lock.json，返回它（给以后的子进程用）。拿着旧 token 的子进程再拿锁就是"被占用"，
        它们手里的嵌套锁也不会再把旧内容写回来。只有持锁的进程能换。"""
        if self.nested or self.released:
            raise RuntimeError("只有持锁的进程能换 token")
        self.data["token"] = secrets.token_hex(8)
        self.data["heartbeat_at"] = _now().isoformat(timespec="seconds")
        _write(self.data, exclusive=False)
        return self.data["token"]

    def child_env(self) -> dict:
        """子进程的环境变量：带上本锁的 token，子进程里的 maapilot 命令拿到嵌套锁。"""
        return {**os.environ, TOKEN_ENV: self.data.get("token", "")}

    @contextmanager
    def keepalive(self, interval_s: float = 60.0):
        """在 with 块里后台定时心跳（等子进程时用）。"""
        stop = threading.Event()

        def beat():
            while not stop.wait(interval_s):
                try:
                    self.heartbeat()
                except OSError:
                    pass
        t = threading.Thread(target=beat, daemon=True)
        t.start()
        try:
            yield self
        finally:
            stop.set()
            t.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def acquire(owner: str, *, job: str | None = None, run_id: str | None = None, wait_s: float = 0,
            ttl_s: int = 900) -> Lock:
    """拿锁。占用中且未过期 → 每 POLL_S 秒重试到 wait_s，仍占用抛 LockBusy。"""
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        busy = _try_acquire(owner, job, run_id, ttl_s)
        if isinstance(busy, Lock):
            return busy
        if time.monotonic() >= deadline:
            raise LockBusy(busy)
        time.sleep(min(POLL_S, max(0.05, deadline - time.monotonic())))


def _busy(d: dict | None, now: datetime) -> str | None:
    """占用判定（不含同进程重入）：有效的锁、外部 maa 进程 → 原因；残留的锁打印警告后视为空闲。"""
    if d is not None:
        why = stale_reason(d, now)
        if why is None:
            return f"模拟器被占用：{_describe(d)}"
        print(f"警告：接管残留的锁 {_describe(d)}：{why}", file=sys.stderr)
    pids = external_maa_pids()
    if pids:
        return f"有不归 MaaPilot 管的 maa 进程在跑（pid {', '.join(map(str, pids))}），等它结束"
    return None


def _try_acquire(owner, job, run_id, ttl_s) -> Lock | str:
    now = _now()
    d = current()
    if d is not None and d.get("host") == _host() and d.get("pid") == os.getpid() and d.get("owner") == owner:
        return Lock(d, nested=True)                       # 同进程同持有者重入；不同持有者照常视为占用
    tok = os.environ.get(TOKEN_ENV)
    if d is not None and tok and d.get("token") == tok and stale_reason(d, now) is None:
        return Lock(d, nested=True)                       # 父进程的锁（子进程继承）
    busy = _busy(d, now)
    if busy:
        return busy
    stamp = now.isoformat(timespec="seconds")
    data = {"owner": owner, "pid": os.getpid(), "host": _host(), "job": job, "run_id": run_id, "step": None,
            "token": secrets.token_hex(8), "acquired_at": stamp, "heartbeat_at": stamp, "ttl_s": ttl_s}
    if d is None:
        if not _write(data, exclusive=True):
            return "模拟器被占用：另一个进程刚拿到锁"
    else:
        _write(data, exclusive=False)
    return Lock(data)


@contextmanager
def hold(owner: str, **kw):
    lk = acquire(owner, **kw)
    try:
        yield lk
    finally:
        lk.release()
