"""日常的基本件：常量、运行标记（同一时间只跑一次日常）、一次运行的记录（_Run）与停下（_Stop）。"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from maapilot import cliutil, paths
from maapilot.cliutil import BUSY, CliError
from maapilot.crew import lock as lockmod
from maapilot.flows import report as reportmod
from maapilot.flows._common import now
from maapilot.oscompat import fs
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

DAILY_ENV = "MAAPILOT_DAILY_RUN"          # 无头 agent 的环境里带着它：agent 里不能再起一次 daily run
AGENT_TIMEOUT_MIN = 90                    # 做目标部分的无头 agent 最长跑多久（config: daily.agent_timeout_min）
RECOVERY_TIMEOUT_MIN = 30                 # 接管失败项的无头 agent 最长跑多久（config: daily.recovery_timeout_min）
MAINTENANCE_AGENT_MIN = 10                # 停机维护时叫起来看一眼的无头 agent 最长跑多久
EXPIRED_AGENT_MIN = 15                    # 只有到期目标要看时叫起来的无头 agent 最长跑多久
TASK_TIMEOUT_MIN = 90                     # 一个 MAA 日常项最长跑多久（config: daily.task_timeout_min）
FARM = "farm"                             # 刷理智（不在 daily.tasks 里；--only 里可以写）
BOX = "box"                               # 识别干员练度（state box；每个游戏日一次，config: daily.read_box；--only 里可以写）
BOX_TIMEOUT_MIN = 15                      # config: daily.box_timeout_min
UPDATE = "game_update"                    # 更新游戏（汇报里的一项；会话里交给在场 agent 时 handoff.task 是它）


def _err(e: BaseException) -> str:
    return e.message if isinstance(e, CliError) else f"{type(e).__name__}: {e}"


def _marker() -> Path:
    return paths.PRIVATE / "daily-run.json"


# --- 运行标记（同一时间只跑一次日常） ---------------------------------------------------------------------------------

def _read_marker() -> dict | None:
    try:
        return json.loads(fs.read_text_utf8(_marker()))
    except (OSError, ValueError):
        return None


def running() -> dict | None:
    """正在跑的日常（标记在、持有进程还活着）；没有 → None。"""
    d = _read_marker()
    if not d:
        return None
    if d.get("host") == socket.gethostname() and d.get("pid") and not lockmod.pid_alive(int(d["pid"])):
        return None
    return d


def _take_marker(run_id: str, trigger: str) -> None:
    cur = daily.running()
    if cur:
        raise CliError("daily_running", f"日常 {cur.get('run_id')} 正在跑（{cur.get('started_at', '')[:16]} 开始）",
                       hint="等它跑完；daily status 看进度", exit=BUSY, data={"running": cur})
    _marker().parent.mkdir(parents=True, exist_ok=True)
    body = {"run_id": run_id, "trigger": trigger, "pid": os.getpid(), "host": socket.gethostname(),
            "started_at": now().isoformat(timespec="seconds"), "step": None}
    try:
        fd = os.open(_marker(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if daily.running():                                # 刚被别人抢到
            raise CliError("daily_running", "另一个日常刚开始", exit=BUSY) from None
        fs.safe_unlink(_marker())                    # 残留的标记
        fd = os.open(_marker(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)


def _set_step(step: str | None) -> None:
    d = _read_marker()
    if d and d.get("pid") == os.getpid():
        d["step"] = step
        fs.atomic_write_text(_marker(), json.dumps(d, ensure_ascii=False))


def _drop_marker() -> None:
    d = _read_marker()
    if d and d.get("pid") == os.getpid():
        fs.safe_unlink(_marker())


# --- 一次运行的记录 --------------------------------------------------------------------------------------------------

def _new_run_id() -> str:
    t = now()
    rid = f"{t:%Y%m%d-%H%M}"
    if (paths.REPORTS / f"{reportmod.stem(rid)}.json").exists():
        rid = f"{t:%Y%m%d-%H%M%S}"
    return rid


class _Run:
    """一次日常的记录：tasks、失败、告警；每一步写进 private/log/daily-<run_id>.jsonl，汇报随时可存。"""

    def __init__(self, run_id: str, trigger: str):
        self.data = {"run_id": run_id, "trigger": trigger, "started_at": now().isoformat(timespec="seconds"),
                     "finished_at": None, "status": "running", "ok": None, "tasks": [], "failed": [],
                     "goals_active": 0, "goals_pending": False, "farm": None, "depot_change": None,
                     "guard": {"ok": None}, "alerts": [], "agents": 0}
        self.events = paths.LOGS / f"daily-{run_id}.jsonl"

    def event(self, kind: str, **kw) -> None:
        try:
            self.events.parent.mkdir(parents=True, exist_ok=True)
            with open(self.events, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": now().isoformat(timespec="seconds"), "event": kind, **kw},
                                   ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def save(self) -> None:
        try:
            reportmod.save(self.data)
        except OSError as e:
            cliutil.log(f"[daily] 汇报写不了：{e}")


class _Stop(Exception):
    """对账不过：整个日常停下。"""
