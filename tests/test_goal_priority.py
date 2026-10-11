"""目标的先后 = 优先级（goals move、priority）；calc need 对照别的目标占着的材料（reserved、conflicts）。"""
import json

import pytest

from maapilot import cli, paths
from maapilot.flows import goals
from maapilot.tools import calc, common

HAS_DATA = (paths.DATA / "character_table.json").exists() and (paths.DATA / "item_table.json").exists()
needs_data = pytest.mark.skipif(not HAS_DATA, reason="没有 data/ 游戏数据表")

D32, PEG, BOOK3, GRIND = "30135", "30033", "3303", "30034"   # D32钢、聚酸酯组、技巧概要·卷3、聚酸酯块


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def goal(title, obj, status="进行中", extra=""):
    return (f"## {title}\n- 状态：{status}\n- 对象：{obj}\n- 原话：{title}（2026-10-01）\n"
            f"- 同意：2026-10-01 20:00，已报：x\n- 花合成玉/源石：否\n{extra}\n分解与进展（agent 自由写）：\n- 10-01：{title} 笔记\n")


def write(*secs, pre="# 目标清单\n\n"):
    paths.GOALS.parent.mkdir(parents=True, exist_ok=True)
    paths.GOALS.write_text(pre + "\n".join(secs), encoding="utf-8")


# --- 先后 ------------------------------------------------------------------------------------------------------------

def titles():
    return [s.title for s in goals.load().sections]


def test_list_priority_in_file_order(capsys):
    write(goal("甲", "关卡 1-7"), goal("乙", "关卡 1-8", status="暂停"), goal("坏", "关卡 1-9", status="做着呢"),
          goal("丙", "关卡 2-1"))
    code, env = run(capsys, "goals", "list")
    assert [(g["title"], g["priority"]) for g in env["data"]["goals"]] == [("甲", 1), ("乙", 2), ("坏", None), ("丙", 3)]
    assert [(g["title"], g["priority"]) for g in goals.active_goals()] == [("甲", 1), ("丙", 3)]


def test_move_reorders_without_changing_sections(capsys):
    secs = [goal(t, "关卡 1-7") for t in "甲乙丙丁"]
    write(*secs)
    code, env = run(capsys, "goals", "move", "丁", "--top")
    assert code == 0, env
    assert [o["title"] for o in env["data"]["order"]] == list("丁甲乙丙") and env["data"]["order"][0]["priority"] == 1
    # 内容一字不改，段间空一行，文件末尾不多空行
    assert paths.GOALS.read_text(encoding="utf-8") == "# 目标清单\n\n" + "\n".join([secs[3], *secs[:3]])
    run(capsys, "goals", "move", "甲", "--after", "丙")
    assert titles() == list("丁乙丙甲")
    run(capsys, "goals", "move", "甲", "--before", "丁")
    assert titles() == list("甲丁乙丙")
    assert paths.GOALS.read_text(encoding="utf-8") == "# 目标清单\n\n" + "\n".join([secs[0], secs[3], secs[1], secs[2]])


def test_move_keeps_preamble_and_no_trailing_newline(capsys):
    pre = "# 目标清单\n\n说明：程序不动。\n"
    last = goal("乙", "关卡 1-8").rstrip("\n")
    write(goal("甲", "关卡 1-7") + "\n", last, pre=pre + "\n")
    run(capsys, "goals", "move", "乙", "--top")
    text = paths.GOALS.read_text(encoding="utf-8")
    assert text.startswith(pre + "\n## 乙\n") and text.endswith("甲 笔记") and "笔记\n\n## 甲\n" in text


@pytest.mark.parametrize("argv,code_", [
    (("甲",), "bad_arguments"),                                 # 没说挪到哪
    (("甲", "--top", "--before", "乙"), "bad_arguments"),
    (("甲", "--before", "甲"), "bad_arguments"),
    (("甲", "--after", "没有这个"), "unknown_goal"),
    (("没有这个", "--top"), "unknown_goal"),
])
def test_move_errors(capsys, argv, code_):
    write(goal("甲", "关卡 1-7"), goal("乙", "关卡 1-8"))
    before = paths.GOALS.read_bytes()
    code, env = run(capsys, "goals", "move", *argv)
    assert code == 2 and env["error"]["code"] == code_
    assert paths.GOALS.read_bytes() == before


# --- 别的目标占着的材料 -----------------------------------------------------------------------------------------------

def snapshots(depot, ops):
    common.write_json(paths.STATE / "depot.json", {"at": common.iso(), "items": depot})
    common.write_json(paths.STATE / "box.json", {"at": common.iso(), "operators": ops})


NENG = {"elite": 2, "level": 90, "skill_level": 7,
        "skills": [{"index": 1, "mastery": 3}, {"index": 2, "mastery": 3}, {"index": 3, "mastery": 2}]}


@needs_data
def test_reserved_by_goals_from_current_state():
    snapshots({}, {"能天使": NENG})
    write(goal("能天使", "干员 能天使 → 技能3 专3"), goal("聚酸酯组", "材料 聚酸酯组 × 10"),
          goal("暂停的", "材料 D32钢 × 100", status="暂停"), goal("关卡", "关卡 1-7"),
          goal("到期的", "材料 D32钢 × 100", extra="- 期限：2020-01-01\n"))
    r = calc.reserved_by_goals()
    # 能天使从快照里的专2 算起：只剩专3 的材料
    assert r[BOOK3]["count"] == 15 and r[D32] == {"name": "D32钢", "count": 6,
                                                  "goals": [{"title": "能天使", "priority": 1, "count": 6}]}
    assert r[PEG]["goals"] == [{"title": "聚酸酯组", "priority": 2, "count": 10}]
    assert D32 in r and r[D32]["count"] == 6                         # 暂停、到期的不占
    assert set(calc.reserved_by_goals(exclude_title="能天使")) == {PEG}
    assert set(calc.reserved_by_goals(exclude_object="材料  聚酸酯组 × 10")) == {BOOK3, D32, GRIND}


@needs_data
def test_calc_need_reports_conflicts(capsys):
    snapshots({D32: 6, PEG: 3}, {"能天使": NENG})
    write(goal("能天使", "干员 能天使 → 技能3 专3"), goal("坏名字", "干员 没这人 → 精1 1级"))
    code, env = run(capsys, "calc", "need", "材料 D32钢 × 4")
    assert code == 0, env
    d = env["data"]
    assert d["short"]["items"] == []                                  # 只看仓库：够
    assert d["conflicts"] == [{"id": D32, "name": "D32钢", "need": 4, "have": 6,
                               "reserved_by_others": [{"goal": "能天使", "priority": 1, "count": 6}],
                               "short_after_reserve": 4}]
    assert d["short_after_goals"]["items"] == [{"id": D32, "name": "D32钢", "count": 4}]
    assert d["reserved"][D32]["count"] == 6
    assert d["reserved_skipped"][0]["title"] == "坏名字"
    # 不撞车的材料：没有 conflicts
    code, env = run(capsys, "calc", "need", "材料 聚酸酯组 × 2")
    assert env["data"]["conflicts"] == [] and env["data"]["short_after_goals"]["items"] == []
    # 算的就是能天使自己：不算自己占的
    code, env = run(capsys, "calc", "need", "干员 能天使 → 技能3 专3")
    assert env["data"]["from"]["mastery"] == {"3": 2} and env["data"]["conflicts"] == []
    assert D32 not in env["data"]["reserved"]
    code, env = run(capsys, "calc", "need", "干员 能天使 → 技能3 专3", "--goal", "坏名字")
    assert D32 in env["data"]["reserved"]                              # 按标题排除的是另一个
    code, env = run(capsys, "calc", "need", "材料 D32钢 × 4", "--ignore-goals")
    assert "reserved" not in env["data"] and "conflicts" not in env["data"]


@needs_data
def test_need_uses_box_skill_and_mastery():
    snapshots({}, {"能天使": NENG})
    d = calc.need("干员 能天使 → 技能3 专3", ignore_goals=True)
    assert d["from"]["mastery"] == {3: 2}
    assert {i["id"]: i["count"] for i in d["need"]["items"]} == {BOOK3: 15, D32: 6, GRIND: 6}
    assert not any("专精不知道" in a for a in d["assumed"])
    # --from 给了的以 --from 为准
    d = calc.need("干员 能天使 → 技能3 专3", "技能3 专0", ignore_goals=True)
    assert d["from"]["mastery"] == {3: 0}
