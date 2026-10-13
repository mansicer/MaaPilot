"""命令记录（private/log/<日期>.jsonl）：参数写错也记、关键结果的摘要、token 和账号信息不进记录。"""
import json

import pytest

from maapilot import cli, paths
from maapilot.tools import calc, log_summary

TOKEN = "AbCdEf0123456789GhIjKlMnOp"


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def rows():
    if not paths.LOGS.exists():
        return []
    return [json.loads(ln) for p in sorted(paths.LOGS.glob("*.jsonl"))
            for ln in p.read_text(encoding="utf-8").splitlines()]


def log_text():
    if not paths.LOGS.exists():
        return ""
    return "".join(p.read_text(encoding="utf-8") for p in paths.LOGS.glob("**/*") if p.is_file())


def test_argparse_error_is_logged(capsys):
    code, env = run(capsys, "screen", "tap")
    assert code == 2
    r = rows()[-1]
    assert r["exit"] == 2 and r["ok"] is False and r["error"] == "bad_arguments"
    assert r["argv"] == ["screen", "tap"] and r["command"] == "screen tap" and r["message"]


def test_unknown_option_is_logged_with_argv(capsys):
    code, _ = run(capsys, "calc", "need", "干员 遥 → 精2 1级", "--frm", "专2")
    assert code == 2
    r = rows()[-1]
    assert r["command"] == "calc need" and r["argv"][-2:] == ["--frm", "专2"]


def test_handler_bad_request_is_logged(capsys, monkeypatch):
    from maapilot.cliutil import BAD_REQUEST, CliError

    def bad(*a, **k):
        raise CliError("bad_arguments", "认不出当前练度「专2」", exit=BAD_REQUEST)
    monkeypatch.setattr(calc, "need", bad)
    code, _ = run(capsys, "calc", "need", "干员 遥 → 技能3 专3", "--from", "专2")
    assert code == 2
    r = rows()[-1]
    assert r["command"] == "calc need" and r["args"]["from_"] == "专2" and r["message"] == "认不出当前练度「专2」"
    assert r["argv"] == ["calc", "need", "干员 遥 → 技能3 专3", "--from", "专2"]


def test_calc_need_result_summary(capsys, monkeypatch):
    monkeypatch.setattr(calc, "need", lambda *a, **k: {
        "short": {"lmd": 1000, "exp": 0, "items": [{"id": "30013", "name": "固源岩组", "count": 3}]},
        "short_after_goals": {"lmd": 1000, "exp": 0, "items": []}, "assumed": []})
    code, _ = run(capsys, "calc", "need", "干员 遥 → 精2 1级")
    assert code == 0
    r = rows()[-1]
    assert r["ok"] is True and r["result"] == {"short_lmd": 1000, "short": ["固源岩组×3"]} and "argv" not in r


def test_plain_success_without_summary_not_logged(capsys):
    code, _ = run(capsys, "lock", "status")
    assert code == 0 and rows() == []


@pytest.mark.parametrize("argv", [
    ["skland", "token", TOKEN],
    ["skland", "token", "--uid", "87654321", TOKEN],
    ["skland", f"--token={TOKEN}"],
    ["state", "sanity", TOKEN],
    ["state", "skland", "--uid", "87654321", TOKEN],
])
def test_token_on_command_line_never_logged(capsys, argv):
    code, _ = run(capsys, *argv)
    assert code == 2
    text = log_text()
    assert text and TOKEN not in text and "87654321" not in text


def test_secret_args_scrubbed():
    from maapilot.tools import common
    common.record("skland token", {"uid": "87654321", "token": TOKEN, "no_browser": True}, ok=False, exit=3,
                  duration_s=0, error="token_invalid", message=f"token {TOKEN} 过期了")
    r = rows()[-1]
    assert r["args"] == {"no_browser": True} and "message" not in r
    assert log_summary.safe_argv(["state", "depot", "--uid=1", "--item", "龙门币"]) == \
        ["state", "depot", "--uid=<略>", "--item", "龙门币"]
    # 路径、物品名不会被当成 token
    s = "/Users/x/Developer/MaaPilot/private/shots/State_Depot0123.png 技巧概要·卷3"
    assert log_summary.scrub_text(s) == s


def test_summaries():
    s = log_summary.summarize
    assert s("state sanity", {"at": "x", "current": 29, "max": 135, "source": "ocr", "suspect": True,
                              "estimate": 120, "shot": "/a.png"}) == \
        {"current": 29, "max": 135, "source": "ocr", "suspect": True, "estimate": 120}
    assert s("maa fight", {"task": "fight", "stage": "YW-6", "times_done": 2, "sanity_used": 0, "proxy_failed": 2,
                           "sanity": {"current": 29, "max": 135}, "drops": [],
                           "tasks": [{"name": "Fight", "status": "Completed"}]}) == \
        {"stage": "YW-6", "times_done": 2, "sanity_used": 0, "proxy_failed": 2, "sanity_left": 29, "drops": 0}
    assert s("guard check", {"before": {"orundum": 3200}, "after": {"orundum": 1400}, "allowed": {},
                             "unexplained": {"orundum": -1800}}) == \
        {"before": {"orundum": 3200}, "after": {"orundum": 1400}, "unexplained": {"orundum": -1800}}
    assert s("guard snapshot", {"at": "x", "originite": 5, "orundum": 3200}) == {"originite": 5, "orundum": 3200}
    assert s("op mastery", {"operator": "遥", "skill": 3, "status": "started", "rank": 2, "hours": 16,
                            "shot": "/a.png"}) == {"operator": "遥", "skill": 3, "status": "started", "rank": 2,
                                                   "hours": 16}
    assert s("goals update", {"goal": {"title": "遥 精2", "status": "暂停", "object": "…"}, "changed": ["状态"]}) == \
        {"title": "遥 精2", "status": "暂停", "changed": ["状态"]}
    assert s("goals list", {"goals": []}) is None and s("maa why", {"x": 1}) is None
