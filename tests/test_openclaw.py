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
