"""要用户在场同意的操作：`guard allow`、`daily schedule / unschedule`（有改动时）、`reset`。

用户可能不在这台电脑前（远程连着会话），所以不弹本机的框：agent 在对话里问、用户在对话里答应了，再跑这些命令。
程序只管一件事：定时日常叫起的无头 agent 里（环境带 MAAPILOT_DAILY_RUN）用户不在场，一律拒绝（退出码 3），什么都不做。
会话里的放行靠 agent 守 AGENTS.md 的硬规则，加上宿主的权限设置（Claude Code：这几条是"询问"）。
"""
from __future__ import annotations

import os

from maapilot.cliutil import NEEDS_USER, CliError


def _unattended() -> str | None:
    from maapilot.flows.daily.base import DAILY_ENV
    return os.environ.get(DAILY_ENV)


def require(what: str) -> None:
    """定时日常里（用户不在场）→ 抛 CliError（退出码 3），什么都没做；会话里 → 放行（agent 已在对话里问过用户）。"""
    run = _unattended()
    if run:
        raise CliError("needs_user", f"{what}要用户在会话里同意，定时日常（{run}）里用户不在场，没做",
                       exit=NEEDS_USER, hint="写进汇报「要你决定的事」，等用户回来在会话里再做")
