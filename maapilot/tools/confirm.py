"""日常期间不做的事：只有记下花合成玉 / 源石的同意（`guard allow`）。

其他操作都以用户在对话里的同意为准，程序不另设关卡（AGENTS.md「硬规则」、CLAUDE_DEV.md「设计哲学」第 3 条）。
花费的同意必须是用户在对话里亲口说的。日常在跑、或者跑完还没收尾（`daily done` 之前）时一律拒绝（退出码 3），什么都不做：
定时日常多半是用户不在的时候跑的（04:40 在睡觉），这时记下的"同意"一定是假的；手动跑的也一样拦，日常里本来就不该临时决定花真实货币。
"""
from __future__ import annotations

from maapilot.cliutil import NEEDS_USER, CliError


def daily_in_progress() -> str | None:
    """在跑或没收尾的日常的运行编号（看不出编号时给 "?"）；都没有 → None。"""
    from maapilot.flows import daily
    run = daily.running()
    if run:
        return str(run.get("run_id") or "?")
    op = daily.open_daily()
    if op:
        return str(op.get("run_id") or "?")
    return None


def require(what: str) -> None:
    """有日常在跑或没收尾 → 抛 CliError（退出码 3），什么都没做；没有 → 放行（agent 已在对话里问过用户）。"""
    run = daily_in_progress()
    if run:
        raise CliError("needs_user", f"日常 {run} 还在进行（在跑或没收尾），{what}现在不能记",
                       exit=NEEDS_USER, hint="等这次日常 daily done 收尾后，用户在对话里同意了再记")
