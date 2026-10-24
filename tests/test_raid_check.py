"""`maa copilot --raid raid` 开打前核对关卡详情页是突袭模式：单份作业时 maa-cli 丢掉突袭设置，按屏幕上的模式打。
截图是假的：灰底上贴切换按钮的模板（MODE_ROI 里），不连模拟器、不跑真的 maa。"""
from __future__ import annotations

import json

import numpy as np
import pytest

from maapilot import cli
from maapilot.crew import battle, clears, lock
from maapilot.tools import maa_cmd
from tests.test_tools import cfg  # noqa: F401  复用假的 maa


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def screen_with(button: str | None) -> np.ndarray:
    img = np.full((720, 1280, 3), 60, dtype=np.uint8)
    if button:
        tpl = clears._template(button)
        x, y = clears.MODE_ROI[0] + 30, clears.MODE_ROI[1] + 20
        img[y:y + tpl.shape[0], x:x + tpl.shape[1]] = tpl
    return img


@pytest.mark.parametrize("button,mode", [
    ("NormalDifficulty", "raid"), ("NormalDifficulty-Chapter15", "raid"),      # 按钮是"切回常规"：现在是突袭
    ("RaidDifficulty", "normal"), ("RaidDifficulty-Chapter15", "normal"),      # 按钮是"突袭"：现在是常规
    (None, "unknown"),                                                         # 没有切换按钮：不在详情页
])
def test_detail_mode(button, mode):
    assert battle.detail_mode(screen_with(button)) == mode


@pytest.fixture
def plan(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text("{}", encoding="utf-8")
    return p


@pytest.fixture
def shots(monkeypatch):
    taken = []

    def use(button):
        monkeypatch.setattr(maa_cmd, "raid_shot", lambda: taken.append(button) or (screen_with(button), "shot.png"))
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    return use, taken


@pytest.mark.parametrize("button,mode", [("RaidDifficulty", "normal"), (None, "unknown")])
def test_raid_refused_when_not_raid_mode(cfg, plan, shots, capsys, button, mode):
    use, taken = shots
    use(button)
    code, env = run(capsys, "maa", "copilot", str(plan), "--formation", "--raid", "raid")
    assert code == 1 and env["error"]["code"] == "not_raid_mode" and "突袭" in env["error"]["hint"]
    assert env["data"]["raid_check"] == {"mode": mode, "shot": "shot.png"} and taken == [button]
    assert not (cfg / "argv.txt").exists()                                    # maa 没跑


def test_raid_runs_when_raid_mode(cfg, plan, shots, capsys):
    use, taken = shots
    use("NormalDifficulty")
    code, env = run(capsys, "maa", "copilot", str(plan), "--raid", "raid")
    assert code == 0 and env["data"]["copilot"]["raid"] == "raid"
    assert env["data"]["copilot"]["raid_check"] == {"mode": "raid", "shot": "shot.png"}
    assert "--raid" in (cfg / "argv.txt").read_text().split("\n")


def test_no_check_without_raid(cfg, plan, shots, capsys):
    use, taken = shots
    use("RaidDifficulty")
    code, env = run(capsys, "maa", "copilot", str(plan))
    assert code == 0 and taken == [] and "raid_check" not in env["data"]["copilot"]


def test_raid_both_refused(cfg, plan, shots, capsys):
    use, taken = shots
    use("NormalDifficulty")
    code, env = run(capsys, "maa", "copilot", str(plan), "--raid", "both")
    assert code == 2 and env["error"]["code"] == "bad_arguments" and taken == []
    assert not (cfg / "argv.txt").exists()
