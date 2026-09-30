"""skills、CLAUDE.md 里写的命令都要是真能跑的命令，权限设置也要对得上。

从说明文字里抽出 `bin/maapilot …`（以及反引号里以命令组开头的简写，如 `state depot`），把占位符换成样例值，
交给 argparse 解析；再检查每条命令都有权限规则（放行或询问），要用户确认的几条在"询问"里。
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
from pathlib import Path

import pytest

from maapilot import cli

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")) + [ROOT / "CLAUDE.md", ROOT / "README.md"]
GROUPS = ("doctor", "screen", "input", "maa", "state", "data", "calc", "goals", "op", "craft", "report",
          "daily", "guard", "lock")
# 要用户在确认框里点同意的命令（design.md 第 7 节、docs/cli.md 命令总览）
ASK = ("guard allow", "daily schedule", "daily unschedule", "lock release")

# 说明里提到的 maa-cli 自己的命令（setup 装资源用），不是 maapilot
MAA_CLI = {"maa install", "maa update"}
# 占位符 → 样例值
PLACEHOLDERS = {
    "关卡": "1-7", "运行编号": "20260930-0440", "run_id": "20260930-0440", "任务": "startup",
    "作业编号": "12345", "物品": "聚酸酯组", "干员": "遥", "名": "遥", "标题": "遥", "数量": "600",
    "这几项": "recruit,mall", "项": "recruit", "字": "终端", "正则": "终端", "对象": "干员 遥 → 精2 1级",
    "当前": "技能 4级",
}
# 说明里的泛指写法，不是一条具体命令
GENERIC = re.compile(r"<命令>|^goals …$|^goals add / update / remove|^maa <任务>$")


def _spans(text: str) -> list[str]:
    """反引号里的片段与代码块里的每一行。"""
    out = re.findall(r"`([^`\n]+)`", text)
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, re.S):
        out += [l.strip() for l in block.splitlines() if l.strip().startswith("bin/maapilot ")]
    return out


def invocations() -> list[tuple[str, str, bool]]:
    found = []
    for f in DOCS:
        for s in _spans(f.read_text(encoding="utf-8")):
            s = s.strip()
            if s.startswith("bin/maapilot "):
                cmd = s[len("bin/maapilot "):]
            elif s.split(" ", 1)[0] in GROUPS and " " in s and s not in MAA_CLI:
                cmd = s                                      # 简写（`state depot`）：只查命令名
            else:
                continue
            if GENERIC.search(cmd):
                continue
            found.append((f"{f.parent.name}/{f.name}", cmd, s.startswith("bin/maapilot ")))
    return found


def normalize(cmd: str) -> list[str]:
    cmd = cmd.replace("maa://<作业编号>", "maa://12345")
    cmd = re.sub(r"\[([^\]]*)\]", r"\1", cmd)                         # 可选部分照写
    cmd = re.sub(r"<([^<>|]+)\|[^<>]*>", lambda m: PLACEHOLDERS.get(m.group(1), m.group(1)), cmd)
    cmd = re.sub(r"<([^<>]+)>", lambda m: PLACEHOLDERS.get(m.group(1), "X"), cmd)
    cmd = cmd.replace("“", '"').replace("”", '"')
    cmd = re.sub(r'"[^"]*"', '"X"', cmd)                              # 引号里的文字（可能不完整）
    cmd = re.sub(r"(\S+)\|\S+", r"\1", cmd)                          # 完成|放弃 → 完成
    parts = []
    for tok in shlex.split(cmd.replace("…", " … ")):
        if tok == "…":
            if parts and parts[-1].startswith("--"):
                parts.append("X")
            continue
        parts.append("3" if tok == "N" else tok)
    # 结尾是只写了开头的参数（`--find ` 之类，反引号截断），补一个值
    if parts and parts[-1].startswith("--") and parts[-1] not in _FLAGS_WITHOUT_VALUE:
        parts.append("X")
    return parts


_FLAGS_WITHOUT_VALUE = {"--brief", "--offline", "--fix", "--formation", "--last", "--all", "--replace-old",
                        "--dry-run", "--interactive", "--no-agent", "--expiring-medicine"}


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = cli.build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra and not getattr(args, "accepts_extra", False):
        raise AssertionError(f"多余的参数：{extra}")
    assert getattr(args, "func", None), "缺少子命令"
    return args


INVOCATIONS = invocations()


def test_found_invocations():
    assert len(INVOCATIONS) > 40
    assert {f for f, _, _ in INVOCATIONS} >= {f"maapilot-{n}/SKILL.md" for n in
                                          ("setup", "daily", "goals", "training", "base", "battle", "recovery")}


@pytest.mark.parametrize("where,cmd,full", INVOCATIONS, ids=[f"{f}:{c}" for f, c, _ in INVOCATIONS])
def test_skill_command_parses(where, cmd, full):
    argv = normalize(cmd)
    try:
        if full:
            _parse(argv)
        else:
            assert argv[:2] in _all_subcommands() or argv[:1] in _all_subcommands(), "没有这个命令"
    except Exception as e:                     # CliError（参数错）或 AssertionError
        pytest.fail(f"{where} 里的 `{cmd}` → {argv} 解析不了：{getattr(e, 'message', e)}")


# --- 权限设置 -------------------------------------------------------------------------------------

def _settings() -> dict:
    return json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))


def _prefixes(kind: str) -> list[str]:
    out = []
    for rule in _settings()["permissions"][kind]:
        m = re.fullmatch(r"Bash\((.*?)(?::\*)?\)", rule)
        if m:
            out.append(m.group(1))
    return out


def _rule(argv: list[str]) -> str | None:
    """这条命令落在哪一类规则（deny > ask > allow，同 Claude Code）。"""
    line = "bin/maapilot " + " ".join(argv)
    for kind in ("deny", "ask", "allow"):
        for p in _prefixes(kind):
            if line == p or line.startswith(p + " "):
                return kind
    return None


def _all_subcommands() -> list[list[str]]:
    parser = cli.build_parser()
    out = []
    for name, sp in parser._subparsers._group_actions[0].choices.items():
        subs = [a for a in sp._actions if isinstance(a, argparse._SubParsersAction)]
        if subs:
            out += [[name, s] for s in subs[0].choices]
        else:
            out.append([name])
    return out


@pytest.mark.parametrize("where,cmd,full", INVOCATIONS, ids=[f"{f}:{c}" for f, c, _ in INVOCATIONS])
def test_skill_command_has_permission_rule(where, cmd, full):
    argv = normalize(cmd)
    assert _rule(argv) in ("allow", "ask"), f"{where} 的 `{cmd}` 没有放行或询问的权限规则"


def test_every_subcommand_has_rule():
    missing = [" ".join(c) for c in _all_subcommands() if _rule(c) not in ("allow", "ask")]
    assert not missing, f"这些命令没有权限规则（agent 用时会弹确认、无头时被拒）：{missing}"


def test_confirm_commands_are_ask():
    for c in ASK:
        assert _rule(c.split()) == "ask", f"`{c}` 必须是询问"
    allowed_ask = [c for c in ASK if any(c.startswith(p.removeprefix("bin/maapilot ")) or
                                          p.removeprefix("bin/maapilot ") == c.split()[0]
                                          for p in _prefixes("allow"))]
    assert not allowed_ask, f"放行规则盖住了要询问的命令：{allowed_ask}"


def test_hook_runs_doctor_brief():
    hooks = _settings()["hooks"]["SessionStart"]
    cmds = [h["command"] for g in hooks for h in g["hooks"]]
    assert any(c.endswith("bin/maapilot doctor --hook") for c in cmds)
