"""定时日常里不做的事：只有记下花合成玉 / 源石的同意（`guard allow`）。

其他操作都以用户在对话里的同意为准，程序不另设关卡（AGENTS.md、docs/design.md「审批与权限」）。
花费的同意必须是用户亲口说的：定时日常叫起的无头 agent 里用户不在场，一律拒绝（退出码 3），什么都不做。
判断"是不是定时日常叫起的"：环境里有 MAAPILOT_DAILY_RUN（有就算，空的也算），或者这条命令是正在跑的日常进程的子孙
（agent 把环境变量清掉也认得出来）。
"""
from __future__ import annotations

import os

from maapilot.cliutil import NEEDS_USER, CliError
from maapilot.oscompat import procs


def _unattended() -> str | None:
    """定时日常的运行编号（看不出编号时给 "?"）；不是 → None。"""
    from maapilot.flows.daily.base import DAILY_ENV
    if DAILY_ENV in os.environ:
        return os.environ[DAILY_ENV] or "?"
    from maapilot.flows import daily
    run = daily.running()
    if run and run.get("pid") and int(run["pid"]) in procs.ancestors():
        return run.get("run_id") or "?"
    return None


def require(what: str) -> None:
    """定时日常里（用户不在场）→ 抛 CliError（退出码 3），什么都没做；会话里 → 放行（agent 已在对话里问过用户）。"""
    run = _unattended()
    if run:
        raise CliError("needs_user", f"{what}要用户在会话里同意，定时日常（{run}）里用户不在场，没做",
                       exit=NEEDS_USER, hint="写进汇报「要你决定的事」，等用户回来在会话里再做")
