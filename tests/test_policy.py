"""权限规则只写一份（maapilot/policy.py），生成两个宿主的文件：入库的文件就是生成的结果；两边的结论一致；
和改成生成之前手写的两份（tests/fixtures/rules_baseline/，10-19）比，没有变松（原来拒绝 / 询问 / 放行的照旧；
原来没规则的 maapilot 命令改成整个放行后可以有结论，10-20）。"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from maapilot import cli, paths, policy
from tests.test_docs_consistency import _all_subcommands

ROOT = paths.ROOT
BASELINE = Path(__file__).parent / "fixtures" / "rules_baseline"


def run_cli(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# --- 读两种文件、按宿主的规则判定 ------------------------------------------------------------------------------------

def claude_rules(settings: dict) -> dict[str, list[str]]:
    return {k: settings["permissions"].get(k, []) for k in policy.KINDS}


def claude_decide(rules: dict, argv: list[str]) -> str | None:
    """Claude Code：`Bash(x:*)` 按词前缀、`Bash(x)` 整条；拒绝 > 询问 > 放行。"""
    line = " ".join(argv)
    for kind in (policy.DENY, policy.ASK, policy.ALLOW):
        for r in rules[kind]:
            m = re.fullmatch(r"Bash\((.+?)(:\*)?\)", r)
            if m and (line == m.group(1) or (m.group(2) and line.startswith(m.group(1) + " "))):
                return kind
    return None


def codex_rules(path: Path) -> list[tuple[list, str]]:
    """规则文件是 Starlark；只用到和 Python 相同的写法，直接执行、收集 prefix_rule。"""
    rules = []
    exec(compile(path.read_text(encoding="utf-8"), path.name, "exec"),
         {"prefix_rule": lambda pattern, decision, justification=None: rules.append((pattern, decision))})
    return rules


def codex_decide(rules, argv: list[str]) -> str | None:
    order = {"forbidden": 3, "prompt": 2, "allow": 1}
    best = None
    for pat, dec in rules:
        if len(argv) >= len(pat) and all((t in p) if isinstance(p, list) else t == p for p, t in zip(pat, argv)):
            best = dec if best is None or order[dec] > order[best] else best
    return {v: k for k, v in policy.CODEX.items()}.get(best) if best else None


def universe() -> list[list[str]]:
    """拿来比的命令：每种 maapilot 写法 × 每个子命令（和整组、带参数的），maa / adb / sleep 的各种写法。"""
    subs = _all_subcommands() + [list(c.words[:1]) for c in policy.COMMANDS] + [list(c.words) for c in policy.COMMANDS]
    subs += [s + ["--x", "1"] for s in subs] + [["dev", "review"], ["dev", "rules"], ["nonexistent"], []]
    out = [[p, *s] for p in policy.MAAPILOT + ("maapilot.cmd", "/abs/maapilot") for s in subs]
    for prog in ("maa", "maa.exe", "adb", "adb.exe"):
        for a in ("run", "fight", "copilot", "ssscopilot", "roguelike", "reclamation", "startup", "closedown",
                  "install", "update", "shell", "-s", "connect", "devices"):
            out += [[prog, a], [prog, a, "x"]]
    out += [["sleep", "300"], ["sleep", "300", "x"], ["sleep", "5"], ["sleep"]]
    return out


# --- 入库的文件就是生成的 --------------------------------------------------------------------------------------------

def test_committed_files_are_generated():
    stale = [p.as_posix() for p, now, want in policy.generated(ROOT) if now != want]
    assert not stale, f"这些文件不是 maapilot/policy.py 生成的（改规则改 policy.py，再跑 maapilot dev rules）：{stale}"


def test_settings_keep_non_permission_parts():
    s = json.loads((ROOT / policy.SETTINGS).read_text(encoding="utf-8"))
    assert list(s) == ["$schema", "permissions", "hooks"] and s["hooks"]["SessionStart"]
    assert policy.render_claude({"a": 1, "permissions": {}, "b": 2}) == {"a": 1, "permissions": policy.claude_permissions(), "b": 2}


def test_dev_rules_check_and_write(tmp_path, capsys):
    for p in (policy.SETTINGS, policy.CODEX_RULES, policy.CLI_DOC):
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / p, tmp_path / p)
    code, out = run_cli(capsys, "dev", "rules", "--check", "--root", str(tmp_path))
    assert code == 0 and not any(f["changed"] for f in out["data"]["files"])
    (tmp_path / policy.CODEX_RULES).write_text("# 手改的\n", encoding="utf-8")
    s = json.loads((tmp_path / policy.SETTINGS).read_text(encoding="utf-8"))
    s["permissions"]["allow"].append("Bash(rm:*)")
    (tmp_path / policy.SETTINGS).write_text(json.dumps(s), encoding="utf-8")
    code, out = run_cli(capsys, "dev", "rules", "--check", "--root", str(tmp_path))
    assert code == 1 and out["error"]["code"] == "rules_out_of_date"
    assert "# 手改的" in (tmp_path / policy.CODEX_RULES).read_text(encoding="utf-8")          # --check 不写
    code, out = run_cli(capsys, "dev", "rules", "--root", str(tmp_path))
    assert code == 0 and out["data"]["written"]
    for p, now, want in policy.generated(ROOT):
        assert (tmp_path / p).read_bytes() == want.encode("utf-8"), p                       # 换行也是 \n


# --- 语义：两边一致、和文件一致 --------------------------------------------------------------------------------------

def test_files_mean_what_policy_says():
    """生成的文件按各宿主自己的匹配规则读出来，和 policy.decide 一样。"""
    claude = claude_rules(json.loads((ROOT / policy.SETTINGS).read_text(encoding="utf-8")))
    codex = codex_rules(ROOT / policy.CODEX_RULES)
    for argv in universe():
        assert claude_decide(claude, argv) == policy.decide(argv, "claude"), argv
        assert codex_decide(codex, argv) == policy.decide(argv, "codex"), argv


def test_hosts_agree():
    """本名 maapilot 两边一样；别的写法 Codex 和本名一样，Claude Code 只有拒绝的列（放行 / 询问按默认弹确认）。"""
    for argv in universe():
        if argv[0] in policy.MAAPILOT:
            bare = [policy.BARE, *argv[1:]]
            assert policy.decide(argv, "codex") == policy.decide(bare, "codex") == policy.decide(bare, "claude"), argv
            c = policy.decide(argv, "claude")
            assert c == policy.decide(bare, "claude") or (c is None and argv[0] != policy.BARE), argv
        elif argv[:2] != ["sleep", "300"] or len(argv) == 2:
            assert policy.decide(argv, "claude") == policy.decide(argv, "codex"), argv


def test_key_decisions():
    d = policy.decide
    for form in policy.MAAPILOT:
        assert d([form, "guard", "allow", "源石", "1"], "codex") == policy.ASK
        assert d([form, "skland", "token"], "codex") == d([form, "skland", "token"], "claude") == policy.DENY
        assert d([form, "daily", "done"], "codex") == policy.ALLOW
    assert d(["maapilot", "guard", "check"]) == policy.ALLOW and d(["maapilot", "dev", "rules"]) == policy.ASK
    assert d(["maapilot", "brand-new-command"]) == policy.ALLOW                     # 新命令不用改规则
    assert d([".venv/bin/maapilot", "dev", "rules"], "codex") == policy.ASK
    assert d(["maa.exe", "fight", "1-7"]) == policy.DENY and d(["maa", "install"]) is None
    assert d(["adb", "-s", "x", "shell"]) == policy.DENY and d(["adb", "connect", "x"]) is None
    assert d(["sleep", "300"]) == policy.ALLOW and d(["sleep", "300", "x"]) is None and d(["sleep", "5"]) is None
    deny = claude_rules(json.loads((ROOT / policy.SETTINGS).read_text(encoding="utf-8")))[policy.DENY]
    assert {"Read(private/skland.json)", "Edit(private/skland.json)", "Edit(private/guard/**)"} <= set(deny)


def test_exceptions_name_real_commands():
    """maapilot 整个放行，例外（询问 / 拒绝）写的命令得真有：改了名例外就悄悄失效了。"""
    real = {tuple(s) for s in _all_subcommands()} | {(s[0],) for s in _all_subcommands()}
    stale = [" ".join(c.words) for c in policy.COMMANDS if c.decision != policy.ALLOW and c.words not in real]
    assert not stale, f"policy.COMMANDS 的例外里有不存在的命令：{stale}"


def test_install_check_expectations_follow_policy():
    from maapilot.tools import host_cmd
    for want, cmd in host_cmd.EXPECT:
        assert policy.CODEX[policy.decide(cmd, "codex")] == want, cmd


# --- 和改成生成之前手写的两份比 --------------------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["claude", "codex"])
def test_not_looser_than_baseline(host):
    if host == "claude":
        old = claude_rules(json.loads((BASELINE / "settings.json").read_text(encoding="utf-8")))
        new = claude_rules(json.loads((ROOT / policy.SETTINGS).read_text(encoding="utf-8")))
        dec = claude_decide
    else:
        old, new, dec = codex_rules(BASELINE / "maapilot.rules"), codex_rules(ROOT / policy.CODEX_RULES), codex_decide
    for argv in universe():
        was, now = dec(old, argv), dec(new, argv)
        if was is not None or argv[0] not in policy.MAAPILOT:
            assert now == was, f"{host}：{' '.join(argv)} 原来是 {was}，现在是 {now}"
    # 原来没规则、现在有的只能是 maapilot 本身的命令；Claude Code 里只有本名放行 / 询问
    assert dec(new, ["maapilot", "dev", "rules"]) == policy.ASK
    if host == "claude":
        assert dec(new, [".venv/bin/maapilot", "daily", "done"]) is None


def test_same_file_rules_as_baseline():
    def files(s):
        return {k: sorted(r for r in v if not r.startswith("Bash(")) for k, v in claude_rules(s).items()}
    old = json.loads((BASELINE / "settings.json").read_text(encoding="utf-8"))
    new = json.loads((ROOT / policy.SETTINGS).read_text(encoding="utf-8"))
    assert files(old) == files(new)
    assert {k: v for k, v in old.items() if k != "permissions"} == {k: v for k, v in new.items() if k != "permissions"}
