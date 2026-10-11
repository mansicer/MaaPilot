import json

import pytest

from maapilot import cli, paths
from maapilot.flows import goals


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


SAMPLE = """# 目标清单

说明：agent 自己的备注，程序不动。

## 遥 精2
- 状态：进行中
- 对象：干员 遥 → 精2 1级
- 原话：把遥练到精2（2026-09-30）
- 同意：2026-09-30 21:40，已报：龙门币约 59 万、经验约 36 万、聚合剂 2、提纯源岩 9…
- 花合成玉/源石：否

分解与进展（agent 自由写）：
- 缺辅助芯片，靠 PR-C 刷；合成要换加工站干员
- 10-01：合成做了一半，差聚合剂 2

## 聚酸酯组
- 花合成玉／源石: 否
- 对象: 材料 聚酸酯组 × 10
- 状态：暂停
- 原话：合 10 个聚酸酯组（2026-09-29）
- 同意：2026-09-29 20:00，已报：龙门币 1 万
  （续一行：还报了固源岩 20）
随手写的笔记，没有标题
"""


def write(text):
    paths.GOALS.parent.mkdir(parents=True, exist_ok=True)
    paths.GOALS.write_text(text, encoding="utf-8")


def test_list_parses_fixed_fields_and_notes(capsys):
    write(SAMPLE)
    code, env = run(capsys, "goals", "list")
    assert code == 0
    g1, g2 = env["data"]["goals"]
    assert g1 == {"title": "遥 精2", "status": "进行中", "object": "干员 遥 → 精2 1级",
                  "said": "把遥练到精2（2026-09-30）",
                  "approved": "2026-09-30 21:40，已报：龙门币约 59 万、经验约 36 万、聚合剂 2、提纯源岩 9…",
                  "premium": False, "deadline": None, "expired": False, "priority": 1,
                  "notes": "分解与进展（agent 自由写）：\n- 缺辅助芯片，靠 PR-C 刷；合成要换加工站干员\n- 10-01：合成做了一半，差聚合剂 2"}
    # 乱序、半角冒号、全角斜杠、折行都认
    assert g2["status"] == "暂停" and g2["premium"] is False and g2["object"] == "材料 聚酸酯组 × 10"
    assert g2["approved"].endswith("龙门币 1 万 （续一行：还报了固源岩 20）")
    assert g2["notes"] == "随手写的笔记，没有标题"
    assert env["data"]["active"] == 1 and env["data"]["problems"] == []
    assert goals.active_count() == 1


def test_list_reports_problems_without_failing(capsys):
    write("## 坏的\n- 状态：做着呢\n- 对象：练遥\n\n## 坏的\n- 状态：进行中\n")
    code, env = run(capsys, "goals", "list")
    assert code == 0
    msgs = [p["message"] for p in env["data"]["problems"]]
    assert any("缺少字段" in m for m in msgs)
    assert any("状态写成了「做着呢」" in m for m in msgs)
    assert any("对象「练遥」" in m for m in msgs)
    assert any("标题重复" in m for m in msgs)


def test_add_creates_file_in_design_format(capsys):
    code, env = run(capsys, "goals", "add", "--title", "遥 精2", "--object", "干员 遥 → 精2 1级",
                    "--said", "把遥练到精2（2026-09-30）", "--agreed", "2026-09-30 21:40，已报：龙门币约 59 万",
                    "--note", "缺辅助芯片")
    assert code == 0, env
    text = paths.GOALS.read_text(encoding="utf-8")
    assert text.startswith("# 目标清单\n\n## 遥 精2\n- 状态：进行中\n- 对象：干员 遥 → 精2 1级\n")
    assert "- 花合成玉/源石：否\n\n分解与进展（agent 自由写）：\n- " in text and text.endswith("：缺辅助芯片\n")
    # 再加一个：前一段原样，段间空一行；原话没日期、同意没时间时补上
    code, env = run(capsys, "goals", "add", "--title", "合聚酸酯组", "--object", "材料 聚酸酯组 × 10",
                    "--said", "合 10 个聚酸酯组", "--approved", "已报：龙门币 1 万", "--premium", "是")
    assert code == 0
    g = env["data"]["goal"]
    assert g["premium"] is True and g["said"].startswith("合 10 个聚酸酯组（20")
    assert g["approved"][:4].isdigit() and g["approved"].endswith("，已报：龙门币 1 万")
    assert paths.GOALS.read_text(encoding="utf-8").startswith(text + "\n## 合聚酸酯组\n")
    assert env["data"]["active"] == 2


@pytest.mark.parametrize("argv,code_", [
    (["--title", "遥 精2", "--object", "干员 遥 → 精2 1级"], "duplicate_goal"),
    (["--title", "x", "--object", "练遥到精2"], "invalid_object"),
    (["--title", "x", "--object", "干员 遥 → 精2 1级", "--premium", "也许"], "bad_arguments"),
])
def test_add_rejects(capsys, argv, code_):
    write(SAMPLE)
    before = paths.GOALS.read_bytes()
    code, env = run(capsys, "goals", "add", *argv, "--said", "s", "--approved", "a")
    assert code == 2 and env["error"]["code"] == code_
    assert paths.GOALS.read_bytes() == before


def test_add_requires_approval(capsys):
    code, env = run(capsys, "goals", "add", "--title", "x", "--object", "干员 遥 → 精2 1级", "--said", "s")
    assert code == 2 and env["error"]["code"] == "bad_arguments"
    assert not paths.GOALS.exists()


def test_update_touches_only_that_section(capsys):
    write(SAMPLE)
    code, env = run(capsys, "goals", "update", "聚酸", "--status", "进行中", "--approved", "2026-10-01 08:00，改报：2 万",
                    "--note", "开始合")
    assert code == 0, env
    text = paths.GOALS.read_text(encoding="utf-8")
    first, second = text.split("## 聚酸酯组\n")
    assert first == SAMPLE.split("## 聚酸酯组\n")[0]                 # 其余字节不变
    assert "- 状态：进行中\n" in second and "续一行" not in second      # 折行随字段一起换掉
    assert "- 同意：2026-10-01 08:00，改报：2 万\n随手写的笔记，没有标题\n- " in second
    assert second.endswith("：开始合\n")
    assert env["data"]["changed"] == ["状态", "同意", "分解与进展"]


def test_update_note_without_notes_and_missing_field(capsys):
    write("## 甲\n- 状态：进行中\n- 对象：其他 模组任务\n")
    code, env = run(capsys, "goals", "update", "甲", "--premium", "是", "--note", "第一条")
    assert code == 0
    text = paths.GOALS.read_text(encoding="utf-8")
    assert text.startswith("## 甲\n- 状态：进行中\n- 对象：其他 模组任务\n- 花合成玉/源石：是\n\n分解与进展（agent 自由写）：\n- ")


def test_update_errors(capsys):
    write(SAMPLE)
    assert run(capsys, "goals", "update", "不存在", "--note", "x")[1]["error"]["code"] == "unknown_goal"
    assert run(capsys, "goals", "update", "遥 精2", "--status", "完成")[1]["error"]["code"] == "bad_arguments"
    assert run(capsys, "goals", "update", "遥 精2")[1]["error"]["code"] == "bad_arguments"
    write(SAMPLE + "\n## 遥 精2 60级\n- 状态：进行中\n")
    assert run(capsys, "goals", "update", "遥", "--note", "x")[1]["error"]["code"] == "ambiguous_goal"
    assert run(capsys, "goals", "update", "遥 精2", "--note", "x")[0] == 0     # 精确匹配优先于前缀


def test_remove_moves_section_to_done(capsys):
    write(SAMPLE)
    code, env = run(capsys, "goals", "remove", "遥 精2", "--status", "完成", "--reason", "遥已精2")
    assert code == 0, env
    text = paths.GOALS.read_text(encoding="utf-8")
    assert "遥 精2" not in text and text.startswith("# 目标清单\n\n说明：agent 自己的备注，程序不动。\n\n## 聚酸酯组\n")
    done = paths.GOALS_DONE.read_text(encoding="utf-8")
    assert done.startswith("# 完成或放弃的目标\n\n## 遥 精2\n- 状态：完成\n")
    assert "- 花合成玉/源石：否\n- 结束：" in done and "完成，遥已精2（没能核对：还没有干员识别的快照）\n\n分解与进展" in done
    assert done.endswith("差聚合剂 2\n")
    # 第二个挪到最后
    run(capsys, "goals", "remove", "聚酸酯组", "--status", "放弃", "--reason", "不要了")
    done2 = paths.GOALS_DONE.read_text(encoding="utf-8")
    assert done2.startswith(done + "\n## 聚酸酯组\n")
    assert paths.GOALS.read_text(encoding="utf-8") == "# 目标清单\n\n说明：agent 自己的备注，程序不动。\n"
    code, env = run(capsys, "goals", "list", "--all")
    assert env["data"]["goals"] == [] and [g["title"] for g in env["data"]["done"]] == ["遥 精2", "聚酸酯组"]
    assert env["data"]["done"][1]["status"] == "放弃" and "不要了" in env["data"]["done"][1]["ended"]
    assert env["data"]["problems"] == []


def test_remove_rejects_bad_status(capsys):
    write(SAMPLE)
    code, env = run(capsys, "goals", "remove", "遥 精2", "--status", "暂停", "--reason", "x")
    assert code == 2 and not paths.GOALS_DONE.exists()


def test_roundtrip_preserves_bytes_without_trailing_newline():
    text = SAMPLE.rstrip("\n")
    gf = goals.parse(text, paths.GOALS)
    assert gf.text().rstrip("\n") == text
    write(text)
    goals.save(goals.load())
    assert paths.GOALS.read_text(encoding="utf-8") == text


def test_heading_in_code_block_is_not_a_goal():
    write("## 甲\n- 状态：进行中\n\n```\n## 不是目标\n```\n")
    assert [s.title for s in goals.load().sections] == ["甲"]
