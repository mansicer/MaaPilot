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


def test_daily_job_has_no_deployment_specific_delivery():
    job = _json("cron-daily.json")
    assert job["delivery"] == {"mode": "announce", "channel": "${CHANNEL}", "accountId": "${ACCOUNT_ID}",
                               "to": "${DELIVERY_TO}", "bestEffort": True}
    text = (OPENCLAW / "README.md").read_text(encoding="utf-8")
    assert "agents.entries" in text and "agents.list" in text
    assert "cron-oneshot.json" not in text
    assert not (OPENCLAW / "cron-oneshot.json").exists()


def _section(text: str, heading: str) -> str:
    start = text.index(f"## {heading}")
    end = text.find("\n## ", start + 3)
    return text[start:] if end < 0 else text[start:end]


def test_openclaw_does_not_replace_the_existing_one_shot_defaults():
    harnesses = (ROOT / "docs" / "harnesses.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = _section(harnesses, "Claude Code（命令行、桌面版）")
    codex = _section(harnesses, "Codex 桌面版")
    openclaw = _section(harnesses, "OpenClaw")
    assert "一次性" in claude and "recurring: false" in claude
    assert "一次性的定时任务" in codex
    assert "重复任务" in openclaw
    assert "默认用会话内的一次性定时接力" in agents
    assert "OpenClaw 例外" in agents
