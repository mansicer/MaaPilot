"""`maapilot dev rules [--check]`：开发用，按 maapilot/policy.py 生成两个宿主的权限规则文件（见那里的说明）。

这个命令不给用户的 agent 用：不写进 skill、不加放行规则、`maapilot --help` 里也不列。
"""
from __future__ import annotations

from pathlib import Path

from maapilot import paths
from maapilot.cliutil import CliError


def cmd_rules(args):
    """生成（或 --check 只核对）.claude/settings.json 的权限、.codex/rules/maapilot.rules、docs/cli.md 里抄的设置。"""
    from maapilot import policy
    root = Path(args.root) if args.root else paths.ROOT
    files = [{"path": p.as_posix(), "changed": now != want} for p, now, want in policy.generated(root)]
    stale = [f["path"] for f in files if f["changed"]]
    if args.check:
        if stale:
            raise CliError("rules_out_of_date", f"这些文件和 maapilot/policy.py 生成的不一样：{'、'.join(stale)}",
                           hint="改规则改 maapilot/policy.py，再跑 maapilot dev rules；不要手改这几个文件", data={"files": files})
        return {"files": files, "written": False}
    for p, now, want in policy.generated(root):
        if now != want:
            (root / p).parent.mkdir(parents=True, exist_ok=True)
            (root / p).write_text(want, encoding="utf-8", newline="\n")
    return {"files": files, "written": bool(stale)}


def register(sub) -> None:
    g = sub.add_parser("dev")               # 不给 help：开发用，`maapilot --help` 里不列
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("rules", help="按 maapilot/policy.py 生成两个宿主的权限规则文件（开发用）")
    p.add_argument("--check", action="store_true", help="只核对，不一样就退出码 1")
    p.add_argument("--root", metavar="目录", help="仓库目录（默认这个仓库；测试用）")
    p.set_defaults(func=cmd_rules, command="dev rules")
