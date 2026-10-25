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
    assert "deleteAfterRun" not in job
    message = job["payload"]["message"]
    assert "重复定时" in message and "不要" in message and "下一次一次性定时" in message


def test_daily_job_stays_silent_until_one_final_delivery():
    message = _json("cron-daily.json")["payload"]["message"]
    assert "不要输出进度" in message
    assert "NO_REPLY" in message
    assert "只有 daily done 成功后" in message
    assert "仅生成一条最终总结" in message
    assert "message/conversations_send/sessions_send" in message


def test_daily_job_polls_background_work_without_relying_on_completion_handoff():
    message = _json("cron-daily.json")["payload"]["message"]
    assert "当前 turn 内用 process 持续 poll 到退出" in message
    assert "不要结束 turn 等待完成事件自动唤醒" in message
    assert "Session event origin is missing" in message


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


def test_openclaw_setup_ends_with_one_issue_and_decision_summary():
    openclaw = (ROOT / "docs" / "harnesses" / "openclaw.md").read_text(encoding="utf-8")
    assert "setup 收尾确认" in openclaw
    assert "setup 过程中遇到的错误" in openclaw
    assert "未经用户明确答复而采用的默认值" in openclaw
    assert "用户确认后才能写 `private/setup.json`" in openclaw
    assert "用户尚未回复时" in openclaw and "setup 保持未完成" in openclaw
    assert "沿用还是修改" in openclaw


def test_openclaw_long_tasks_do_not_rely_on_missing_origin_wakeup():
    openclaw = (ROOT / "docs" / "harnesses" / "openclaw.md").read_text(encoding="utf-8")
    assert "不要结束当前 agent turn" in openclaw
    assert "同一个 cron turn 内" in openclaw
    assert "Session event origin is missing" in openclaw
    assert "不要重跑 `daily run`" in openclaw
    assert "只补执行 `maapilot daily done`" in openclaw
