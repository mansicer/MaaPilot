"""agent 宿主（maapilot.hosts、host 命令）：host status / install、子进程超时连子孙进程组一起杀。
不碰真的 ~/.codex（conftest 把 CODEX_HOME 指到临时目录），不跑真的 codex。"""
from __future__ import annotations

import json
import os
import signal
import sys
import time

import pytest
import yaml

from maapilot import paths
from maapilot.cliutil import CliError
from maapilot.flows import daily
from maapilot.tools import host_cmd
from tests.test_daily import _no_real_crontab, env, run, script  # noqa: F401 - fixture

ROOT = paths.ROOT

FAKE_CODEX = """
import json, os, sys, time
log = os.environ["FAKE_CODEX_LOG"]
a = sys.argv[1:]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": a}, ensure_ascii=False) + "\\n")
if a[0] == "execpolicy":
    cmd = a[a.index("-r") + 2:]
    prog = cmd[0].replace("\\\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")   # .venv\\Scripts\\maapilot.exe → maapilot
    d = {"maapilot": "allow", "adb": "forbidden", "maa": "forbidden"}.get(prog)
    if [prog] + cmd[1:3] == ["maapilot", "guard", "allow"]:
        d = "prompt"
    if [prog] + cmd[1:3] == ["maapilot", "skland", "token"]:
        d = "forbidden"
    print(json.dumps({"decision": d} if d else {"matchedRules": []}))
"""


@pytest.fixture
def codex(tmp_path, monkeypatch):
    prog = script(tmp_path / "codex", FAKE_CODEX)
    log = tmp_path / "codex-calls.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))

    def calls():
        return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return prog, calls


def _use_codex(prog):
    cfg = yaml.safe_load(paths.CONFIG.read_text(encoding="utf-8"))
    cfg.setdefault("tools", {})["codex"] = str(prog)
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")


# --- 子进程超时 ------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="脚本靠 shebang 跑、POSIX 进程组")
def test_timeout_kills_descendant_groups(tmp_path):
    """codex 的每条命令自成进程组：超时杀 codex 时，它们也要一起走（10-15 实测：只杀 codex 会留下孤儿）。"""
    pidfile = tmp_path / "child.pid"
    parent = script(tmp_path / "parent", f"""
import subprocess, sys, time
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
open({str(pidfile)!r}, "w").write(str(p.pid))
time.sleep(60)
""")
    code, _, timed_out = daily._run_group([str(parent)], env=dict(os.environ), timeout_s=2)
    assert timed_out
    child = int(pidfile.read_text(encoding="utf-8"))
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(child, signal.SIGKILL)
        pytest.fail("子孙进程组没被杀掉")


# --- host install ----------------------------------------------------------------------------------------------------

def test_install_codex_trusts_the_folder(capsys, env, codex, monkeypatch):
    prog, _ = codex
    _use_codex(prog)
    cfgfile = host_cmd.codex_home() / "config.toml"
    cfgfile.parent.mkdir(parents=True)
    cfgfile.write_text('model = "x"\n[projects."/somewhere/else"]\ntrust_level = "trusted"\n', encoding="utf-8")
    code, out = run(capsys, "host", "install", "codex", "--dry-run")
    key = json.dumps(str(ROOT))                                              # TOML 里的写法（Windows 上反斜杠要转义）
    assert code == 0 and out["data"]["changed"] is False and key in out["data"]["append"]
    assert host_cmd.codex_trusted(ROOT) is False
    code, out = run(capsys, "host", "install", "codex")
    assert code == 0 and out["data"]["changed"] and out["data"]["trusted"] is True and out["data"]["rules"]["ok"]
    assert cfgfile.read_text(encoding="utf-8").startswith('model = "x"\n')   # 原来的不动
    code, out = run(capsys, "host", "install", "codex")                         # 再跑：不重复加
    assert out["data"]["changed"] is False and cfgfile.read_text(encoding="utf-8").count(key) == 1
    code, out = run(capsys, "host", "status")
    assert out["data"]["hosts"]["codex"] == {"path": str(prog), "trusted": True}
    assert "session" not in out["data"] and "agent" not in out["data"]                  # 不再记会话、不再有 agent


def test_install_refuses_when_user_said_no(capsys, env, codex, monkeypatch):
    prog, _ = codex
    _use_codex(prog)
    cfgfile = host_cmd.codex_home() / "config.toml"
    cfgfile.parent.mkdir(parents=True)
    cfgfile.write_text(f'[projects.{json.dumps(str(ROOT))}]\ntrust_level = "untrusted"\n', encoding="utf-8")
    code, out = run(capsys, "host", "install", "codex")
    assert code == 2 and out["error"]["code"] == "codex_untrusted"



def test_trust_key_spelled_differently(capsys, env, codex, monkeypatch):
    """Codex 写下的键和我们的写法不完全一样（Windows 上大小写、分隔符；这里用多余的 /. 和结尾 /）：认得出，不重复加一段。"""
    prog, _ = codex
    _use_codex(prog)
    cfgfile = host_cmd.codex_home() / "config.toml"
    cfgfile.parent.mkdir(parents=True)
    odd = str(ROOT.parent) + "/./" + ROOT.name + "/"
    cfgfile.write_text(f'[projects.{json.dumps(odd)}]\ntrust_level = "untrusted"\n', encoding="utf-8")
    assert host_cmd.codex_trusted(ROOT) is False
    code, out = run(capsys, "host", "install", "codex")
    assert code == 2 and out["error"]["code"] == "codex_untrusted"
    cfgfile.write_text(f'[projects.{json.dumps(odd)}]\ntrust_level = "trusted"\n', encoding="utf-8")
    assert host_cmd.codex_trusted(ROOT) is True
    code, out = run(capsys, "host", "install", "codex")
    assert code == 0 and out["data"]["changed"] is False and "[projects" not in cfgfile.read_text(encoding="utf-8")[5:]


# --- OpenClaw -----------------------------------------------------------------------------------------------------------

def test_install_openclaw_only_returns_setup_steps(env, monkeypatch, tmp_path):
    """OpenClaw 没有项目信任目录：install 只认出程序、给出步骤，不写 Codex 的 config.toml。"""
    binary = str(tmp_path / "bin" / "openclaw")
    monkeypatch.setattr(host_cmd.hosts, "resolve", lambda *args, **kwargs: binary)
    out = host_cmd.install("openclaw", dry_run=False)
    assert out == {
        "host": "openclaw",
        "path": binary,
        "trusted": None,
        "changed": False,
        "next": ["按 docs/harnesses.md「OpenClaw」一节把本仓库注册成一个 agent",
                 "把 templates/openclaw/agent-entry.json 合并进 openclaw.json 的 agents.entries",
                 "用 templates/openclaw/cron-daily.json 建重复定时；不要建一次性接力"],
    }
    assert not (host_cmd.codex_home() / "config.toml").exists()


def test_install_openclaw_reports_missing_program(env, monkeypatch):
    monkeypatch.setattr(host_cmd.hosts, "resolve", lambda *args, **kwargs: None)
    with pytest.raises(CliError) as caught:
        host_cmd.install("openclaw", dry_run=False)
    assert caught.value.code == "no_openclaw" and caught.value.exit == 5

# --- 读 Codex 的规则文件（tests/test_hosts_windows.py 用；两边一致、是生成的见 tests/test_policy.py） -------------------

def _codex_rules() -> list[tuple[list, str]]:
    """.codex/rules/maapilot.rules 是 Starlark；这里只用到和 Python 相同的写法，直接执行、收集 prefix_rule。"""
    rules = []
    src = (ROOT / ".codex" / "rules" / "maapilot.rules").read_text(encoding="utf-8")
    exec(compile(src, "maapilot.rules", "exec"),
         {"prefix_rule": lambda pattern, decision, justification=None: rules.append((pattern, decision))})
    return rules


def _decide(rules, cmd: list[str]) -> str | None:
    order = {"forbidden": 3, "prompt": 2, "allow": 1}
    best = None
    for pat, dec in rules:
        if len(cmd) >= len(pat) and all((t in p) if isinstance(p, list) else t == p for p, t in zip(pat, cmd)):
            best = dec if best is None or order[dec] > order[best] else best
    return best
