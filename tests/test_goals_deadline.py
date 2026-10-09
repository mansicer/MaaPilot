"""目标的期限（限时活动）：期限字段、活动对象、到期不算进行中、goals expire。"""
import json
from datetime import datetime

import pytest

from maapilot import cli, paths
from maapilot.flows import goals
from maapilot.knowledge.calendar import GAME_TZ

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=GAME_TZ)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(goals, "_now", lambda: NOW)


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def write(text):
    paths.GOALS.parent.mkdir(parents=True, exist_ok=True)
    paths.GOALS.write_text(text, encoding="utf-8")


def goal(title, obj="活动 昨日海", status="进行中", deadline="2026-10-30 03:59", notes=None):
    s = (f"## {title}\n- 状态：{status}\n- 对象：{obj}\n- 原话：活动期间刷活动（2026-10-09）\n"
         f"- 同意：2026-10-09 12:30，已报：活动关理智\n- 花合成玉/源石：否\n")
    if deadline is not None:
        s += f"- 期限：{deadline}\n"
    if notes:
        s += f"\n分解与进展（agent 自由写）：\n{notes}\n"
    return s


ADD = ("goals", "add", "--title", "昨日海", "--object", "活动 昨日海", "--said", "活动期间刷活动",
       "--agreed", "已报：活动关理智")


def test_parse_deadline_forms():
    assert goals.parse_deadline("2026-10-30 03:59") == datetime(2026, 10, 30, 3, 59, tzinfo=GAME_TZ)
    # 只写日期：那个游戏日结束，即次日 04:00
    assert goals.parse_deadline("2026-10-30") == datetime(2026, 10, 31, 4, 0, tzinfo=GAME_TZ)
    assert goals.parse_deadline("2026/10/30 3:59（昨日海结束）") == datetime(2026, 10, 30, 3, 59, tzinfo=GAME_TZ)
    assert goals.parse_deadline("") is None
    for bad in ("下周", "2026-13-01", "2026-10-30 25:00"):
        with pytest.raises(ValueError):
            goals.parse_deadline(bad)


def test_add_with_deadline_writes_field(capsys):
    code, env = run(capsys, *ADD, "--deadline", "2026-10-30 03:59")
    assert code == 0, env
    text = paths.GOALS.read_text(encoding="utf-8")
    assert "- 花合成玉/源石：否\n- 期限：2026-10-30 03:59\n\n分解与进展" in text
    g = env["data"]["goal"]
    assert g["deadline"] == "2026-10-30T03:59+08:00" and g["expired"] is False and g["days_left"] == 9.7
    assert env["data"]["active"] == 1


@pytest.mark.parametrize("argv,code_", [
    ((), "bad_deadline"),                                   # 活动目标必须有期限
    (("--deadline", "月底"), "bad_deadline"),
    (("--deadline", "2026-10-30 03:59 左右"), "bad_deadline"),  # 命令行参数不带说明
    (("--deadline", "2026-10-01"), "bad_deadline"),         # 已经过了
])
def test_add_deadline_errors(capsys, argv, code_):
    code, env = run(capsys, *ADD, *argv)
    assert code == 2 and env["error"]["code"] == code_
    assert not paths.GOALS.exists()


def test_add_non_event_without_deadline_and_date_only(capsys):
    code, env = run(capsys, "goals", "add", "--title", "遥", "--object", "干员 遥 → 精2 1级", "--said", "练遥",
                    "--agreed", "已报")
    assert code == 0 and env["data"]["goal"]["deadline"] is None and "days_left" not in env["data"]["goal"]
    code, env = run(capsys, "goals", "update", "遥", "--deadline", "2026-10-30")
    assert code == 0 and env["data"]["changed"] == ["期限"]
    assert env["data"]["goal"]["deadline"] == "2026-10-31T04:00+08:00"
    assert "- 期限：2026-10-30\n" in paths.GOALS.read_text(encoding="utf-8")


def test_update_deadline_set_replace_remove(capsys):
    write(goal("遥", obj="干员 遥 → 精2 1级", deadline=None, notes="- 10-09：开始"))
    code, env = run(capsys, "goals", "update", "遥", "--deadline", "2026-10-25T12:00")
    assert code == 0, env
    assert "- 期限：2026-10-25 12:00\n\n分解与进展" in paths.GOALS.read_text(encoding="utf-8")
    run(capsys, "goals", "update", "遥", "--deadline", "2026-10-26 12:00")
    assert paths.GOALS.read_text(encoding="utf-8").count("期限") == 1
    for off in ("none", ""):
        code, env = run(capsys, "goals", "update", "遥", "--deadline", "2026-10-26 12:00")
        code, env = run(capsys, "goals", "update", "遥", "--deadline", off)
        assert code == 0 and env["data"]["goal"]["deadline"] is None
        assert "期限" not in paths.GOALS.read_text(encoding="utf-8")


def test_update_cannot_drop_event_deadline(capsys):
    write(goal("昨日海"))
    before = paths.GOALS.read_text(encoding="utf-8")
    code, env = run(capsys, "goals", "update", "昨日海", "--deadline", "none")
    assert code == 2 and env["error"]["code"] == "bad_deadline"
    code, env = run(capsys, "goals", "update", "昨日海", "--deadline", "2026-10-05")
    assert code == 2 and env["error"]["code"] == "bad_deadline"
    assert paths.GOALS.read_text(encoding="utf-8") == before


def test_list_marks_expired_and_problems(capsys):
    write(goal("昨日海") + "\n" + goal("旧活动", deadline="2026-10-19 03:59") + "\n" + goal("没期限", deadline=None)
          + "\n" + goal("写错了", obj="其他 随便", deadline="下个月"))
    code, env = run(capsys, "goals", "list")
    assert code == 0
    by = {g["title"]: g for g in env["data"]["goals"]}
    assert by["昨日海"]["expired"] is False and by["昨日海"]["days_left"] == 9.7
    assert by["旧活动"]["expired"] is True and by["旧活动"]["days_left"] == -1.3
    assert by["没期限"]["deadline"] is None and by["没期限"]["expired"] is False
    assert by["写错了"]["deadline"] is None
    assert env["data"]["active"] == 3                            # 到期的不算进行中
    msgs = {p["title"]: p["message"] for p in env["data"]["problems"]}
    assert set(msgs) == {"没期限", "写错了"}
    assert "活动目标没写期限" in msgs["没期限"] and "期限写成了「下个月」" in msgs["写错了"]
    assert [g["title"] for g in goals.active_goals()] == ["昨日海", "没期限", "写错了"]
    assert goals.active_count() == 3


def test_expire_moves_expired_goals(capsys):
    keep = goal("昨日海")
    write("# 目标清单\n\n" + goal("旧活动", deadline="2026-10-19 03:59", notes="- 10-10：打完了 SS-1")
          + "\n" + keep + "\n" + goal("暂停的", status="暂停", deadline="2026-10-01"))
    code, env = run(capsys, "goals", "expire")
    assert code == 0, env
    assert [g["title"] for g in env["data"]["expired"]] == ["旧活动", "暂停的"]
    assert env["data"]["active"] == 1
    assert all(g["status"] == "到期" and g["expired"] for g in env["data"]["expired"])
    assert paths.GOALS.read_text(encoding="utf-8") == "# 目标清单\n\n" + keep
    done = paths.GOALS_DONE.read_text(encoding="utf-8")
    assert "- 结束：2026-10-20 到期，期限 2026-10-19 03:59 已过，自动结束\n" in done
    assert "- 10-10：打完了 SS-1\n- 10-20：到期（期限 2026-10-19 03:59），自动结束\n" in done
    assert "- 10-20：到期（期限 2026-10-01），自动结束" in done
    # goals-done.md 里"到期"是合法状态
    code, env = run(capsys, "goals", "list", "--all")
    assert env["data"]["problems"] == [] and len(env["data"]["done"]) == 2
    # 再跑一次：没有到期的，什么都不写
    mtime = paths.GOALS_DONE.stat().st_mtime_ns
    assert goals.expire_due() == []
    assert paths.GOALS_DONE.stat().st_mtime_ns == mtime


def test_expire_due_with_explicit_now():
    write(goal("昨日海"))
    assert goals.expire_due(datetime(2026, 10, 30, 3, 58, tzinfo=GAME_TZ)) == []
    moved = goals.expire_due(datetime(2026, 10, 30, 3, 59, tzinfo=GAME_TZ))
    assert [g["title"] for g in moved] == ["昨日海"]
    assert goals.load().sections == []


def test_event_object_parses_but_not_computable(capsys):
    from maapilot.tools.calc import parse_object
    assert parse_object("活动 昨日海") == {"kind": "活动", "text": "昨日海"}
    code, env = run(capsys, "calc", "need", "活动 昨日海")
    assert code == 2 and env["error"]["code"] == "not_computable"
