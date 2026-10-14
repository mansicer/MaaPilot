"""现场练度（live_box）：森空岛滞后时用我们在游戏里读到 / 做成的练度补上，按项取高；一天后还对不上的当可能读错。"""
import json
from datetime import timedelta

import pytest

from maapilot import cli, paths
from maapilot.tools import calc, common, doctor, live_box

from test_goals_remove import goal, needs_items, run, write


def box(at=None, **ops):
    common.write_json(paths.STATE / "box.json", {"at": at or common.iso(), "source": "skland", "operators": ops})


def skills(*m):
    return [{"index": i + 1, "mastery": v} for i, v in enumerate(m)]


# 10-14 的真事：专3 04:58 练完，05:13 森空岛还是专2
SAKIKO = {"elite": 2, "level": 60, "skill_level": 7, "skills": skills(0, 2, 3)}


def test_overlay_takes_higher_per_part():
    box(丰川祥子=SAKIKO, 能天使={"elite": 1, "level": 80, "skill_level": 7, "skills": skills(0, 0, 0)})
    live_box.record("丰川祥子", source="op progress", mastery={2: 3})
    live_box.record("能天使", source="op progress", elite=2, level=1)
    ops, _ = calc.box_snapshot()
    s = ops["丰川祥子"]
    assert [x["mastery"] for x in s["skills"]] == [0, 3, 3] and s["level"] == 60
    assert s["live"]["source"] == "op progress" and s["live"]["parts"] == ["mastery2"]
    a = ops["能天使"]
    assert (a["elite"], a["level"]) == (2, 1) and a["live"]["parts"] == ["elite"]
    assert calc.raw_box_snapshot()[0]["丰川祥子"]["skills"][1]["mastery"] == 2     # 原快照不动


def test_snapshot_ahead_wins():
    box(能天使={"elite": 2, "level": 40, "skill_level": 7, "skills": skills(0, 0, 2)})
    live_box.record("能天使", source="state op", elite=2, level=1, skill_level=6)
    a = calc.box_snapshot()[0]["能天使"]
    assert (a["elite"], a["level"], a["skill_level"]) == (2, 40, 7) and "live" not in a


def test_record_keeps_max_and_prunes_caught_up():
    old = (common.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    box(at=old, 丰川祥子=SAKIKO)
    live_box.record("丰川祥子", source="op progress", mastery={2: 3})
    live_box.record("丰川祥子", source="state op", elite=2, level=50, skill_level=7)   # 低于快照的不算数
    e = live_box.load()["丰川祥子"]
    assert e["mastery"] == {"2": 3} and e["source"] == "state op"
    box(丰川祥子={**SAKIKO, "skills": skills(0, 3, 3)})                     # 森空岛追上来了
    live_box.record("能天使", source="op progress", skill_level=7)
    assert set(live_box.load()) == {"能天使"}


def test_old_reading_snapshot_still_lower_is_suspect(capsys):
    then = common.now() - timedelta(hours=30)
    common.write_json(paths.STATE / "live.json", {"operators": {"丰川祥子": {
        "mastery": {"2": 3}, "at": then.isoformat(timespec="seconds"), "source": "op progress"}}})
    box(丰川祥子=SAKIKO)
    assert calc.box_snapshot()[0]["丰川祥子"]["skills"][1]["mastery"] == 2                 # 不再采用
    (s,) = live_box.suspects()
    assert s["name"] == "丰川祥子" and s["parts"] == ["mastery2"]
    lines = doctor.brief_lines({**_brief_base(), "live_suspects": [s]})
    assert any("练度可能读错了：丰川祥子" in l for l in lines)


def _brief_base():
    return {"setup_done": True, "guard_stop": None, "goals": [], "goals_active": 0, "last_daily": None,
            "schedule": {"installed": False, "times": []}, "news": {}, "sources": [], "unseen_problems": []}


@pytest.mark.parametrize("plan,rows,want", [
    ({"skill_mastery": [0, 3, 0]},
     [{"step": "技能2 专3", "result": "Success", "detail": "AlreadySatisfied", "skill_mastery": [0, 3, 0]}],
     {"mastery": {"2": 3}}),
    ({"skill_mastery": [0, 0, 3]},                                            # 开了专2：现在至少专1
     [{"step": "技能3 专3", "result": "Success", "detail": "PrerequisiteTraining", "skill_mastery": [0, 0, 2]}],
     {"mastery": {"3": 1}}),
    ({"elite": 2, "skill_level": 7},
     [{"step": "精英化到精2", "result": "Success", "detail": "Completed", "elite": 2},
      {"step": "技能升到 7 级", "result": "Success", "detail": "AlreadySatisfied"}],
     {"elite": 2, "level": 1, "skill_level": 7}),
    ({"elite": 2}, [{"step": "精英化到精2", "result": "Failed", "detail": "ResourceInsufficient"}], None),
])
def test_from_progress(plan, rows, want):
    live_box.from_progress("能天使", plan, rows)
    e = live_box.load().get("能天使")
    if want is None:
        assert e is None
    else:
        assert {k: e[k] for k in want} == want and e["source"] == "op progress"


def test_goal_done_with_live_reading(capsys):
    box(丰川祥子=SAKIKO)
    write(goal("丰川祥子 技能2专3", "干员 丰川祥子 → 技能2 专3"))
    code, env = run(capsys, "goals", "remove", "丰川祥子 技能2专3", "--status", "完成", "--reason", "专3 练完")
    assert code == 3 and env["error"]["code"] == "not_achieved"
    live_box.record("丰川祥子", source="op progress", mastery={2: 3})
    code, env = run(capsys, "goals", "remove", "丰川祥子 技能2专3", "--status", "完成", "--reason", "专3 练完")
    assert code == 0, env
    assert "op progress 的现场读数）" in paths.GOALS_DONE.read_text(encoding="utf-8")


@needs_items
def test_calc_need_uses_live_reading(capsys):
    box(丰川祥子=SAKIKO)
    live_box.record("丰川祥子", source="op progress", mastery={2: 3})
    assert cli.main(["calc", "need", "干员 丰川祥子 → 技能2 专3"]) == 0
    d = json.loads(capsys.readouterr().out)["data"]
    assert not d["need"]["items"] and d["need"]["lmd"] == 0
