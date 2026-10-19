"""skills、CLAUDE.md 里写的命令都要是真能跑的命令，权限规则（maapilot/policy.py）也要对得上。

从说明文字里抽出 `maapilot …`（以及反引号里以命令组开头的简写，如 `state depot`），把占位符换成样例值，
交给 argparse 解析；再检查每条命令都有权限规则（放行或询问），要用户确认的几条在"询问"里。
"""
from __future__ import annotations

import argparse
import json
from maapilot import paths
import re
import shlex
from pathlib import Path

import pytest

from maapilot import cli, policy

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md")) + [ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / "README.md"]
GROUPS = ("doctor", "screen", "input", "maa", "state", "data", "calc", "goals", "op", "craft", "report",
          "daily", "guard", "lock", "reset", "skland", "news", "game", "copilot")
# 权限设置里是询问的命令：只有记下花合成玉 / 源石的同意（design.md 第 7 节、docs/cli.md 命令总览）
ASK = ("guard allow",)
# 只能由用户自己在终端里跑的命令（agent 一律被拒；skill 里只会让用户去跑）
USER_ONLY = ("skland token",)
# 开发用的命令：不写进 skill、不给放行规则（用户的 agent 用会弹确认、无头时被拒）
DEV_ONLY = ("dev",)

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
        out += [l.strip() for l in block.splitlines() if l.strip().startswith("maapilot ")]
    return out


def invocations() -> list[tuple[str, str, bool]]:
    found = []
    for f in DOCS:
        for s in _spans(f.read_text(encoding="utf-8")):
            s = s.strip()
            if s.startswith("maapilot "):
                cmd = s[len("maapilot "):]
            elif s.split(" ", 1)[0] in GROUPS and " " in s and s not in MAA_CLI:
                cmd = s                                      # 简写（`state depot`）：只查命令名
            else:
                continue
            if GENERIC.search(cmd):
                continue
            found.append((f"{f.parent.name}/{f.name}", cmd, s.startswith("maapilot ")))
    return found


def normalize(cmd: str) -> list[str]:
    cmd = cmd.replace("prts://<作业编号>", "prts://12345")
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
    """这条命令落在哪一类规则（deny > ask > allow，同 Claude Code）。按 maapilot/policy.py 判；
    入库的 settings.json / Codex 规则就是它生成的（tests/test_policy.py 核对），两边结论一样。"""
    return policy.decide(["maapilot", *argv], "claude")


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
    if any(" ".join(argv).startswith(u) for u in USER_ONLY):           # 让用户自己在终端里跑的：agent 被拒
        assert _rule(argv) == "deny", f"{where} 的 `{cmd}` 只能由用户跑，agent 应被拒"
        return
    assert _rule(argv) in ("allow", "ask"), f"{where} 的 `{cmd}` 没有放行或询问的权限规则"


def test_every_subcommand_has_rule():
    missing = [" ".join(c) for c in _all_subcommands()
               if _rule(c) not in ("allow", "ask") and " ".join(c) not in USER_ONLY and c[0] not in DEV_ONLY]
    assert not missing, f"这些命令没有权限规则（agent 用时会弹确认、无头时被拒）：{missing}"


def test_dev_commands_not_for_user_agent():
    """开发用的命令：skill、CLAUDE.md 里不提，也没有放行规则。"""
    assert not [c for _, c, _ in INVOCATIONS if c.split(" ", 1)[0] in DEV_ONLY]
    assert all(_rule([d, "review"]) is None for d in DEV_ONLY)


def test_confirm_commands_are_ask():
    for c in ASK:
        assert _rule(c.split()) == "ask", f"`{c}` 必须是询问"
    allowed_ask = [c for c in ASK if any(c.startswith(p.removeprefix("maapilot ")) or
                                          p.removeprefix("maapilot ") == c.split()[0]
                                          for p in _prefixes("allow"))]
    assert not allowed_ask, f"放行规则盖住了要询问的命令：{allowed_ask}"


def test_user_only_commands_and_token_file_are_denied():
    for c in USER_ONLY:
        assert _rule(c.split()) == "deny", f"`{c}` 要由用户自己跑，agent 应被拒"
    deny = _settings()["permissions"]["deny"]
    assert "Read(private/skland.json)" in deny and "Edit(private/skland.json)" in deny


def test_hook_runs_doctor_brief():
    hooks = _settings()["hooks"]["SessionStart"]
    cmds = [h["command"] for g in hooks for h in g["hooks"]]
    assert any(c.endswith("bin/session-start") for c in cmds)
    assert "doctor --hook" in (paths.ROOT / "bin" / "session-start").read_text(encoding="utf-8")


def test_settings_file_rules_use_edit_not_write():
    """Claude Code 的文件权限只认 Edit(...)（覆盖所有写文件的工具）；Write(...) 规则不生效，还会在启动时报警告。"""
    import json
    from pathlib import Path
    s = json.loads((Path(__file__).resolve().parents[1] / ".claude" / "settings.json").read_text(encoding="utf-8"))
    rules = [r for k in ("allow", "ask", "deny") for r in s["permissions"].get(k, [])]
    assert not [r for r in rules if r.startswith("Write(")]
