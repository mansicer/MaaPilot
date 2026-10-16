"""agent 宿主（maapilot.hosts、host 命令）：Codex 的无头 agent、往用户的 Codex 会话推消息、超时连子孙进程组一起杀、
host install、两边的命令规则一致。不碰真的 ~/.codex（conftest 把 CODEX_HOME 指到临时目录），不跑真的 codex。"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time

import pytest
import yaml

from maapilot import hosts, paths
from maapilot.flows import daily
from maapilot.tools import host_cmd
from tests.test_daily import _no_real_crontab, env, run, script  # noqa: F401 - fixture

ROOT = paths.ROOT

FAKE_CODEX = """
import json, os, sys, time
log = os.environ["FAKE_CODEX_LOG"]
a = sys.argv[1:]
via_stdin = a[0] == "exec" and a[-1] == "-"            # 提示从 stdin 给（Windows 上 codex.cmd 那样）：记下来时放回去
if via_stdin:
    a[-1] = sys.stdin.read()
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": a, "stdin": via_stdin, "daily": os.environ.get("MAAPILOT_DAILY_RUN")},
                       ensure_ascii=False) + "\\n")
if a[0] == "exec":
    print(json.dumps({"type": "thread.started", "thread_id": "t-1"}))
    text = os.environ.get("FAKE_CODEX_RESULT", "处理好了。\\n结果：完成")
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}, ensure_ascii=False))
    print(json.dumps({"type": "turn.completed", "usage": {}}))
    open(a[a.index("-o") + 1], "w", encoding="utf-8").write(text)
elif a[0] == "queue":
    sys.exit(int(os.environ.get("FAKE_CODEX_QUEUE_EXIT", "0")))
elif a[0] == "execpolicy":
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
    cfg["agent"] = "codex"
    cfg["tools"]["codex"] = str(prog)
    paths.CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")


# --- 无头 agent ------------------------------------------------------------------------------------------------------

def test_prompt_on_stdin_only_for_batch_files(tmp_path, monkeypatch):
    """Windows 上 npm 装的 .cmd 经 cmd.exe 转，多行参数会被截断：提示改走 stdin；单行的推送消息洗掉 cmd 会改的字符。"""
    from maapilot.oscompat import procs
    t = tmp_path / "agent.jsonl"
    assert hosts.command("codex", "/x/codex", "做事", t, stdin=True)[-1] == "-"
    argv = hosts.command("claude", "/x/claude", "做事", t, stdin=True)
    assert argv[:3] == ["/x/claude", "-p", "--output-format"] and "做事" not in argv
    assert procs.batch_arg('a\r\nb "c" 100%') == "a  b 'c' 100％"
    assert procs.batch_arg("a&b|c<d>e^f") == "a＆b｜c＜d＞e＾f"
    monkeypatch.setattr(procs, "WINDOWS", True)
    assert procs.is_batch(r"C:\npm\codex.CMD") and procs.is_batch("x.bat") and not procs.is_batch(r"C:\x\codex.exe")
    monkeypatch.setattr(procs, "WINDOWS", False)
    assert not procs.is_batch("/x/codex.cmd") and not hosts.prompt_on_stdin("/x/claude.cmd")


def test_codex_command_line(tmp_path):
    t = tmp_path / "agent.jsonl"
    argv = hosts.command("codex", "/x/codex", "做事", t)
    assert argv[:2] == ["/x/codex", "exec"] and argv[-1] == "做事"
    assert argv[argv.index("-C") + 1] == str(paths.ROOT) and argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "--json" in argv and argv[argv.index("-o") + 1] == str(hosts.last_message_file(t))
    assert {argv[i + 1] for i, x in enumerate(argv) if x == "--disable"} >= {"computer_use", "browser_use"}
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv and "--approve-for-me" not in argv


def test_unknown_host_is_a_config_error():
    from maapilot.cliutil import CliError
    with pytest.raises(CliError) as e:
        hosts.name({"agent": "gpt"})
    assert e.value.code == "invalid_config"


def test_parse_codex_transcript(tmp_path):
    t = tmp_path / "a.jsonl"
    t.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"type": "thread.started", "thread_id": "x"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "先看看"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "做完了。\n结果：没做成"}},
        {"type": "turn.completed", "usage": {}}]), encoding="utf-8")
    info = daily.parse_transcript(t, "codex")
    assert info["summary"].endswith("结果：没做成") and info["is_error"] is False and info["recovered"] is False
    hosts.last_message_file(t).write_text("最后一句。\n结果：完成", encoding="utf-8")   # -o 写的文件优先
    assert daily.parse_transcript(t, "codex")["recovered"] is True
    t.write_text(json.dumps({"type": "turn.failed", "error": {"message": "x"}}), encoding="utf-8")
    hosts.last_message_file(t).unlink()
    assert daily.parse_transcript(t, "codex")["is_error"] is True


def test_scheduled_daily_runs_codex(capsys, env, codex):
    prog, calls = codex
    _use_codex(prog)
    env["behavior"]["fail"] = {"infrast"}
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 0, out
    (call,) = [c for c in calls() if c["argv"][0] == "exec"]
    assert "maapilot-recovery" in call["argv"][-1] and call["daily"] == out["data"]["run_id"]
    infra = next(t for t in out["data"]["tasks"] if t["task"] == "infrast")
    assert infra["recovered"] is True and infra["agent"]["host"] == "codex"


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


# --- 用户的会话 ------------------------------------------------------------------------------------------------------

def test_record_session_from_environment(monkeypatch):
    assert hosts.record_session({}) is None and hosts.session() is None
    rec = hosts.record_session({"CODEX_THREAD_ID": "th-9"})
    assert rec["host"] == "codex" and hosts.session()["thread"] == "th-9"
    assert hosts.record_session({"CODEX_THREAD_ID": "th-x", daily.DAILY_ENV: "r"}) is None   # 无头 agent 不算
    assert hosts.session()["thread"] == "th-9"
    hosts.record_session({"CLAUDECODE": "1"})
    assert hosts.session()["host"] == "claude"


def test_brief_records_codex_session(monkeypatch):
    from maapilot.tools import doctor
    monkeypatch.setenv("CODEX_THREAD_ID", "th-7")
    assert doctor.session_brief() == {"host": "codex", "thread": "th-7"}


def test_push_only_to_codex_sessions(env, codex, monkeypatch):
    prog, calls = codex
    _use_codex(prog)
    assert hosts.push("x")["pushed"] is False                          # 没记过会话
    hosts.record_session({"CLAUDECODE": "1"})
    assert hosts.push("x")["pushed"] is False and calls() == []        # Claude Code 自己挂着 daily watch
    hosts.record_session({"CODEX_THREAD_ID": "th-1"})
    assert hosts.push("你好") == {"pushed": True, "thread": "th-1"}
    assert calls()[-1]["argv"] == ["queue", "--thread", "th-1", "--message", "你好"]
    monkeypatch.setenv("FAKE_CODEX_QUEUE_EXIT", "1")
    assert hosts.push("x")["pushed"] is False


def test_scheduled_daily_pushes_result(capsys, env, monkeypatch):
    pushed = []
    monkeypatch.setattr(daily, "push_session", lambda text: pushed.append(text) or {"pushed": True})
    env["behavior"]["fail"] = {"recruit"}
    os.environ["FAKE_CLAUDE_RESULT"] = "没弄好。\n结果：没做成"
    try:
        code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    finally:
        del os.environ["FAKE_CLAUDE_RESULT"]
    rid = (out["data"] or {}).get("run_id")
    (msg,) = pushed
    assert msg.startswith("[MaaPilot 定时日常]") and f"report seen {rid}" in msg and "recruit" in msg
    code, out = run(capsys, "daily", "run")                           # 会话里手动跑：在场的 agent 自己看，不推
    assert len(pushed) == 1


def test_refused_scheduled_daily_pushes_reason(capsys, env, monkeypatch):
    pushed = []
    monkeypatch.setattr(daily, "push_session", lambda text: pushed.append(text) or {"pushed": True})
    paths.GUARD.mkdir(parents=True, exist_ok=True)
    (paths.GUARD / "stop.json").write_text("{}", encoding="utf-8")
    code, out = run(capsys, "daily", "run", "--trigger", "schedule")
    assert code == 3 and "这次没跑" in pushed[0] and "guard_stop" in pushed[0]


# --- host install ----------------------------------------------------------------------------------------------------

def test_install_codex_trusts_the_folder(capsys, env, codex, monkeypatch):
    prog, _ = codex
    _use_codex(prog)
    monkeypatch.delenv(daily.DAILY_ENV, raising=False)
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
    assert out["data"]["agent"] == "codex" and out["data"]["hosts"]["codex"]["trusted"] is True


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

# --- 两边的命令规则一致 ------------------------------------------------------------------------------------------------

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


def test_codex_rules_match_claude_settings():
    perms = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]
    want = {"allow": "allow", "ask": "prompt", "deny": "forbidden"}
    rules = _codex_rules()
    for kind, dec in want.items():
        for r in perms[kind]:
            m = re.fullmatch(r"Bash\((.+?)(?::\*)?\)", r)
            if not m:
                continue
            cmd = m.group(1).split()
            got = _decide(rules, cmd)
            assert got == dec, f"{r}：Claude Code 是 {kind}，Codex 规则给的是 {got}"
