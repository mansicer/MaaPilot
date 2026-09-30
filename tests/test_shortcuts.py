"""op upgrade / mastery、craft：快捷脚本换成假的，Game 换成假的，不碰模拟器。"""
import json

import pytest

from maapilot import cli, paths
from maapilot.crew import core, lock
from maapilot.crew import craft as crew_craft
from maapilot.crew import manufacture, mastery, shop, upgrade
from maapilot.flows import craft as craft_flow
from maapilot.flows import op
from maapilot.knowledge import items, recipes


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


class FakeGame:
    def __init__(self, *a, **k):
        pass

    def save(self, name):
        paths.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        p = paths.DEBUG_DIR / f"{name}.png"
        p.write_bytes(b"png")
        return p


@pytest.fixture(autouse=True)
def device(monkeypatch):
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    monkeypatch.setattr(core, "Game", FakeGame)
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\n", encoding="utf-8")
    monkeypatch.setattr(op, "check_operator", lambda name: {"max_levels": [50, 80, 90], "skills": 3})


def journal_rows():
    return [json.loads(ln) for p in paths.LOGS.glob("*.jsonl") for ln in p.read_text(encoding="utf-8").splitlines()]


def test_upgrade_ok(capsys, monkeypatch):
    seen = {}

    def one(g, name, elite, level, skill):
        seen.update(name=name, elite=elite, level=level, skill=skill)
        return {"name": name, "elite": 2, "level": 1, "skill": 7, "reached": True, "start": {"elite": 1, "level": 70}}
    monkeypatch.setattr(upgrade, "upgrade_one", one)
    monkeypatch.setattr(upgrade, "leave_list_unfiltered", lambda g: None)
    code, out = run(capsys, "op", "upgrade", "遥", "--elite", "2", "--level", "1", "--skill", "7")
    assert code == 0, out
    assert seen == {"name": "遥", "elite": 2, "level": 1, "skill": 7}
    assert out["data"]["before"] == {"elite": 1, "level": 70} and out["data"]["after"]["elite"] == 2
    assert out["data"]["shot"].endswith("op_upgrade_遥.png")
    (row,) = journal_rows()
    assert row["command"] == "op upgrade" and row["ok"] is True
    assert lock.current() is None


def test_upgrade_partial_target_reads_current(capsys, monkeypatch):
    monkeypatch.setattr(upgrade, "read_operator", lambda g, n: {"name": n, "elite": 1, "level": 40, "skill": 4})
    seen = {}

    def one(g, name, elite, level, skill):
        seen.update(elite=elite, level=level)
        return {"elite": elite, "level": level, "skill": skill, "reached": True, "start": {"elite": 1, "level": 40}}
    monkeypatch.setattr(upgrade, "upgrade_one", one)
    monkeypatch.setattr(upgrade, "leave_list_unfiltered", lambda g: None)
    assert run(capsys, "op", "upgrade", "遥", "--level", "60")[0] == 0 and seen == {"elite": 1, "level": 60}
    assert run(capsys, "op", "upgrade", "遥", "--elite", "2")[0] == 0 and seen == {"elite": 2, "level": 1}


def test_upgrade_failure_is_honest(capsys, monkeypatch):
    def boom(g, name, elite, level, skill):
        e = upgrade.UpgradeError("promotion to 精2 not confirmed")
        e.reached = {"elite": 1, "level": 80}
        raise e
    monkeypatch.setattr(upgrade, "upgrade_one", boom)
    code, out = run(capsys, "op", "upgrade", "遥", "--elite", "2", "--level", "1")
    assert code == 1 and out["error"]["code"] == "shortcut_failed"
    assert "not confirmed" in out["error"]["message"]
    assert out["data"]["reached"] == {"elite": 1, "level": 80} and out["data"]["shot"].endswith("op_upgrade_error_遥.png")
    assert out["data"]["stage"] == "升级 / 精英化 / 升技能"
    assert journal_rows()[0]["ok"] is False and lock.current() is None


def test_upgrade_not_reached(capsys, monkeypatch):
    monkeypatch.setattr(upgrade, "upgrade_one", lambda *a: {"elite": 1, "level": 60, "skill": 7, "reached": False})
    monkeypatch.setattr(upgrade, "leave_list_unfiltered", lambda g: None)
    code, out = run(capsys, "op", "upgrade", "遥", "--elite", "1", "--level", "70")
    assert code == 1 and out["error"]["code"] == "shortcut_failed"


@pytest.mark.parametrize("argv", [["遥"], ["遥", "--elite", "3"], ["遥", "--elite", "1", "--level", "81"],
                                  ["遥", "--skill", "8"]])
def test_upgrade_bad_args(capsys, argv):
    code, out = run(capsys, "op", "upgrade", *argv)
    assert code == 2 and not paths.LOGS.exists()


@pytest.mark.parametrize("status,code,err", [
    ("started", 0, None), ("reached", 0, None), ("room busy", 1, "training_busy"),
    ("blocked: need farming {\"x\": 1}", 1, "short_materials"), ("rank unknown", 1, "shortcut_failed"),
])
def test_mastery_statuses(capsys, monkeypatch, status, code, err):
    seen = {}

    def start(g, name, skill, depot, max_rank=None):
        seen.update(name=name, skill=skill, max_rank=max_rank)
        return {"name": name, "skill": skill, "status": status, "target_rank": 1, "hours": 8}
    monkeypatch.setattr(mastery, "start_mastery", start)
    c, out = run(capsys, "op", "mastery", "遥", "--skill", "2", "--rank", "3")
    assert c == code and seen == {"name": "遥", "skill": 2, "max_rank": 3}
    assert (out["error"] or {}).get("code") == err


def test_craft_workshop(capsys, monkeypatch):
    monkeypatch.setattr(items, "resolve_item", lambda n: "30073")
    monkeypatch.setattr(items, "display_name", lambda i: "聚酸酯块")
    monkeypatch.setattr(recipes, "workshop_by_name", lambda *a: {"聚酸酯块": {"count": 1}})
    monkeypatch.setattr(craft_flow, "_offer", lambda iid: None)
    done = []
    monkeypatch.setattr(crew_craft, "goto_workshop", lambda g: None)
    monkeypatch.setattr(crew_craft, "craft_once", lambda g, n, t: done.append((n, t)))
    code, out = run(capsys, "craft", "聚酸酯块", "--count", "3")
    assert code == 0 and done == [("聚酸酯块", 3)] and out["data"]["station"] == "WORKSHOP"


def test_craft_not_craftable(capsys, monkeypatch):
    monkeypatch.setattr(items, "resolve_item", lambda n: "30011")
    monkeypatch.setattr(items, "display_name", lambda i: "源岩")
    monkeypatch.setattr(recipes, "workshop_by_name", lambda *a: {})
    monkeypatch.setattr(craft_flow, "_offer", lambda iid: None)
    code, out = run(capsys, "craft", "源岩")
    assert code == 2 and out["error"]["code"] == "not_craftable"


def test_craft_dual_chip_not_restored(capsys, monkeypatch):
    monkeypatch.setattr(items, "resolve_item", lambda n: "3263")
    monkeypatch.setattr(items, "display_name", lambda i: "辅助双芯片")
    monkeypatch.setattr(manufacture, "make", lambda n, t: {"item": n, "made": t, "original": "赤金",
                                                          "restored": False, "shot": "private/debug/x.png"})
    code, out = run(capsys, "craft", "辅助双芯片", "--count", "2")
    assert code == 1 and out["error"]["code"] == "not_restored" and out["data"]["made"] == 2


def test_craft_shop_aborted(capsys, monkeypatch):
    from maapilot.knowledge.shops import Offer
    monkeypatch.setattr(items, "resolve_item", lambda n: "32001")
    monkeypatch.setattr(items, "display_name", lambda i: {"32001": "芯片助剂", "4006": "采购凭证"}.get(i, i))
    monkeypatch.setattr(craft_flow, "_offer", lambda iid: Offer(item="32001", shop="凭证交易所", tab="采购凭证区",
                                                                currency="4006", price=90))

    def buy(name, count, *, shop, tab, unit_price):
        raise shop_mod.ShopAborted("余额 50 不够 90", balance=50)
    shop_mod = shop
    monkeypatch.setattr(shop, "buy", buy)
    code, out = run(capsys, "craft", "芯片助剂")
    assert code == 1 and out["error"]["code"] == "not_bought" and out["data"]["balance"] == 50
    monkeypatch.setattr(shop, "buy", lambda n, c, **k: {"before": 700, "after": 610})
    code, out = run(capsys, "craft", "芯片助剂")
    assert code == 0 and out["data"]["currency"] == "采购凭证" and out["data"]["balance_after"] == 610


def test_busy_device(capsys, monkeypatch):
    held = lock.acquire("other")
    try:
        monkeypatch.setattr(lock.os, "getpid", lambda: 4242)
        monkeypatch.setattr(lock, "pid_alive", lambda pid: True)
        code, out = run(capsys, "op", "mastery", "遥", "--skill", "1", "--rank", "1")
    finally:
        monkeypatch.undo()
        held.release()
    assert code == 4 and out["error"]["code"] == "busy"
