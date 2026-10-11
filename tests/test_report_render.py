"""汇报的 Markdown：agent 那几行怎么写、补充去重；无头 agent 提示的共同要求。"""
from maapilot.flows import daily, report

GOALS_SUMMARY = """这次日常的目标部分做完了。

- **理智**：120 理智全打在 YW-3 上，3 星。
- **训练室**：丰川祥子技能 2 专 1 还在练。
  - 专 2 的材料留好了。

**要你决定**：要不要分理智刷研磨石？

结果：完成"""


def base(**kw):
    d = {"run_id": "20261011-1640", "trigger": "schedule", "status": "done", "ok": True,
         "started_at": "2026-10-11T16:40:00+08:00", "finished_at": "2026-10-11T17:04:00+08:00",
         "tasks": [], "failed": [], "alerts": [], "guard": {"ok": True}, "depot_change": []}
    d.update(kw)
    return d


def test_goals_row_is_its_own_section():
    md = report.render(base(tasks=[
        {"task": "recruit", "ok": True, "duration_s": 52},
        {"task": "goals", "ok": True, "agent": {"ok": True, "recovered": True, "duration_s": 344,
                                                "summary": GOALS_SUMMARY}},
        {"task": "award", "ok": True, "duration_s": 30}]))
    lst = md.split("## 做了什么")[1].split("## 目标部分")[0]
    assert "- 目标部分：做完了（5 分 44 秒），见下面「目标部分」" in lst
    assert "理智" not in lst and "- 领任务奖励与邮件：完成（30 秒）" in lst      # 总结不混进任务列表
    sec = md.split("## 目标部分")[1].split("## 仓库变化")[0]
    assert "- **训练室**：丰川祥子技能 2 专 1 还在练。\n  - 专 2 的材料留好了。" in sec
    assert "结果：" not in md and "agent 接手" not in md and md.count("120 理智全打") == 1


def test_takeover_summary_indented_under_its_task():
    md = report.render(base(tasks=[
        {"task": "infrast", "ok": True, "recovered": True, "duration_s": 600, "error": "基建卡住了",
         "agent": {"recovered": True, "summary": "关掉了弹窗。\n\n- 重跑了基建\n- 回到首页\n结果：完成"}},
        {"task": "award", "ok": True, "duration_s": 30}]))
    assert ("- 基建：完成（10 分 0 秒）\n  - agent 接手（处理好了）：关掉了弹窗。\n\n    - 重跑了基建\n"
            "    - 回到首页\n- 领任务奖励与邮件") in md
    assert "结果：" not in md


def test_game_update_and_maintenance_rows():
    md = report.render(base(tasks=[
        {"task": "game_update", "ok": True, "duration_s": 717,
         "agent": {"recovered": True, "summary": "游戏已经更新好了。\n\n- 2.7.71 → 2.7.81\n\n结果：完成"}}]))
    assert "- 更新游戏：完成（11 分 57 秒）——游戏已经更新好了。\n\n  - 2.7.71 → 2.7.81" in md
    assert "agent 接手" not in md and "结果：" not in md
    md = report.render(base(status="maintenance", maintenance={}, tasks=[
        {"task": "maintenance", "ok": True, "agent": {"recovered": True, "duration_s": 40,
                                                       "summary": "维护到 12:00，理智会溢出约 30。\n结果：完成"}}]))
    assert "- 停机维护：agent 看过了（40 秒）——维护到 12:00，理智会溢出约 30。" in md and "结果：" not in md


def test_appendix_same_as_agent_summary_shown_once():
    appendix = [{"at": "2026-10-11T17:01:00+08:00",
                 "text": "目标部分：120 理智全打在 YW-3 上，3 星。训练室：丰川祥子技能 2 专 1 还在练，专 2 的材料留好了。"
                         "要你决定：要不要分理智刷研磨石？"},
                {"at": "2026-10-11T18:00:00+08:00", "text": "用户说先不刷研磨石。"}]
    md = report.render(base(appendix=appendix, tasks=[
        {"task": "goals", "ok": True, "agent": {"recovered": True, "summary": GOALS_SUMMARY}}]))
    assert md.count("## 补充") == 1 and "用户说先不刷研磨石。" in md


def test_same_text():
    assert report.same_text("理智 120 全打了 YW-3。", "- **理智**：120 全打了 YW-3\n结果：完成")
    assert not report.same_text("用户说先不刷研磨石。", GOALS_SUMMARY)
    assert not report.same_text("", GOALS_SUMMARY)


def test_every_headless_prompt_has_report_rules():
    w = {"start": "2026-10-09T06:00:00+08:00", "end": "2026-10-09T12:00:00+08:00", "title": "维护"}
    prompts = [
        daily.recovery_prompt("r", "infrast", {"message": "x"}, None, ["infrast"]),
        daily.goals_prompt("r", [{"title": "遥 精2", "object": "干员 遥"}], []),
        daily.expired_prompt("r", ["活动"], []),
        daily.maintenance_prompt("r", w),
        daily.update_prompt("r", {"installed": {"version": "1"}, "latest": {"version": "2"}}),
    ]
    for p in prompts:
        assert "全程用中文（包括中间的说明），总结写给用户看" in p
        assert "不要用 maapilot report write" in p
        assert p.rstrip().splitlines()[-1].startswith(("结束时", "最后一行"))   # 「结果：」那句还在最后
