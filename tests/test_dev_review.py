"""`maapilot dev review`：用小的假命令记录、假会话记录。"""
import json
from datetime import timedelta

from maapilot import cli, paths
from maapilot.tools import common, dev_cmd


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _at(minutes_ago: float) -> str:
    return common.iso(common.now() - timedelta(minutes=minutes_ago))


def _write_log(rows):
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    with open(paths.LOGS / f"{common.now():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ts(minutes_ago: float) -> str:
    return (common.now() - timedelta(minutes=minutes_ago)).astimezone().isoformat()


def _use(i, name, minutes_ago, **inp):
    return {"type": "assistant", "timestamp": _ts(minutes_ago),
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}}


def _result(i, minutes_ago, text, error=False):
    return {"type": "user", "timestamp": _ts(minutes_ago),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": text,
                                                     "is_error": error}]}}


def _say(minutes_ago, text):
    return {"type": "assistant", "timestamp": _ts(minutes_ago),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _session(tdir, sid, entries):
    tdir.mkdir(parents=True, exist_ok=True)
    with open(tdir / f"{sid}.jsonl", "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _fixture(tmp_path):
    _write_log([
        {"at": _at(60), "command": "screen tap", "args": {}, "ok": False, "exit": 2, "duration_s": 0,
         "error": "bad_arguments", "message": "invalid choice: 'tap'", "argv": ["screen", "tap"]},
        {"at": _at(59), "command": "screen tap", "args": {}, "ok": False, "exit": 2, "duration_s": 0,
         "error": "bad_arguments", "message": "invalid choice: 'tap'", "argv": ["screen", "tap"]},
        {"at": _at(50), "command": "op mastery", "args": {"operator": "遥", "skill": 3, "rank": 3}, "ok": False,
         "exit": 1, "duration_s": 30, "error": "shortcut_failed", "stage": "训练室", "message": "确认框里的材料数没认全"},
        {"at": _at(49), "command": "input tap", "args": {"x": 1, "y": 2}, "ok": True, "exit": 0, "duration_s": 1},
        {"at": _at(48), "command": "input tap", "args": {"x": 1, "y": 2}, "ok": True, "exit": 0, "duration_s": 1},
        {"at": _at(40), "command": "maa fight", "args": {"stage": "YW-6"}, "ok": False, "exit": 1, "duration_s": 9,
         "error": "maa_failed", "run_id": "20261013-0440"},
        {"at": _at(30), "command": "maa fight", "args": {"stage": "YW-6"}, "ok": False, "exit": 1, "duration_s": 9,
         "error": "maa_failed", "run_id": "20261013-0440"},
        {"at": _at(60 * 24 * 10), "command": "old", "args": {}, "ok": False, "exit": 1, "duration_s": 0},
    ])
    tdir = tmp_path / "transcripts"
    _session(tdir, "aaaa1111-user", [
        {"type": "user", "timestamp": _ts(70), "message": {"role": "user", "content": "帮我把遥练到专三"}},
        _use(1, "Bash", 69, command="maapilot state depot"),
        _result(1, 68, '{"ok": true}'),
        _use(2, "Bash", 67, command="cat private/goals.md | grep 遥"),
        _result(2, 67, "## 遥"),
        _use(3, "Bash", 66, command="maapilot calc need \"干员 遥 → 技能3 专3\" --from 专2"),
        _result(3, 66, 'Exit code 2\n{"ok": false, "command": "calc need", "data": null, '
                       '"error": {"code": "bad_arguments", "message": "认不出「专2」", "hint": null}}', error=True),
        _use(4, "Bash", 65, command="maapilot guard allow 合成玉 600 --reason x"),
        _result(4, 65, "Permission for this tool use was denied. It requires approval", error=True),
        _use(5, "Read", 64, file_path="/x/private/skland.json"),
        _result(5, 64, "File does not exist.", error=True),
        _say(63, "做完了。\n\n结果：部分完成"),
    ])
    _session(tdir, "bbbb2222-dev", [
        {"type": "user", "timestamp": _ts(20), "message": {"role": "user", "content": "改一下代码"}},
        _use(1, "Edit", 19, file_path="/r/maapilot/tools/common.py", old_string="a", new_string="b"),
        _use(2, "Bash", 18, command="cat x | grep y"),
    ])
    _session(tdir, "cccc3333-other", [
        {"type": "user", "timestamp": _ts(10), "message": {"role": "user", "content": "你好"}},
        _use(1, "Bash", 9, command="maapilot doctor --brief"),
        _result(1, 9, '{"ok": true}'),
        _use(2, "Bash", 8, command='maapilot screen ocr --find "行动结束|代理"'),     # 引号里的 | 不算管道
    ])
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    (paths.LOGS / "agent-20261013-0440-1-goals.jsonl").write_text(
        json.dumps({"type": "system", "session_id": "cccc3333-other"}) + "\n", encoding="utf-8")
    return tdir


def test_review_log_and_transcripts(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    tdir = _fixture(tmp_path)
    code, env = run(capsys, "dev", "review", "--transcripts", str(tdir), "--since", "1d")
    assert code == 0, env
    d = env["data"]
    log = d["log"]
    assert log["rows"] == 7 and log["failed"] == 5
    assert log["misuse"] == [{"command": "screen tap", "error": "bad_arguments", "message": "invalid choice: 'tap'",
                              "argv": ["screen", "tap"], "count": 2, "last_at": log["misuse"][0]["last_at"]}]
    assert log["repeated"][0]["command"] == "maa fight" and log["repeated"][0]["count"] == 2
    assert log["shortcut_then_manual"][0]["command"] == "op mastery"
    assert log["shortcut_then_manual"][0]["manual_inputs"] == 2
    assert {g["command"] for g in log["failures"]} == {"screen tap", "op mastery", "maa fight"}

    tr = d["transcripts"]
    assert [x["session"] for x in tr["excluded"]] == ["bbbb2222-dev"]
    by = {s["session"]: s for s in tr["sessions"]}
    u = by["aaaa1111-user"]
    assert u["prompt"] == "帮我把遥练到专三" and u["result"] == "结果：部分完成"
    assert u["denied"][0]["tool"] == "Bash" and "guard allow" in u["denied"][0]["what"]
    assert u["maapilot_failed"] == [{"command": "calc need", "exit": 2, "error": "bad_arguments",
                                     "message": "认不出「专2」", "cmd": u["maapilot_failed"][0]["cmd"]}]
    assert u["tool_errors"][0]["tool"] == "Read"
    assert u["bash_nonstandard"]["count"] == 1 and u["bash_nonstandard"]["by_first_word"] == {"cat": 1}
    assert u["bash"] == 4
    o = by["cccc3333-other"]
    assert o["label"] == "日常 20261013-0440 goals" and "bash_nonstandard" not in o
    assert any("screen tap" in ln for ln in d["lines"])
    # 自己不往命令记录里写
    assert not any("dev review" in ln for ln in (paths.LOGS / f"{common.now():%Y-%m-%d}.jsonl").read_text().splitlines())


def test_review_session_filter_and_exclude(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cccc3333-other")
    tdir = _fixture(tmp_path)
    code, env = run(capsys, "dev", "review", "--transcripts", str(tdir), "--exclude", "aaaa")
    assert code == 0
    tr = env["data"]["transcripts"]
    assert tr["sessions"] == []
    assert {x["why"] for x in tr["excluded"]} >= {"--exclude", "当前会话"}
    code, env = run(capsys, "dev", "review", "--transcripts", str(tdir), "--session", "bbbb")
    assert [s["session"] for s in env["data"]["transcripts"]["sessions"]] == ["bbbb2222-dev"]   # 点名的开发会话也看


def test_since_parsing_and_project_dir(tmp_path):
    assert (common.now() - dev_cmd.parse_since("12h")).total_seconds() >= 12 * 3600 - 5
    assert dev_cmd.parse_since("2026-10-11").date().isoformat() == "2026-10-11"
    import pytest
    from maapilot.cliutil import CliError
    with pytest.raises(CliError):
        dev_cmd.parse_since("昨天")
    from pathlib import Path
    assert dev_cmd.project_dir(Path("/Users/a/Developer/MaaPilot")).name == "-Users-a-Developer-MaaPilot"
    assert dev_cmd.project_dir(Path("/Users/a/Developer/MaaPilot/.claude/worktrees/agent-1")).name == \
        "-Users-a-Developer-MaaPilot"
    assert dev_cmd.project_dir(Path("/Users/a/my.repo")).name == "-Users-a-my-repo"


def test_dev_not_in_help(capsys):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
        cli.build_parser().parse_args(["--help"])
    assert "dev" not in buf.getvalue().split("doctor", 1)[-1]
