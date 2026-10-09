"""goals remove 的保护：--reason 必填、完成前按快照核对；daily run 用的 snapshot() / diff()。"""
import json

import pytest

from maapilot import cli, paths
from maapilot.flows import goals
from maapilot.tools import common

HAS_ITEMS = (paths.DATA / "item_table.json").exists()
needs_items = pytest.mark.skipif(not HAS_ITEMS, reason="没有 data/ 游戏数据表")


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def goal(title, obj, status="进行中", extra=""):
    return (f"## {title}\n- 状态：{status}\n- 对象：{obj}\n- 原话：{title}（2026-10-01）\n"
            f"- 同意：2026-10-01 20:00，已报：龙门币 1 万\n- 花合成玉/源石：否\n{extra}")


def write(*secs):
    paths.GOALS.parent.mkdir(parents=True, exist_ok=True)
    paths.GOALS.write_text("# 目标清单\n\n" + "\n".join(secs), encoding="utf-8")


def box(**ops):
    common.write_json(paths.STATE / "box.json", {"at": common.iso(), "operators": ops})


YAO = {"elite": 2, "level": 1, "skill_level": 7,
       "skills": [{"index": 1, "mastery": 0}, {"index": 2, "mastery": 3}, {"index": 3, "mastery": 1}]}


def test_reason_required(capsys):
    write(goal("遥", "干员 遥 → 精2 1级"))
    for extra in ((), ("--reason", "  ")):
        code, env = run(capsys, "goals", "remove", "遥", "--status", "放弃", *extra)
        assert code == 2 and env["error"]["code"] == "reason_required"
    assert not paths.GOALS_DONE.exists()


def test_done_verified_from_box(capsys):
    box(遥=YAO)
    write(goal("遥", "干员 遥 → 精2 1级，技能 7级，技能2 专3"))
    code, env = run(capsys, "goals", "remove", "遥", "--status", "完成", "--reason", "遥练好了")
    assert code == 0, env
    assert env["data"]["verified"] is True
    assert "完成，遥练好了（已核对快照）" in paths.GOALS_DONE.read_text(encoding="utf-8")


def test_done_not_achieved_blocks_until_user_confirms(capsys):
    box(遥=YAO)
    write(goal("遥 专三", "干员 遥 → 技能3 专3"))
    before = paths.GOALS.read_text(encoding="utf-8")
    code, env = run(capsys, "goals", "remove", "遥 专三", "--status", "完成", "--reason", "做完了")
    assert code == 3 and env["error"]["code"] == "not_achieved"
    assert env["data"]["short"] == [{"part": "技能3专精", "target": "专3", "current": "专1"}]
    assert env["data"]["current"]["elite"] == 2
    assert paths.GOALS.read_text(encoding="utf-8") == before and not paths.GOALS_DONE.exists()
    code, env = run(capsys, "goals", "remove", "遥 专三", "--status", "完成", "--reason", "用户说算完成",
                    "--confirmed-by-user")
    assert code == 0 and env["data"]["verified"] is False
    assert "完成，用户说算完成（用户确认：快照里还没到 技能3专精 专1→专3）" in paths.GOALS_DONE.read_text(encoding="utf-8")


def test_done_level_short(capsys):
    box(遥={"elite": 1, "level": 80})
    write(goal("遥", "干员 遥 → 精2 1级"))
    code, env = run(capsys, "goals", "remove", "遥", "--status", "完成", "--reason", "x")
    assert code == 3 and env["data"]["short"] == [{"part": "精英化与等级", "target": "精2 1级", "current": "精1 80级"}]


@pytest.mark.parametrize("snap,why", [
    (None, "还没有干员识别的快照"),
    ({"别人": {"elite": 2, "level": 1}}, "干员识别里没有遥"),
    ({"遥": {"elite": 2, "level": 1}}, "快照里没有技能等级"),        # MAA 识别不读技能
])
def test_done_unverifiable_does_not_block(capsys, snap, why):
    if snap is not None:
        box(**snap)
    write(goal("遥", "干员 遥 → 精2 1级，技能 7级"))
    code, env = run(capsys, "goals", "remove", "遥", "--status", "完成", "--reason", "练好了")
    assert code == 0 and env["data"]["verified"] is None
    assert why in env["data"]["check"]["why"]
    assert f"完成，练好了（没能核对：{why}" in paths.GOALS_DONE.read_text(encoding="utf-8")


def test_done_non_computable_objects(capsys):
    write(goal("打 12-17", "关卡 12-17"), goal("昨日海", "活动 昨日海", extra="- 期限：2099-10-30 03:59\n"))
    for t in ("打 12-17", "昨日海"):
        code, env = run(capsys, "goals", "remove", t, "--status", "完成", "--reason", "三星了")
        assert code == 0 and env["data"]["verified"] is None
    assert "（没能核对：关卡目标没法按快照核对）" in paths.GOALS_DONE.read_text(encoding="utf-8")


def test_dropped_skips_check(capsys):
    box(遥={"elite": 0, "level": 1})
    write(goal("遥", "干员 遥 → 精2 1级"))
    code, env = run(capsys, "goals", "remove", "遥", "--status", "放弃", "--reason", "用户说不要了")
    assert code == 0 and env["data"]["verified"] is None and "check" not in env["data"]
    assert "- 结束：" in paths.GOALS_DONE.read_text(encoding="utf-8")


@needs_items
def test_done_material_checks_depot(capsys):
    from maapilot.tools.data import resolve_item_or_fail
    iid = resolve_item_or_fail("聚酸酯组")
    write(goal("聚酸酯组", "材料 聚酸酯组 × 10"))
    code, env = run(capsys, "goals", "remove", "聚酸酯组", "--status", "完成", "--reason", "合完了")
    assert code == 0 and env["data"]["verified"] is None                 # 没有仓库快照：不拦
    write(goal("聚酸酯组", "材料 聚酸酯组 × 10"))
    common.write_json(paths.STATE / "depot.json", {"at": common.iso(), "items": {iid: 6}})
    code, env = run(capsys, "goals", "remove", "聚酸酯组", "--status", "完成", "--reason", "合完了")
    assert code == 3 and env["data"]["short"][0]["count"] == 4
    common.write_json(paths.STATE / "depot.json", {"at": common.iso(), "items": {iid: 12}})
    code, env = run(capsys, "goals", "remove", "聚酸酯组", "--status", "完成", "--reason", "合完了")
    assert code == 0 and env["data"]["verified"] is True


def test_snapshot_and_diff():
    write(goal("甲", "干员 遥 → 精2 1级"), goal("乙", "关卡 12-17", status="暂停"),
          goal("丙", "其他 模组任务"), goal("丁", "关卡 1-7"))
    before = goals.snapshot()
    assert set(before) == {"甲", "乙", "丙", "丁"}
    assert before["乙"] == {"status": "暂停", "object": "关卡 12-17", "said": "乙（2026-10-01）",
                           "approved": "2026-10-01 20:00，已报：龙门币 1 万", "premium": "否", "deadline": None}
    goals.remove("丙", "放弃", "用户说不要了")
    gf = goals.load()
    sec = goals.find(gf, "甲")
    goals._set_field(sec, "状态", "暂停")
    goals._set_field(sec, "期限", "2099-01-01")
    del gf.sections[gf.sections.index(goals.find(gf, "丁"))]             # agent 直接删掉的，没经过 remove
    goals.save(gf)
    goals.add("戊", "材料 聚酸酯组 × 5", "合 5 个", "已报")
    d = goals.diff(before, goals.snapshot())
    rm = {r["title"]: r for r in d["removed"]}
    assert set(rm) == {"丙", "丁"}
    assert rm["丙"]["done_entry"]["status"] == "放弃" and "用户说不要了" in rm["丙"]["done_entry"]["ended"]
    assert rm["丁"]["done_entry"] is None and rm["丁"]["fields"]["object"] == "关卡 1-7"
    assert [a["title"] for a in d["added"]] == ["戊"]
    assert d["changed"] == [{"title": "甲", "field": "status", "before": "进行中", "after": "暂停"},
                            {"title": "甲", "field": "deadline", "before": None, "after": "2099-01-01"}]
    assert goals.diff(before, before) == {"removed": [], "added": [], "changed": []}
