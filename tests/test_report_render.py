"""汇报的 Markdown：标题写定时 / 手动、「目标部分」来自 daily done、补充（report write）去重、没收尾时的提示、重跑过的项。"""
from maapilot.flows import report

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


def test_title_says_schedule_or_manual():
    assert report.render(base()).startswith("# 日常汇报：10 月 11 日 16:40（定时）")
    assert report.render(base(trigger="manual")).startswith("# 日常汇报：10 月 11 日 16:40（手动）")


def test_goals_summary_is_its_own_section():
    md = report.render(base(goals_pending=True, goals_active=2, tasks=[
        {"task": "recruit", "ok": True, "duration_s": 52},
        {"task": "award", "ok": True, "duration_s": 30}],
        goals_summary={"at": "2026-10-11T17:30:00+08:00", "text": GOALS_SUMMARY, "by": "agent"}))
    lst = md.split("## 做了什么")[1].split("## 目标部分")[0]
    assert "- 目标：有 2 个进行中的目标，见下面「目标部分」" in lst
    assert "理智" not in lst and "- 领任务奖励与邮件：完成（30 秒）" in lst      # 总结不混进任务列表
    sec = md.split("## 目标部分")[1].split("## 仓库变化")[0]
    assert "- **训练室**：丰川祥子技能 2 专 1 还在练。\n  - 专 2 的材料留好了。" in sec
    assert "结果：" not in md and md.count("120 理智全打") == 1


def test_no_goals_summary_section_until_done():
    md = report.render(base(goals_pending=True, goals_active=1, award_pending=True, open=True))
    assert "## 目标部分" not in md and "留给 agent 接着做" in md and "还没收尾" in md
    assert "还没收尾" not in report.render(base())


def test_retried_task_marked():
    md = report.render(base(tasks=[
        {"task": "infrast", "ok": False, "error": "基建卡住了", "retried": True},
        {"task": "infrast", "ok": True, "duration_s": 600}]))
    assert "- 基建：没做成——基建卡住了（后面重跑了）\n- 基建：完成（10 分 0 秒）" in md


def test_appendix_same_as_goals_summary_shown_once():
    appendix = [{"at": "2026-10-11T17:01:00+08:00",
                 "text": "目标部分：120 理智全打在 YW-3 上，3 星。训练室：丰川祥子技能 2 专 1 还在练，专 2 的材料留好了。"
                         "要你决定：要不要分理智刷研磨石？"},
                {"at": "2026-10-11T18:00:00+08:00", "text": "用户说先不刷研磨石。"}]
    md = report.render(base(appendix=appendix, goals_summary={"text": GOALS_SUMMARY}))
    assert md.count("## 补充") == 1 and "用户说先不刷研磨石。" in md
    assert md.index("## 目标部分") < md.index("## 补充")


def test_same_text():
    assert report.same_text("理智 120 全打了 YW-3。", "- **理智**：120 全打了 YW-3\n结果：完成")
    assert not report.same_text("用户说先不刷研磨石。", GOALS_SUMMARY)
    assert not report.same_text("", GOALS_SUMMARY)


def test_news_due_line():
    lines = report.summary_lines(base(news={"due": ["6774:公告"], "lines": []}))
    assert any("官方日程有 1 条消息" in x for x in lines)
