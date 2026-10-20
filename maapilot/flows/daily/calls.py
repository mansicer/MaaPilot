"""子进程：调 maapilot 命令（maapilot_call，自成进程组，超时结束整棵进程树）；任务超时后还在的 maa 进程收拾掉。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import FAILED
from maapilot.crew import lock as lockmod
from maapilot.oscompat import procs
from maapilot.flows.daily.base import _Run
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

# --- 子进程 ----------------------------------------------------------------------------------------------------------

def _run_group(cmd: list[str], *, env: dict, timeout_s: float | None, stdout=subprocess.PIPE,
               cwd: Path | None = None, input: str | None = None) -> tuple[int | None, str, bool]:
    """跑一个子进程（自成进程组，见 procs.spawn）；超时 → 结束整棵进程树（procs.kill_tree：先 TERM 等 15 秒，再 KILL 等 5 秒）。
    input：写进子进程 stdin 的文字（不给就接空）。→ (退出码或 None, stdout 文本, 是否超时)。"""
    p = procs.spawn(cmd, cwd=str(cwd or paths.ROOT), env=env, stdout=stdout, stderr=None,
                    stdin=subprocess.DEVNULL if input is None else subprocess.PIPE)
    try:
        out, _ = p.communicate(input, timeout=timeout_s)
        return p.returncode, out or "", False
    except subprocess.TimeoutExpired:
        pass
    code = procs.kill_tree(p, 15, kill_wait_s=5)
    try:
        out, _ = p.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError):
        out = ""
    return (code if code is not None else p.returncode), out or "", True


def maapilot_call(args: list[str], env: dict, timeout_s: float | None = None) -> tuple[int, dict]:
    """调一条 maapilot 命令（同一个 Python、同一份代码），返回 (退出码, 信封)。测试里替换掉。"""
    cmd = [sys.executable, "-m", "maapilot.cli", *args]
    code, out, timed_out = _run_group(cmd, env=env, timeout_s=timeout_s)
    if timed_out:
        return FAILED, {"ok": False, "data": None,
                        "error": {"code": "timeout", "message": f"超过 {int((timeout_s or 0) // 60)} 分钟没结束，已停下"}}
    try:
        env_ = json.loads(out)
        if not isinstance(env_, dict):
            raise ValueError
    except ValueError:
        env_ = {"ok": False, "data": None,
                "error": {"code": "bad_output", "message": (out or "").strip()[-300:] or "没有输出"}}
    return (code if code is not None else FAILED), env_


# --- 任务超时后留下的 maa ----------------------------------------------------------------------------------------------

LEFTOVER_KILL_GRACE_S = 10
TIMEOUT_LEFTOVER_WAIT_S = 20     # 任务超时、maapilot maa 被杀之后，给 maa 多久自己跟着退


def _leftover_maa() -> list[int]:
    """日常拿着锁、自己没在跑 maa 时还在的 maa 进程。"""
    return lockmod.external_maa_pids()


def _stop_leftover_maa(r: _Run, wait_s: float, when: str) -> list[int]:
    """日常拿着锁、自己没在跑 maa 时还在的 maa 进程：等 wait_s 秒让它自己走，还在就 TERM，再不走就 KILL。返回结束掉的。"""
    deadline = time.monotonic() + wait_s
    pids = daily._leftover_maa()
    while pids and time.monotonic() < deadline:
        time.sleep(min(5, max(0.1, deadline - time.monotonic())))
        pids = daily._leftover_maa()
    if not pids:
        return []
    daily.procs.kill_pids(pids, LEFTOVER_KILL_GRACE_S)
    r.data["alerts"].append(f"{when}还有 maa 进程在跑（pid {', '.join(map(str, pids))}），已结束掉")
    r.event("leftover_maa", pids=pids, when=when)
    return pids
