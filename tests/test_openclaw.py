"""OpenClaw 配置片段：按当前格式和 MaaPilot 的定时约定校验。"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPENCLAW = ROOT / "templates" / "openclaw"


def _json(name: str) -> dict:
    return json.loads((OPENCLAW / name).read_text(encoding="utf-8"))


def test_agent_uses_current_entries_schema():
    cfg = _json("agent-entry.json")
    assert set(cfg) == {"agents"}
    assert set(cfg["agents"]) == {"entries"}
    entry = cfg["agents"]["entries"]["${AGENT_ID}"]
    assert entry["workspace"] == "${REPO}"
    assert entry["agentDir"] == "${AGENT_DIR}"
    assert entry["model"] == "${MODEL}"
    assert "list" not in cfg["agents"]


def test_daily_job_is_recurring_and_does_not_build_its_own_successor():
    job = _json("cron-daily.json")
    assert job["schedule"]["kind"] == "cron"
    assert job["schedule"]["tz"] == "${TZ}"
    assert job["sessionTarget"] == "isolated"
    assert job["payload"]["timeoutSeconds"] == 10800
    assert "deleteAfterRun" not in job
    message = job["payload"]["message"]
    assert "重复定时" in message and "不要" in message and "下一次一次性定时" in message


def test_daily_job_stays_silent_until_one_final_delivery():
    message = _json("cron-daily.json")["payload"]["message"]
    assert "不要输出进度" in message
    assert "NO_REPLY" in message
    assert "正常等待且没有异常时才使用 NO_REPLY" in message
    assert "失败通知不以 daily done 成功为前提" in message
    for failure in ("部分失败", "启动失败", "超时", "无法恢复", "daily done 收尾失败"):
        assert failure in message
    assert "仅生成一条最终总结" in message
    assert "message/conversations_send/sessions_send" in message


def test_daily_job_polls_background_work_without_relying_on_completion_handoff():
    message = _json("cron-daily.json")["payload"]["message"]
    assert "当前 turn 内用 process 持续 poll 到退出" in message
    assert "不要结束 turn 等待完成事件自动唤醒" in message
    # Historical error strings belong in the operator guide, not every cron turn.
    assert "Session event origin is missing" not in message
    assert "docs/harnesses/openclaw.md" in message


def test_daily_job_has_no_deployment_specific_delivery():
    job = _json("cron-daily.json")
    assert job["delivery"] == {"mode": "announce", "channel": "${CHANNEL}", "accountId": "${ACCOUNT_ID}",
                               "to": "${DELIVERY_TO}", "bestEffort": False}
    text = (OPENCLAW / "README.md").read_text(encoding="utf-8")
    assert "agents.entries" in text and "agents.list" in text
    assert "cron-oneshot.json" not in text
    assert not (OPENCLAW / "cron-oneshot.json").exists()


def test_openclaw_does_not_replace_the_existing_one_shot_defaults():
    harnesses = (ROOT / "docs" / "harnesses" / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    openclaw = (ROOT / "docs" / "harnesses" / "openclaw.md").read_text(encoding="utf-8")
    assert "claude-code.md" in harnesses
    assert "codex.md" in harnesses
    assert "openclaw.md" in harnesses
    assert "| OpenClaw | `docs/harnesses/openclaw.md` |" in agents
    assert agents.count("OpenClaw") == 1
    assert "一次性接力" in openclaw
    assert "重复 cron" in openclaw
    assert "OpenClaw 例外" not in (ROOT / ".agents" / "skills" / "maapilot-daily" / "SKILL.md").read_text(encoding="utf-8")


def test_openclaw_delivery_is_channel_independent_and_tested_before_use():
    readme = (OPENCLAW / "README.md").read_text(encoding="utf-8")
    openclaw = (ROOT / "docs" / "harnesses" / "openclaw.md").read_text(encoding="utf-8")
    main_agent = (OPENCLAW / "main-agent-delivery.md").read_text(encoding="utf-8")

    assert "主动投递" in readme and "delivery.mode=\"none\"" in readme
    assert "投递渠道预检" in openclaw
    assert "只代表 agent turn 跑完，不代表消息送达" in openclaw
    assert "NO_REPLY" in openclaw
    assert "main-agent-delivery.md" in openclaw
    assert "原始" in main_agent and "sessions_send" in main_agent
    assert "内部" in main_agent and "一条最终结果" in main_agent
    assert "qqbot" not in readme.lower() and "weixin" not in readme.lower()


def test_openclaw_setup_does_not_silently_accept_defaults_and_reports_results():
    openclaw = (ROOT / "docs" / "harnesses" / "openclaw.md").read_text(encoding="utf-8")
    assert "不得静默采用默认值" in openclaw
    assert "一次只问一个问题" in openclaw
    assert "setup 结束报告" in openclaw
    assert "请按编号回复" not in openclaw
    assert "活动签到是否代领" not in openclaw
    assert "理智液偏好" not in openclaw
    assert "setup 过程中遇到的错误" in openclaw
    assert "用户没有答复、仍靠默认值运行的 setup 必需项" in openclaw
    assert "在得到答复前仍算未确认" in openclaw
    assert "不写 `setup.json`" in openclaw
    assert "报告不是批量确认" in openclaw


def test_daily_cron_timeout_covers_whole_turn():
    openclaw = (ROOT / "docs" / "harnesses" / "openclaw.md").read_text(encoding="utf-8")
    assert "10800" in openclaw
    assert "覆盖整个定时 turn" in openclaw
    assert "不要把它和单个 MAA 日常项的 `task_timeout_min` 对齐" in openclaw


def test_openclaw_long_tasks_do_not_rely_on_missing_origin_wakeup():
    openclaw = (ROOT / "docs" / "harnesses" / "openclaw.md").read_text(encoding="utf-8")
    assert "不要结束当前 agent turn" in openclaw
    assert "同一个 cron turn 内" in openclaw
    assert "Session event origin is missing" in openclaw
    assert "不要重跑 `daily run`" in openclaw
    assert "只补执行 `maapilot daily done`" in openclaw
