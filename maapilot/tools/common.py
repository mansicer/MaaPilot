"""tools/ 各命令共用的小东西：命令记录（private/log/<日期>.jsonl）、JSON 文件读写、连模拟器、时间。

命令记录：碰模拟器的命令结束时追加一行 `{at, command, args, ok, exit, duration_s, result?, run_id?, depot_change?}`。
处理函数用 `@logged` 包一下即可（flows/ 的命令也可以用）；`run_id` 取环境变量 MAAPILOT_RUN_ID（`daily run` 给子进程设）。
`result` 是关键结果的摘要（log_summary.summarize）。没包 `@logged` 的命令失败（含参数写错）或有摘要时，由 cli 补记一行。
森空岛 token、uid、昵称不进记录（log_summary.scrub_args）。
"""
from __future__ import annotations

import functools
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import FAILED, CliError

RUN_ID_ENV = "MAAPILOT_RUN_ID"
_SKIP_ARGS = {"func", "command", "accepts_extra", "group", "sub"}


def now() -> datetime:
    return datetime.now().astimezone()


def iso(t: datetime | None = None) -> str:
    return (t or now()).isoformat(timespec="seconds")


def parse_iso(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


def age_min(at: str | None) -> int | None:
    t = parse_iso(at)
    return None if t is None else max(0, int((now() - t).total_seconds() // 60))


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    """临时文件 + 替换，不留半截文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def rel(path: Path | str) -> str:
    """给输出用的路径：绝对路径（agent 用读图、读文件工具要绝对路径）。"""
    return str(Path(path).resolve())


# --- 命令记录 --------------------------------------------------------------------------

_recorded: set[str] = set()     # 本进程里已经记过的命令（cli 据此决定要不要补记）


def recorded(command: str) -> bool:
    return command in _recorded


def record(command: str, args: dict, *, ok: bool, exit: int, duration_s: float, **extra) -> None:
    """往 private/log/<日期>.jsonl 追加一行；写不了不影响命令本身。参数里的 token、uid 等不记。"""
    from maapilot.tools.log_summary import safe_text, scrub_args
    _recorded.add(command)
    if extra.get("message") is not None:
        extra["message"] = safe_text(command, extra["message"])
    row = {"at": iso(), "command": command, "args": scrub_args(args), "ok": ok, "exit": exit,
           "duration_s": round(duration_s, 1)}
    if os.environ.get(RUN_ID_ENV):
        row["run_id"] = os.environ[RUN_ID_ENV]
    row.update({k: v for k, v in extra.items() if v is not None})
    try:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        with open(paths.LOGS / f"{now():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def _args_dict(args) -> dict:
    return {k: v for k, v in vars(args).items() if k not in _SKIP_ARGS and v is not None and v is not False}


def logged(handler):
    """包住碰模拟器的处理函数：结束时记一行（带 `result` 摘要）。处理函数可以在返回的 data 里放 `_log`
    （额外字段，如 depot_change），它会被取出记进日志、不进输出。"""
    @functools.wraps(handler)
    def wrapper(args):
        from maapilot.tools.log_summary import summarize
        t0 = time.monotonic()
        command = getattr(args, "command", "?")
        try:
            data = handler(args)
        except CliError as e:
            extra = (e.data or {}).pop("_log", None) if isinstance(e.data, dict) else None
            record(command, _args_dict(args), ok=False, exit=e.exit, duration_s=time.monotonic() - t0,
                   error=e.code, message=e.message, result=summarize(command, e.data), **(extra or {}))
            raise
        except Exception as e:
            record(command, _args_dict(args), ok=False, exit=FAILED, duration_s=time.monotonic() - t0,
                   error=f"{type(e).__name__}: {e}")
            raise
        extra = data.pop("_log", None) if isinstance(data, dict) else None
        record(command, _args_dict(args), ok=True, exit=0, duration_s=time.monotonic() - t0,
               result=summarize(command, data), **(extra or {}))
        return data
    return wrapper


# --- 模拟器 -----------------------------------------------------------------------------

def ensure_device() -> None:
    """配置设备连接（`cliutil.device()` 已经做过时什么也不做）。给不经命令行、直接调函数的地方（daily run）用。"""
    from maapilot import config
    from maapilot.crew import device as dev
    try:
        dev.current()
    except dev.NotConfigured:
        dev.configure(config.connection())


def connect(*, ocr: bool = False, readonly: bool = False):
    """连模拟器（MaaFramework）。readonly=True 不开输入服务、不能点（截图、识字用，不拿锁时只能这样连）。
    测试里替换掉。失败 → CliError(screen_failed, 退出码 5)。"""
    from maapilot.cliutil import ENVIRONMENT
    try:
        from maapilot.crew.core import Game
        return Game(debug=False, ocr=ocr, readonly=readonly)
    except Exception as e:                           # adb 连不上、分辨率不对、资源缺失
        raise CliError("screen_failed", f"连不上模拟器或截不了图：{type(e).__name__}: {e}",
                       hint="maapilot doctor", exit=ENVIRONMENT) from None


def public_lock(d: dict | None) -> dict | None:
    return None if d is None else {k: v for k, v in d.items() if k != "token"}


@contextmanager
def device(owner: str, *, job: str | None = None):
    """同 `cliutil.device()`（读配置、连设备、拿锁），占用时在 CliError 的 data 里带上 `holder`（谁占着）。"""
    from maapilot import cliutil
    from maapilot.crew import lock
    try:
        with cliutil.device(owner, job=job) as lk:
            yield lk
    except CliError as e:
        if e.code == "busy" and e.data is None:
            e.data = {"holder": public_lock(lock.holder())}
        raise
