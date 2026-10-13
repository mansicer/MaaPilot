"""子进程与无头 agent：调 maapilot 命令（maapilot_call）、起无头 claude -p（run_agent）、agent 结束后收拾留下的 maa。"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from maapilot import cliutil, paths
from maapilot.cliutil import FAILED
from maapilot.crew import lock as lockmod
from maapilot.flows._common import rel
from maapilot.flows.daily.base import AGENT_TIMEOUT_MIN, RECOVERY_TIMEOUT_MIN, _Run
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

# --- 子进程 ----------------------------------------------------------------------------------------------------------

def _run_group(cmd: list[str], *, env: dict, timeout_s: float | None, stdout=subprocess.PIPE,
               cwd: Path | None = None) -> tuple[int | None, str, bool]:
    """跑一个子进程（自成进程组）；超时 → 先 TERM 再 KILL 整个进程组。→ (退出码或 None, stdout 文本, 是否超时)。"""
    p = subprocess.Popen(cmd, cwd=str(cwd or paths.ROOT), env=env, stdout=stdout, stderr=None,
                         stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                         start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout_s)
        return p.returncode, out or "", False
    except subprocess.TimeoutExpired:
        for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
            try:
                os.killpg(p.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                out, _ = p.communicate(timeout=wait)
                return p.returncode, out or "", True
            except subprocess.TimeoutExpired:
                continue
        return None, "", True


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


# --- 无头 agent ------------------------------------------------------------------------------------------------------

def _claude_bin(cfg: dict) -> str:
    c = str((cfg.get("tools") or {}).get("claude") or "claude")
    return shutil.which(c) or c


def agent_command(claude: str, prompt: str) -> list[str]:
    """无头 agent 的命令行：要弹确认的一律自动拒绝。显式带上项目的权限设置（--settings）：
    文件夹没被 Claude Code 信任过时，无头 claude 不读项目的 .claude/settings.json。"""
    return [claude, "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--settings", str(paths.ROOT / ".claude" / "settings.json"),
            "--permission-mode", "default", "--permission-prompts", "none"]


_OUTCOME = re.compile(r"结果[：:]\s*(完成|没做成|做完|失败|处理好了|没处理好)")


def parse_transcript(path: Path) -> dict:
    """stream-json 输出里最后一条 result：总结、出错没有、花费。"""
    res = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "result":
                    res = ev
    except OSError:
        pass
    if res is None:
        return {"summary": None, "is_error": True}
    summary = (res.get("result") or "").strip() or None
    m = None
    for m in _OUTCOME.finditer(summary or ""):
        pass
    recovered = None if m is None else m.group(1) in ("完成", "做完", "处理好了")
    return {"summary": summary, "is_error": bool(res.get("is_error")), "recovered": recovered,
            "cost_usd": res.get("total_cost_usd"), "turns": res.get("num_turns")}


def run_agent(cfg: dict, prompt: str, env: dict, transcript: Path, *, recovery: bool = False,
              timeout_min: float | None = None) -> dict:
    """起一个无头 claude -p 并等它结束。→ {ok, summary, recovered, timed_out, transcript, duration_s, …}。"""
    dcfg = cfg.get("daily") or {}
    if timeout_min:
        timeout_s = float(timeout_min) * 60
    elif recovery:
        timeout_s = float(dcfg.get("recovery_timeout_min") or RECOVERY_TIMEOUT_MIN) * 60
    else:
        timeout_s = float(dcfg.get("agent_timeout_min") or AGENT_TIMEOUT_MIN) * 60
    transcript.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    cmd = agent_command(_claude_bin(cfg), prompt)
    cliutil.log(f"[daily] 叫无头 agent（最长 {int(timeout_s // 60)} 分钟），记录在 {rel(transcript)}")
    try:
        with open(transcript, "w", encoding="utf-8") as out:
            code, _, timed_out = _run_group(cmd, env=env, timeout_s=timeout_s, stdout=out)
    except OSError as e:
        return {"ok": False, "error": f"起不来 claude（{cmd[0]}）：{e}", "recovered": False,
                "transcript": rel(transcript), "duration_s": 0}
    info = parse_transcript(transcript)
    info.update(ok=(code == 0 and not timed_out and not info.get("is_error")), exit=code, timed_out=timed_out,
                transcript=rel(transcript), duration_s=round(time.monotonic() - t0, 1))
    if timed_out:
        info["recovered"] = False
    return info


# --- agent 留下的 maa ------------------------------------------------------------------------------------------------

LEFTOVER_WAIT_S = 60


def _leftover_maa() -> list[int]:
    """日常拿着锁、自己没在跑 maa 时还在的 maa 进程：只能是 agent 留下的。"""
    return lockmod.external_maa_pids()


def _after_agent(r: _Run, env: dict, lk) -> None:
    """无头 agent 结束（或超时被杀）之后：换锁的 token，它留下还在跑的 maapilot 命令不能再进来；
    还有 maa 进程在跑（agent 起的，没随它一起结束）就等一会儿，还不走就结束掉。"""
    try:
        env[lockmod.TOKEN_ENV] = lk.rotate_token()
    except Exception as e:                               # noqa: BLE001
        cliutil.log(f"[daily] 换锁 token 失败：{e}")
    _stop_leftover_maa(r, daily.LEFTOVER_WAIT_S, "agent 结束后")


LEFTOVER_KILL_GRACE_S = 10
TIMEOUT_LEFTOVER_WAIT_S = 20     # 任务超时、maapilot maa 被杀之后，给 maa 多久自己跟着退


def _stop_leftover_maa(r: _Run, wait_s: float, when: str) -> list[int]:
    """日常拿着锁、自己没在跑 maa 时还在的 maa 进程：等 wait_s 秒让它自己走，还在就 TERM，再不走就 KILL。返回结束掉的。"""
    deadline = time.monotonic() + wait_s
    pids = daily._leftover_maa()
    while pids and time.monotonic() < deadline:
        time.sleep(min(5, max(0.1, deadline - time.monotonic())))
        pids = daily._leftover_maa()
    if not pids:
        return []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        end = time.monotonic() + LEFTOVER_KILL_GRACE_S
        while time.monotonic() < end and set(daily._leftover_maa()) & set(pids):
            time.sleep(0.5)
        if not set(daily._leftover_maa()) & set(pids):
            break
    r.data["alerts"].append(f"{when}还有 maa 进程在跑（pid {', '.join(map(str, pids))}），已结束掉")
    r.event("leftover_maa", pids=pids, when=when)
    return pids
