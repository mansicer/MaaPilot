"""子进程与无头 agent：调 maapilot 命令（maapilot_call）、起无头 agent（run_agent，claude -p 或 codex exec，见 maapilot.hosts）、
agent 结束后收拾它留下的 maa、maapilot 命令和 maacore_runner。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from maapilot import cliutil, hosts, paths
from maapilot.cliutil import FAILED
from maapilot.crew import lock as lockmod
from maapilot.flows._common import rel
from maapilot.oscompat import procs
from maapilot.flows.daily.base import AGENT_TIMEOUT_MIN, RECOVERY_TIMEOUT_MIN, _Run
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

# --- 子进程 ----------------------------------------------------------------------------------------------------------

def _self_and_ancestors() -> set[int]:
    """日常自己和它的祖先进程：收拾 agent 留下的进程时绝不能碰。"""
    return {os.getpid(), *procs.ancestors()}


TRACK_EVERY_S = 3                 # 跑无头 agent 时每隔几秒记一次它的子孙（它自己一结束，子孙就挂到 1 号进程下，找不回来了）


class _Tracker:
    """后台定时记下一个进程的子孙（pid → (进程组, 命令行)，见 procs.descendants），结束后用来收拾它留下的进程。"""

    def __init__(self, pid: int, every_s: float | None = None):
        self.pid, self.every_s, self.seen = pid, every_s or TRACK_EVERY_S, {}
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def sample(self) -> None:
        try:
            self.seen.update(procs.descendants(self.pid))
        except Exception:                               # noqa: BLE001 - 记不到就算了
            pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.every_s)

    def stop(self) -> dict[int, tuple[int, str]]:
        self._stop.set()
        self._t.join(timeout=15)
        return dict(self.seen)


def _run_group(cmd: list[str], *, env: dict, timeout_s: float | None, stdout=subprocess.PIPE,
               cwd: Path | None = None, track: dict | None = None, input: str | None = None
               ) -> tuple[int | None, str, bool]:
    """跑一个子进程（自成进程组，见 procs.spawn）；超时 → 结束整棵进程树，连同子孙另起的进程组（procs.kill_tree：
    先 TERM 等 15 秒，再 KILL 等 5 秒，子孙的组最后一律再 KILL）。track 给一个 dict：跑的过程中定时把子孙进程
    （pid → (进程组, 命令行)）记进去。input：写进子进程 stdin 的文字（不给就接空）。
    → (退出码或 None, stdout 文本, 是否超时)。"""
    p = procs.spawn(cmd, cwd=str(cwd or paths.ROOT), env=env, stdout=stdout, stderr=None,
                    stdin=subprocess.DEVNULL if input is None else subprocess.PIPE)
    tracker = _Tracker(p.pid) if track is not None else None
    try:
        try:
            out, _ = p.communicate(input, timeout=timeout_s)
            return p.returncode, out or "", False
        except subprocess.TimeoutExpired:
            pass
        if tracker:
            tracker.sample()
        code = procs.kill_tree(p, 15, kill_wait_s=5, also=tracker.seen if tracker else None)
        try:
            out, _ = p.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError):
            out = ""
        return (code if code is not None else p.returncode), out or "", True
    finally:
        if tracker:
            seen = tracker.stop()
            track.update(seen)


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


_LAST_AGENT: dict = {"descendants": {}}       # 最近一个无头 agent 跑的时候记下的子孙进程

# --- 无头 agent ------------------------------------------------------------------------------------------------------

def _claude_bin(cfg: dict) -> str:
    return hosts.binary(cfg, "claude")


def agent_command(claude: str, prompt: str) -> list[str]:
    """无头 claude 的命令行（见 maapilot.hosts.command）。"""
    return hosts.command("claude", claude, prompt, Path(os.devnull))


_OUTCOME = re.compile(r"结果[：:]\s*(完成|没做成|做完|失败|处理好了|没处理好)")


def parse_transcript(path: Path, host: str = "claude") -> dict:
    """agent 的输出记录：总结、出错没有、花费，加上总结里"结果：…"那行算出的 recovered。"""
    info = hosts.parse(host, path)
    m = None
    for m in _OUTCOME.finditer(info.get("summary") or ""):
        pass
    info["recovered"] = None if m is None else m.group(1) in ("完成", "做完", "处理好了")
    return info


def run_agent(cfg: dict, prompt: str, env: dict, transcript: Path, *, recovery: bool = False,
              timeout_min: float | None = None) -> dict:
    """起一个无头 agent（config.yaml 的 agent：claude / codex）并等它结束。
    → {ok, host, summary, recovered, timed_out, transcript, duration_s, …}。"""
    dcfg = cfg.get("daily") or {}
    if timeout_min:
        timeout_s = float(timeout_min) * 60
    elif recovery:
        timeout_s = float(dcfg.get("recovery_timeout_min") or RECOVERY_TIMEOUT_MIN) * 60
    else:
        timeout_s = float(dcfg.get("agent_timeout_min") or AGENT_TIMEOUT_MIN) * 60
    transcript.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    host = hosts.name(cfg)
    cmd = hosts.command(host, hosts.binary(cfg, host), prompt, transcript)
    cliutil.log(f"[daily] 叫无头 agent（最长 {int(timeout_s // 60)} 分钟），记录在 {rel(transcript)}")
    seen: dict = {}
    _LAST_AGENT["descendants"] = seen                  # agent 结束后收拾它留下的进程（_after_agent）
    try:
        with open(transcript, "w", encoding="utf-8") as out:
            code, _, timed_out = _run_group(cmd, env=env, timeout_s=timeout_s, stdout=out, track=seen)
    except OSError as e:
        return {"ok": False, "host": host, "error": f"起不来 {host}（{cmd[0]}）：{e}", "recovered": False,
                "transcript": rel(transcript), "duration_s": 0}
    info = parse_transcript(transcript, host)
    info.update(host=host, ok=(code == 0 and not timed_out and not info.get("is_error")), exit=code, timed_out=timed_out,
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
    还有 maa 进程在跑（agent 起的，没随它一起结束）就等一会儿，还不走就结束掉；
    它起的 maapilot 命令、maacore_runner 还在（按它跑的时候记下的子孙认，不按命令行大范围找）也结束掉。"""
    try:
        env[lockmod.TOKEN_ENV] = lk.rotate_token()
    except Exception as e:                               # noqa: BLE001
        cliutil.log(f"[daily] 换锁 token 失败：{e}")
    _stop_leftover_maa(r, daily.LEFTOVER_WAIT_S, "agent 结束后")
    seen, _LAST_AGENT["descendants"] = _LAST_AGENT.get("descendants") or {}, {}
    _stop_leftover_procs(r, seen, "agent 结束后")


_LEFTOVER_CMD = re.compile(r"(?:^|[\s/\\])maapilot(?:\.exe)?(?:\s|$)|-m\s+maapilot\.cli\b|maapilot\.crew\.maacore_runner\b",
                           re.IGNORECASE if os.name == "nt" else 0)


def _leftover_procs(seen: dict) -> list[int]:
    """agent 跑的时候记下的子孙里，现在还在的 maapilot 命令和 maacore_runner：pid 和命令行都对得上（pid 没被别的进程复用），
    不是日常自己或它的祖先。"""
    if not seen:
        return []
    mine = _self_and_ancestors()
    return sorted(pid for pid, (_, cmd) in seen.items()
                  if pid not in mine and _LEFTOVER_CMD.search(cmd or "") and procs.cmdline(pid) == cmd)


def _stop_leftover_procs(r: _Run, seen: dict, when: str) -> list[int]:
    """agent 起的、它结束后还在跑的 maapilot 命令和 maacore_runner：TERM，再不走就 KILL。返回结束掉的。"""
    try:
        pids = _leftover_procs(seen)
    except Exception as e:                               # noqa: BLE001
        cliutil.log(f"[daily] 查 agent 留下的进程失败：{e}")
        return []
    if not pids:
        return []
    procs.kill_pids(pids, LEFTOVER_KILL_GRACE_S)
    r.data["alerts"].append(f"{when}还有 agent 起的 maapilot 进程在跑（pid {', '.join(map(str, pids))}），已结束掉")
    r.event("leftover_procs", pids=pids, when=when)
    return pids


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
    procs.kill_pids(pids, LEFTOVER_KILL_GRACE_S)
    r.data["alerts"].append(f"{when}还有 maa 进程在跑（pid {', '.join(map(str, pids))}），已结束掉")
    r.event("leftover_maa", pids=pids, when=when)
    return pids
