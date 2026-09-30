"""flows 共用：命令记录（private/log/<日期>.jsonl）、快捷操作的执行外壳。"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from maapilot import cliutil, paths
from maapilot.cliutil import BAD_REQUEST, ENVIRONMENT, FAILED, CliError


def now() -> datetime:
    return datetime.now().astimezone()


def rel(p) -> str | None:
    """给输出用的路径：在仓库里的写成相对仓库根目录的，其余原样。"""
    if p is None:
        return None
    p = Path(p)
    try:
        return str(p.resolve().relative_to(paths.ROOT))
    except ValueError:
        return str(p)


def journal(command: str, args: dict, ok: bool, exit: int, duration_s: float, **extra) -> None:
    """碰模拟器的命令结束时在 private/log/<日期>.jsonl 追加一行。写不了不影响命令本身。"""
    t = now()
    row = {"at": t.isoformat(timespec="seconds"), "command": command, "args": args, "ok": ok, "exit": exit,
           "duration_s": round(duration_s, 1), **{k: v for k, v in extra.items() if v is not None}}
    try:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        with open(paths.LOGS / f"{t:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def game_data_error(e: Exception) -> CliError:
    return CliError("no_game_data", f"读不到游戏数据：{e}", hint="bin/maapilot data sync（或 doctor --fix）",
                    exit=ENVIRONMENT)


def check_operator(name: str) -> dict:
    """干员名 → 游戏数据条目；认不出 → 退出码 2 unknown_name。"""
    from maapilot.knowledge import gamedata
    try:
        ops = gamedata.operators()
    except (OSError, ValueError) as e:
        raise game_data_error(e) from None
    if name not in ops:
        near = [n for n in ops if name and (name in n or n in name)][:5]
        raise CliError("unknown_name", f"认不出干员「{name}」", hint="用游戏里的中文全名", exit=BAD_REQUEST,
                       data={"suggestions": near})
    return ops[name]


def depot_names() -> dict[str, int]:
    """state depot 快照 → {中文名: 数量}；没有快照 → 退出码 2 no_snapshot。"""
    from maapilot.knowledge.items import display_name
    p = paths.STATE / "depot.json"
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CliError("no_snapshot", "没有仓库快照，算不了要合成什么", hint="先 bin/maapilot state depot",
                       exit=BAD_REQUEST) from None
    return {display_name(str(k)): int(v) for k, v in (snap.get("items") or {}).items()}


class Progress:
    """快捷操作做到哪一步（失败时放进 data.stage），做成了什么（data.done）。"""

    def __init__(self, stage: str = "开始"):
        self.stage = stage
        self.done: list = []


def run_shortcut(command: str, args: dict, shot_name: str, body, *, needs_game: bool = True) -> dict:
    """拿锁 → 连模拟器（needs_game 时）→ body(g, progress) → 返回 data。
    body 抛出的非 CliError 异常 → 退出码 1 shortcut_failed，data 带 stage / shot / done（异常上的 reached、made 等也带上）。"""
    t0 = time.monotonic()
    code = 0
    try:
        with cliutil.device(command):
            pr = Progress()
            g = None
            if needs_game:
                from maapilot.crew.core import Game
                try:
                    g = Game()
                except Exception as e:                  # noqa: BLE001 - 连不上模拟器是环境问题
                    raise CliError("device_failed", f"连不上模拟器：{e}", hint="bin/maapilot doctor",
                                   exit=ENVIRONMENT) from None
            try:
                return body(g, pr)
            except CliError:
                raise
            except Exception as e:                      # noqa: BLE001 - 快捷操作失败都交给 agent 在界面上做
                shot = getattr(e, "shot", None)
                if shot is None and g is not None:
                    try:
                        shot = g.save(shot_name)
                    except Exception:                   # noqa: BLE001
                        shot = None
                data = {"stage": pr.stage, "shot": rel(shot), "done": pr.done or None}
                for k in ("reached", "made", "pending", "switched", "restored", "original", "original_count",
                          "balance"):
                    if getattr(e, k, None) is not None:
                        data[k] = getattr(e, k)
                raise CliError("shortcut_failed", f"{type(e).__name__}: {e}" if not str(e) else str(e),
                               hint="快捷操作没做完：先 screen shot 看看停在哪，再在界面上接着做",
                               exit=FAILED, data=data) from e
    except CliError as e:
        code = e.exit
        raise
    finally:
        journal(command, args, code == 0, code, time.monotonic() - t0)
