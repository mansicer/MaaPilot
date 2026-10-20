"""builder A 的工具命令：doctor / screen / input / maa / state / data / calc / guard / lock。
不碰真机：maa-cli 换成假脚本，MaaFramework 的 Game 换成假对象，adb 走假的 Probe。"""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

from maapilot import cli, paths
from maapilot.cliutil import CliError
from maapilot.crew import lock
from maapilot.tools import common
from tests.portable import WINDOWS, fake_program

HAS_DATA = (paths.DATA / "character_table.json").exists() and (paths.DATA / "stage_table.json").exists()
needs_data = pytest.mark.skipif(not HAS_DATA, reason="没有 data/ 游戏数据表")


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def _no_external_maa(monkeypatch):
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])


@pytest.fixture
def cfg(_tmp_private, tmp_path, monkeypatch):
    """config.yaml + 假的 maa：FAKE_MAA_DIR 下的 stdout.txt / log.txt / rc 决定它的输出。"""
    fake_dir = tmp_path / "fakemaa"
    fake_dir.mkdir()
    script = fake_program(fake_dir / "maa", f"""\
        import os, sys, pathlib
        d = pathlib.Path({str(fake_dir)!r})
        (d / "argv.txt").write_text("\\n".join(sys.argv[1:]))
        (d / "env.txt").write_text(os.environ.get("MAA_CONFIG_DIR", ""))
        for a in sys.argv:
            if a.startswith("--log-file="):
                src = d / "log.txt"
                pathlib.Path(a.split("=", 1)[1]).write_text(src.read_text() if src.exists() else "")
        out = d / "stdout.txt"
        sys.stdout.write(out.read_text() if out.exists() else "")
        rc = d / "rc"
        sys.exit(int(rc.read_text()) if rc.exists() else 0)
        """)
    _tmp_private.mkdir(parents=True, exist_ok=True)
    (_tmp_private / "config.yaml").write_text(
        f"emulator:\n  address: 'emu-test:1'\ntools:\n  maa: {str(script)!r}\nserver: Official\n", encoding="utf-8")
    return fake_dir


# --- maa-cli 输出解析 --------------------------------------------------------------------------

SUMMARY = """Summary
-----------------------------------
[fight] 21:45:00 - 21:50:12 (5m 12s) Completed
Fight 1-7 2 times, used 0 medicine
total drops: 固源岩 × 3
"""
FIGHT_LOG = """[2026-09-30 21:45:01 INFO] Current sanity: 82/135
[2026-09-30 21:45:02 INFO] Mission started
[2026-09-30 21:46:02 INFO] Drops: 固源岩 × 2, 龙门币 × 72
[2026-09-30 21:46:03 INFO] Mission started
[2026-09-30 21:47:02 INFO] Drops: 固源岩 × 1, 龙门币 × 72
[2026-09-30 21:47:05 INFO] Current sanity: 70/135
"""
PROXY_FAIL_LOG = """[2026-10-13 05:20:43 INFO ] Current sanity: 49/205
[2026-10-13 05:20:45 INFO ] Mission started (1 times, use 21 sanity)
[2026-10-13 05:23:45 INFO ] Drops: 理智 × 21
[2026-10-13 05:23:50 INFO ] Mission started (1 times, use 21 sanity)
[2026-10-13 05:26:45 INFO ] Drops: 全新装置 × 1, 未来瞻望镜 × 21
[2026-10-13 05:27:00 INFO ] Current sanity: 28/205
"""


def test_parse_summary_and_timeline(tmp_path):
    from maapilot.crew import maa
    log = tmp_path / "x.log"
    log.write_text(FIGHT_LOG, encoding="utf-8")
    p = maa.parse_output(SUMMARY, log)
    assert p["tasks"][0]["status"] == "Completed" and p["fight_times"] == 2
    assert p["fights"] == 2 and p["drops"] == {"固源岩": 3, "龙门币": 144}
    assert p["sanity_first"] == [82, 135] and p["sanity_last"] == [70, 135]
    assert p["offline_seen"] is False


def test_proxy_failure_is_not_a_drop(tmp_path):
    """代理失败：游戏退回理智，MAA 记成掉落"理智 × 21"。不算掉落、不算用掉的理智，单独报出来。"""
    from types import SimpleNamespace
    from maapilot.crew import maa
    from maapilot.tools import maa_cmd
    log = tmp_path / "x.log"
    log.write_text(PROXY_FAIL_LOG, encoding="utf-8")
    p = maa.timeline(log)
    assert p["fights"] == 2 and p["proxy_failed"] == 1 and p["proxy_refund"] == 21
    assert p["drops"] == {"全新装置": 1, "未来瞻望镜": 21}
    run = SimpleNamespace(parsed=p, ok=True, duration_s=400, offline=False, log=log)
    d = maa_cmd.result("fight", run, {"code": "YW-6", "ap_cost": 21})
    assert d["times_done"] == 2 and d["sanity_used"] == 21 and d["proxy_failed"] == 1
    assert "代理失败" in d["note"] and all(x["name"] != "理智" for x in d["drops"])


def test_to_toml():
    from maapilot.crew import maa
    text = maa.to_toml([{"name": "f", "type": "Fight", "params": {"stage": "1-7", "stone": 0, "x": [1, "a"]}}])
    import tomllib
    assert tomllib.loads(text)["tasks"][0]["params"] == {"stage": "1-7", "stone": 0, "x": [1, "a"]}


# --- maa 参数 ---------------------------------------------------------------------------------

def test_params_forbid_stone_and_medicine(cfg):
    from maapilot import config
    from maapilot.cliutil import CliError
    from maapilot.tools import maa_cmd
    c = config.load()
    for bad in ('{"stone": 1}', '{"medicine": 3}', '{"use_sanity_potion": true}'):
        with pytest.raises(CliError) as e:
            maa_cmd.build_params("fight", {"stage": "X-1", "params": bad}, c)
        assert e.value.exit == 2 and e.value.code == "forbidden_param"
    c["daily"]["params"]["recruit"] = {"stone": 2}
    with pytest.raises(CliError):
        maa_cmd.build_params("recruit", {}, c)
    with pytest.raises(CliError) as e:
        maa_cmd.build_params("mall", {"params": '{"no_such": 1}'}, config.load())
    assert e.value.code == "bad_params"


def test_fight_params_default_zero(cfg):
    from maapilot import config
    from maapilot.tools import maa_cmd
    t, p, _ = maa_cmd.build_params("fight", {"stage": "ZZ-9", "times": 3}, config.load())
    assert t == "Fight" and p["stone"] == 0 and p["medicine"] == 0 and p["times"] == 3
    assert p["client_type"] == "Official" and p["server"] == "CN"
    t, p, _ = maa_cmd.build_params("annihilation", {"medicine": 1}, config.load())
    assert p["stage"] == "Annihilation" and p["times"] == 1 and p["medicine"] == 1 and p["stone"] == 0


def test_copilot_argv_flags(tmp_path):
    from maapilot.tools import maa_cmd
    plan = tmp_path / "123.json"
    plan.write_text("{}", encoding="utf-8")
    fetched = []

    def fetch(pid):
        fetched.append(pid)
        return plan
    f = str(plan.resolve())
    argv, info = maa_cmd._copilot_argv({"uri": "prts://123", "formation": True}, fetch)
    assert argv == ["copilot", f, "--formation"] and info == {"uri": "prts://123", "file": f, "formation": True}
    argv, info = maa_cmd._copilot_argv({"uri": "maa://123", "formation": True, "formation_index": 2, "add_trust": True,
                                        "ignore_requirements": True, "support": 2, "support_name": "棘刺",
                                        "raid": "raid", "loop_times": 3}, fetch)
    assert argv == ["copilot", f, "--formation", "--formation-index", "2", "--add-trust",
                    "--ignore-requirements", "--support-unit-usage", "2", "--support-unit-name", "棘刺",
                    "--raid", "raid", "--loop-times", "3"]
    assert info["uri"] == "prts://123" and info["ignore_requirements"] is True and info["support"] == 2
    assert info["raid"] == "raid" and "--use-sanity-potion" not in argv
    argv, _ = maa_cmd._copilot_argv({"uri": "123", "raid": "normal", "support": 0}, fetch)
    assert argv == ["copilot", f] and fetched == [123, 123, 123]
    argv, info = maa_cmd._copilot_argv({"uri": str(plan)}, fetch)                 # 本地文件原样给
    assert argv == ["copilot", f] and info["uri"] == f and fetched == [123, 123, 123]


def test_copilot_plan_uris():
    from maapilot.tools import maa_cmd
    assert [maa_cmd.plan_id_of(u) for u in ("prts://64408", "maa://64408", " 64408 ", "PRTS://1")] == [64408, 64408,
                                                                                                         64408, 1]
    assert maa_cmd.plan_id_of("prts://s12") is None and maa_cmd.plan_id_of("a.json") is None
    with pytest.raises(CliError) as e:
        maa_cmd._copilot_argv({"uri": "prts://s12"}, lambda pid: None)
    assert e.value.code == "bad_arguments"


def test_fetch_plan_saves_content(monkeypatch):
    from maapilot.tools import copilot_cmd, maa_cmd
    monkeypatch.setattr(copilot_cmd, "get", lambda pid: {"id": pid, "content": '{"stage_name": "x", "actions": []}'})
    p = maa_cmd.fetch_plan(7)
    assert p == paths.PRIVATE / "cache" / "copilot" / "7.json"
    assert json.loads(p.read_text(encoding="utf-8")) == {"stage_name": "x", "actions": []}
    monkeypatch.setattr(copilot_cmd, "get", lambda pid: {"id": pid, "content": "坏的"})
    with pytest.raises(CliError) as e:
        maa_cmd.fetch_plan(8)
    assert e.value.code == "bad_plan"


@needs_data
def test_fight_not_open(cfg, monkeypatch):
    from maapilot import config
    from maapilot.cliutil import CliError
    from maapilot.knowledge import calendar, stages
    from maapilot.tools import maa_cmd
    days = stages.open_weekdays("PR-C-2")
    closed = next(d for d in range(1, 8) if d not in days)
    monkeypatch.setattr(calendar, "game_weekday", lambda at=None: closed)
    with pytest.raises(CliError) as e:
        maa_cmd.build_params("fight", {"stage": "PR-C-2"}, config.load())
    assert e.value.code == "not_open" and e.value.exit == 1


def test_maa_fight_end_to_end(cfg, capsys):
    (cfg / "stdout.txt").write_text(SUMMARY, encoding="utf-8")
    (cfg / "log.txt").write_text(FIGHT_LOG, encoding="utf-8")
    code, env = run(capsys, "maa", "fight", "ZZ-9", "--times", "2")
    assert code == 0, env
    d = env["data"]
    assert d["times_done"] == 2 and d["medicine_used"] == 0 and d["sanity"] == {"current": 70, "max": 135}
    assert {x["name"]: x["count"] for x in d["drops"]}["固源岩"] == 3
    argv = (cfg / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert argv[:2] == ["run", "maapilot-fight"] and "--addr" in argv and "emu-test:1" in argv
    assert (cfg / "env.txt").read_text(encoding="utf-8") == str(paths.PRIVATE / "maa-config")
    task_file = paths.PRIVATE / "maa-config" / "tasks" / "maapilot-fight.toml"
    assert "stone = 0" in task_file.read_text(encoding="utf-8") and "medicine = 0" in task_file.read_text(encoding="utf-8")
    assert json.loads((paths.STATE / "sanity.json").read_text(encoding="utf-8"))["current"] == 70
    rows = [json.loads(l) for l in next(paths.LOGS.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["command"] == "maa fight" and rows[-1]["ok"] is True
    assert not paths.LOCK_FILE.exists()


def test_maa_failure_and_offline(cfg, capsys):
    (cfg / "stdout.txt").write_text("Summary\n[recruit] 10:00:00 - 10:01:00 (1m) Error\n", encoding="utf-8")
    (cfg / "log.txt").write_text("[x ERROR] something broke\n", encoding="utf-8")
    (cfg / "rc").write_text("1", encoding="utf-8")
    code, env = run(capsys, "maa", "recruit")
    assert code == 1 and env["error"]["code"] == "maa_failed" and env["data"]["errors"]
    (cfg / "log.txt").write_text("[x] GameOffline\n", encoding="utf-8")
    code, env = run(capsys, "maa", "recruit")
    assert code == 1 and env["error"]["code"] == "offline"


def test_maa_busy_reports_holder(cfg, capsys, monkeypatch):
    monkeypatch.setattr(lock, "pid_alive", lambda pid: True)
    common.write_json(paths.LOCK_FILE, {"owner": "daily", "pid": os.getpid() + 999999, "host": lock._host(),
                                        "job": "daily run", "token": "secret", "acquired_at": common.iso(),
                                        "heartbeat_at": common.iso(), "ttl_s": 900})
    code, env = run(capsys, "maa", "award")
    assert code == 4 and env["error"]["code"] == "busy"
    assert env["data"]["holder"]["job"] == "daily run" and "token" not in env["data"]["holder"]


# --- state / guard --------------------------------------------------------------------------------

def _depot_log(items: dict) -> str:
    inner = json.dumps({"data": json.dumps(items)})
    return f"[info] Depot: {{\"details\": {inner}}}\n"


def test_state_depot_and_show(cfg, capsys):
    (cfg / "stdout.txt").write_text("Summary\n[Depot] 10:00:00 - 10:01:00 (1m) Completed\n", encoding="utf-8")
    (cfg / "log.txt").write_text(_depot_log({"4001": 1000, "4003": 3200, "30012": 5}), encoding="utf-8")
    code, env = run(capsys, "state", "depot")
    assert code == 0, env
    assert {i["id"]: i["count"] for i in env["data"]["items"]}["4003"] == 3200
    (cfg / "log.txt").write_text(_depot_log({"4001": 1500, "4003": 3200, "30012": 2}), encoding="utf-8")
    code, env = run(capsys, "state", "depot")
    assert {c["id"]: c["delta"] for c in env["data"]["changed"]} == {"30012": -3, "4001": 500}
    code, env = run(capsys, "state", "show", "depot")
    assert code == 0 and env["data"]["age_min"] == 0
    code, env = run(capsys, "state", "show", "box")
    assert code == 2 and env["error"]["code"] == "no_snapshot"
    rows = [json.loads(l) for l in next(paths.LOGS.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()]
    depot = [r for r in rows if r["command"] == "state depot"]
    assert depot[-1]["depot_change"]
    assert depot[-1]["result"] == {"items": 3, "lmd": 1500, "orundum": 3200, "changed": 2}
    assert rows[-1]["command"] == "state show" and rows[-1]["exit"] == 2 and rows[-1]["error"] == "no_snapshot"


def test_guard_evaluate_pure():
    from maapilot.tools.guard import evaluate
    allowed, used, unexplained = evaluate({"originite": 10, "orundum": 3200}, {"originite": 10, "orundum": 1400},
                                          [{"item": "orundum", "amount": 1800}])
    assert unexplained == {} and used == {0: 1800} and allowed["orundum"] == 1800
    _, _, unexplained = evaluate({"originite": 10, "orundum": 3200}, {"originite": 9, "orundum": 1100},
                                 [{"item": "orundum", "amount": 100}])
    assert unexplained == {"originite": -1, "orundum": -2000}
    # 上千的数读不准：精确值 16866 对仓库约数 16000，减少不到 1000 不算花费；源石这种小数照样精确比
    _, _, unexplained = evaluate({"originite": 16, "orundum": 16866}, {"originite": 16, "orundum": 16000}, [])
    assert unexplained == {}
    _, _, unexplained = evaluate({"originite": 16, "orundum": 16866}, {"originite": 15, "orundum": 15800}, [])
    assert unexplained == {"originite": -1, "orundum": -1066}
    _, used, unexplained = evaluate({"originite": 1, "orundum": 100}, {"originite": 5, "orundum": 900}, [])
    assert unexplained == {} and used == {}


def test_guard_flow(cfg, capsys, monkeypatch):
    from maapilot.tools import guard
    now = {"v": {"originite": 12, "orundum": 3200}}
    monkeypatch.setattr(guard, "read_now", lambda: dict(now["v"]))
    code, env = run(capsys, "guard", "snapshot")
    assert code == 0 and env["data"]["orundum"] == 3200
    now["v"] = {"originite": 12, "orundum": 1400}
    code, env = run(capsys, "guard", "check")
    assert code == 3 and env["error"]["code"] == "currency_decreased"
    assert env["data"]["unexplained"] == {"orundum": -1800}
    assert guard.stopped() is not None
    from maapilot.tools import doctor
    monkeypatch.setattr(doctor, "PROBE", FakeProbe({"crontab -l": (0, b"", b"")}))
    code, env = run(capsys, "doctor", "--brief")
    assert env["data"]["guard_stop"] and (env["data"]["next"] in ("guard", "setup"))
    code, env = run(capsys, "guard", "allow", "合成玉", "1800", "--reason", "用户同意抽 3 次")
    assert code == 0 and env["data"]["recorded"]["item"] == "orundum"
    code, env = run(capsys, "guard", "check")
    assert code == 0 and env["data"]["resumed"] is True and env["data"]["used"][0]["amount"] == 1800
    assert guard.stopped() is None
    code, env = run(capsys, "guard", "status")
    assert env["data"]["allowances"] == [] and env["data"]["snapshot"]["orundum"] == 1400
    code, env = run(capsys, "guard", "allow", "龙门币", "1", "--reason", "x")
    assert code == 2


def test_guard_currencies_missing_counts_as_zero():
    from maapilot.cliutil import CliError
    from maapilot.tools.guard import currencies
    assert currencies({"4001": 5}) == {"originite": 0, "orundum": 0}
    with pytest.raises(CliError):
        currencies({})


# --- screen / input -----------------------------------------------------------------------------

class FakeHit:
    def __init__(self, text, box, score=0.9):
        self.text, self.box, self.score = text, box, score


class FakeGame:
    def __init__(self):
        self.calls = []
        self.connects = []                     # 每次连接是不是只读

    def connected(self, readonly: bool):
        self.connects.append(readonly)
        return self

    def screencap(self):
        return np.zeros((720, 1280, 3), dtype=np.uint8)

    def ocr(self, roi, img=None):
        return [FakeHit("终端", (1080, 150, 60, 30)), FakeHit("82/135", (1100, 40, 80, 30))]

    def tap(self, x, y, delay=0.8):
        self.calls.append(("tap", x, y))

    def swipe(self, *a, duration_ms=400, delay=0.8):
        self.calls.append(("swipe", *a, duration_ms))

    def key_back(self, delay=0):
        self.calls.append(("back",))

    def input_text(self, text, delay=0):
        self.calls.append(("text", text))


@pytest.fixture
def game(cfg, monkeypatch):
    g = FakeGame()
    monkeypatch.setattr(common, "connect", lambda ocr=False, readonly=False: g.connected(readonly))
    return g


def test_screen_shot_and_ocr(game, capsys):
    code, env = run(capsys, "screen", "shot", "--name", "home")
    assert code == 0 and env["data"]["width"] == 1280 and env["data"]["path"].endswith("-home.png")
    assert os.path.exists(env["data"]["path"])
    code, env = run(capsys, "screen", "ocr", "--find", "终端")
    assert code == 0 and [h["text"] for h in env["data"]["hits"]] == ["终端"]
    assert env["data"]["hits"][0]["center"] == [1110, 165]
    code, env = run(capsys, "screen", "ocr", "--roi", "1200", "0", "200", "10")
    assert code == 2 and env["error"]["code"] == "bad_roi"


def _foreign_lock():
    common.write_json(paths.LOCK_FILE, {"owner": "maa infrast", "pid": os.getpid() + 999999, "host": lock._host(),
                                        "job": "maa infrast", "token": "secret", "acquired_at": common.iso(),
                                        "heartbeat_at": common.iso(), "ttl_s": 900})


def test_screen_does_not_need_the_lock(game, capsys, monkeypatch):
    """截图、识字是只读的：别人拿着锁（MAA 正在跑）也能做，用只读连接，不动锁、不记"可能花了东西"；点击照样被挡。"""
    monkeypatch.setattr(lock, "pid_alive", lambda pid: True)
    _foreign_lock()
    before = paths.LOCK_FILE.read_text(encoding="utf-8")
    code, env = run(capsys, "screen", "shot")
    assert code == 0, env
    code, env = run(capsys, "screen", "ocr", "--find", "终端")
    assert code == 0 and env["data"]["hits"][0]["text"] == "终端"
    assert game.connects == [True, True]
    assert paths.LOCK_FILE.read_text(encoding="utf-8") == before
    assert not (paths.GUARD / "dirty.json").exists()
    code, env = run(capsys, "input", "tap", "10", "20")
    assert code == 4 and env["error"]["code"] == "busy" and game.calls == []


def test_readonly_game_refuses_input():
    from maapilot.crew.core import Game
    g = Game.__new__(Game)
    g.readonly = True
    for call in (lambda: g.tap(1, 2), lambda: g.swipe(1, 2, 3, 4), lambda: g.key_back(), lambda: g.input_text("a"),
                 lambda: g.long_press(1, 2, 10)):
        with pytest.raises(RuntimeError):
            call()


def test_input_commands(game, capsys):
    assert run(capsys, "input", "tap", "10", "20")[0] == 0
    assert run(capsys, "input", "swipe", "1", "2", "3", "4", "--ms", "300")[0] == 0
    assert run(capsys, "input", "back")[0] == 0
    assert run(capsys, "input", "text", "abc")[0] == 0
    assert game.calls == [("tap", 10, 20), ("swipe", 1, 2, 3, 4, 300), ("back",), ("text", "abc")]
    code, env = run(capsys, "input", "tap", "1280", "5")
    assert code == 2 and env["error"]["code"] == "bad_point"


class _Hit:
    def __init__(self, text, box):
        self.text, self.box = text, box


# 2026-09 真机首页右上角的 OCR（HOME_ROI 内，账号数字换过）
HOME_HITS = [_Hit("", (654, 66, 54, 41)), _Hit("10568025", (709, 60, 128, 38)), _Hit("<", (729, 168, 23, 25)),
             _Hit("22", (767, 133, 123, 85)), _Hit("理智/2O5", (783, 233, 100, 27)), _Hit("202030", (819, 22, 106, 24)),
             _Hit("终端", (899, 122, 134, 72)), _Hit("16566+", (907, 40, 121, 41)), _Hit("当前", (913, 204, 43, 29)),
             _Hit("16+", (1106, 23, 80, 38))]


def test_sanity_parse():
    from maapilot.tools.state import parse_sanity
    assert parse_sanity([_Hit("终端", (0, 0, 1, 1)), _Hit("82/135", (0, 0, 1, 1))]) == (82, 135)
    assert parse_sanity([_Hit("1 / 2", (0, 0, 1, 1))]) is None
    assert parse_sanity(HOME_HITS) == (22, 205)


def test_sanity_merge_ones():
    """首页理智的"1"按字形认，别的字用 OCR 读出的（真机读法见 tests/test_screens.py）。"""
    from maapilot.tools.state import merge_ones
    one, wide = True, False
    assert merge_ones("O", [one, one, wide]) == 110          # 两个 1 都丢了
    assert merge_ones("{4", [one, one, wide]) == 114         # 一个读成"{"、一个丢了
    assert merge_ones("730", [one, wide, wide]) == 130       # 1 读成 7
    assert merge_ones("47", [wide, one]) == 41
    assert merge_ones("I3O", [one, wide, wide]) == 130
    assert merge_ones("205", [wide, wide, wide]) == 205
    assert merge_ones("29", [one, wide, wide]) == 129
    assert merge_ones("2", [one, wide, wide]) is None        # 少了一个宽字：对不上，宁可读不到
    assert merge_ones("11", [wide, wide]) is None            # 宽的字不会是 1


def test_topbar_parse_and_merge():
    from maapilot.tools.guard import merge_topbar
    from maapilot.tools.state import parse_topbar
    top = parse_topbar(HOME_HITS)
    assert top == {"lmd": 10568025, "orundum": 16566, "originite": 16}
    assert parse_topbar(HOME_HITS[:3]) is None
    # 真机 10-05：龙门币前的图标认成"{"，龙门币以前读不出就整个作废，退回仓库约数、误报合成玉减少
    real = [_Hit("=", (659, 69, 49, 35)), _Hit("{10679969", (694, 59, 144, 40)), _Hit("294", (733, 135, 155, 88)),
            _Hit("2o2oo5o2:3", (818, 16, 152, 31)), _Hit("16866+", (906, 38, 122, 45)), _Hit("16+", (1105, 23, 80, 38))]
    assert parse_topbar(real) == {"lmd": 10679969, "orundum": 16866, "originite": 16}
    # 同一屏另一次识别：日期认成"2o2oo5o2:"（去掉末尾冒号就像个数），不能被当成顶栏数字
    real2 = [_Hit("{10679969", (694, 59, 144, 40)), _Hit("895", (715, 134, 175, 93)), _Hit("2o2oo5o2:", (818, 16, 152, 31)),
             _Hit("16866+", (906, 38, 122, 45)), _Hit("16+", (1107, 23, 78, 38))]
    assert parse_topbar(real2) == {"lmd": 10679969, "orundum": 16866, "originite": 16}
    no_lmd = [h for h in real if "1067" not in h.text]
    assert parse_topbar(no_lmd) == {"lmd": None, "orundum": 16866, "originite": 16}
    depot = {"orundum": 16000, "originite": 16}
    assert merge_topbar(depot, top) == {"orundum": 16566, "originite": 16}
    assert merge_topbar(depot, {**top, "orundum": 19566}) == depot           # 对不上：不信顶栏
    assert merge_topbar(depot, {**top, "originite": 61}) == depot
    assert merge_topbar(depot, None) == depot


# --- lock ---------------------------------------------------------------------------------------

def test_lock_status_release(capsys):
    code, env = run(capsys, "lock", "status")
    assert code == 0 and env["data"]["holder"] is None
    common.write_json(paths.LOCK_FILE, {"owner": "x", "pid": 1, "host": "elsewhere", "token": "t",
                                        "acquired_at": "2020-01-01T00:00:00+08:00",
                                        "heartbeat_at": "2020-01-01T00:00:00+08:00", "ttl_s": 60})
    code, env = run(capsys, "lock", "status")
    assert env["data"]["stale"] and "token" not in env["data"]["stale"]
    code, env = run(capsys, "lock", "release")
    assert code == 0 and env["data"]["released"] and not paths.LOCK_FILE.exists()
    held = lock.acquire("test")
    try:
        code, env = run(capsys, "lock", "release")
        assert code == 4
    finally:
        held.release()


# --- data / calc ---------------------------------------------------------------------------------

def test_parse_object():
    from maapilot.cliutil import CliError
    from maapilot.tools.calc import parse_object
    o = parse_object("干员 遥 → 精2 60级，技能 7级，技能3 专3，模组X 2级")
    assert o["operator"] == "遥"
    assert o["target"] == {"elite": 2, "level": 60, "skill_level": 7, "mastery": {3: 3}, "module": {"X": 2}}
    assert parse_object("材料 聚酸酯组 × 10") == {"kind": "材料", "item": "聚酸酯组", "count": 10}
    assert parse_object("关卡 12-17")["kind"] == "关卡"
    with pytest.raises(CliError):
        parse_object("干员 遥 精2")
    with pytest.raises(CliError):
        parse_object("练遥")


@needs_data
def test_calc_need_with_snapshots(capsys):
    common.write_json(paths.STATE / "depot.json", {"at": common.iso(), "items": {"4001": 100000, "30115": 1, "2004": 10}})
    common.write_json(paths.STATE / "box.json", {"at": common.iso(),
                                                 "operators": {"遥": {"elite": 1, "level": 80, "rarity": 6}}})
    code, env = run(capsys, "calc", "need", "干员 遥 → 精2 1级")
    assert code == 0, env
    d = env["data"]
    assert d["from"] == {"elite": 1, "level": 80} and d["need"]["exp"] == 0 and d["need"]["lmd"] == 180000
    assert d["have"]["exp"] == 20000 and d["short"]["lmd"] == 80000
    short = {i["name"]: i["count"] for i in d["short"]["items"]}
    assert short["聚合剂"] == 3
    code, env = run(capsys, "calc", "need", "其他 模组任务")
    assert code == 2 and env["error"]["code"] == "not_computable"
    code, env = run(capsys, "calc", "need", "干员 不存在的人 → 精1 1级")
    assert code == 2 and env["error"]["code"] == "unknown_name"


@needs_data
def test_calc_craft_and_data_lookup(capsys):
    common.write_json(paths.STATE / "depot.json", {"at": common.iso(), "items": {"30014": 1, "30044": 1, "30054": 1}})
    code, env = run(capsys, "calc", "craft", "聚合剂")
    assert code == 0 and env["data"]["steps"][-1]["item"] == "聚合剂" and env["data"]["short"] == []
    code, env = run(capsys, "calc", "craft", "源岩")
    assert code == 2 and env["error"]["code"] == "not_craftable"
    code, env = run(capsys, "data", "item", "聚合")
    assert code == 2 and "聚合剂" in env["data"]["suggestions"]
    code, env = run(capsys, "data", "stage", "1-7")
    assert code == 0 and env["data"]["ap"] == 6
    code, env = run(capsys, "data", "operator", "遥")
    assert code == 0 and env["data"]["rarity"] == 6 and len(env["data"]["elite"]) == 2


@needs_data
def test_calc_stages_plan(capsys):
    code, env = run(capsys, "calc", "stages", "固源岩=20")
    assert code == 0
    d = env["data"]
    if d["plan"]:                                    # 有掉率缓存
        assert d["sanity_total"] == sum(p["sanity"] for p in d["plan"]) and d["days"] > 0


# --- doctor ------------------------------------------------------------------------------------

class FakeProbe:
    def __init__(self, table: dict, which: dict | None = None):
        self.table, self.whichmap, self.calls = table, which or {}, []

    def run(self, argv, timeout=15, env=None):
        from maapilot.tools.doctor import Proc
        self.calls.append(argv)
        for key, res in self.table.items():
            if key in " ".join(argv):
                return Proc(*res)
        return Proc(1, b"", b"not faked")

    def which(self, name):
        return self.whichmap.get(name, f"/usr/bin/{name}")


def _png(w, h):
    import struct
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h) + b"\x00" * 8


def test_doctor_checks_with_fake_probe(cfg, monkeypatch):
    from maapilot.tools import doctor
    monkeypatch.setattr(doctor, "claude_trusted", lambda root: False)
    probe = FakeProbe({
        "adb version": (0, b"Android Debug Bridge version 1.0.41\n", b""),
        "get-state": (0, b"device\n", b""),
        "screencap": (0, _png(1920, 1080), b""),
        "/system/bin/input": (0, b"/system/bin/input\n", b""),
        "dumpsys window": (0, b"  mCurrentFocus=Window{1 u0 com.hypergryph.arknights/x}\n", b""),
        "maa version": (0, b"maa-cli v0.7.5\nMaaCore v6.17.5\n", b""),
        "crontab -l": (0, b"", b""),
    })
    checks = {c["name"]: c for c in doctor.run_checks(probe=probe)}
    assert checks["emulator"]["ok"] and checks["screen"]["ok"] and checks["input"]["ok"] and checks["game"]["ok"]
    assert checks["screen"]["resolution"] == [1920, 1080]
    assert checks["game"]["package"] == "com.hypergryph.arknights"
    assert checks["maa_cli"]["ok"] is False                                          # 资源目录没造
    assert "claude" not in checks and "codex" not in checks and "schedule" not in checks   # 不再查无头 agent、不再设定时
    assert checks["agent_config"]["ok"] and checks["legacy_schedule"]["ok"]
    paths.CONFIG.write_text(paths.CONFIG.read_text(encoding="utf-8") + "agent: codex\nschedule_options: {wake_computer: true}\n"
                            "daily: {agent_timeout_min: 90}\n", encoding="utf-8")
    item = doctor.check_agent_config(doctor.Ctx(True, probe))
    assert not item["ok"] and item["required"] is False and item["legacy"] == ["agent", "schedule_options",
                                                                               "daily.agent_timeout_min"]
    from maapilot import config
    assert "agent" not in config.DEFAULTS and config.load()["agent"] == "codex"    # 写了也不报错，只是不用
    probe.table["dumpsys window"] = (0, b"  mCurrentFocus=Window{1 u0 com.hypergryph.arknights.bilibili/x}\n", b"")
    game = {c["name"]: c for c in doctor.run_checks(probe=probe)}["game"]
    assert not game["ok"] and game["package"].endswith(".bilibili") and "Bilibili" in game["fix"]
    probe.table["dumpsys window"] = (0, b"  mCurrentFocus=Window{1 u0 com.hypergryph.arknights/x}\n", b"")
    probe.table["screencap"] = (0, _png(1280, 800), b"")
    checks = {c["name"]: c for c in doctor.run_checks(probe=probe)}
    assert not checks["screen"]["ok"] and "16:9" in checks["screen"]["detail"]
    probe.calls.clear()
    offline = {c["name"] for c in doctor.run_checks(offline=True, probe=probe)}
    assert not offline & set(doctor.DEVICE_CHECKS)
    assert not any(a[0] == "/usr/bin/adb" and len(a) > 2 for a in probe.calls)     # 只跑了 adb version


def test_doctor_candidates_when_no_address(_tmp_private):
    from maapilot.tools import doctor
    _tmp_private.mkdir(parents=True, exist_ok=True)
    (_tmp_private / "config.yaml").write_text("emulator:\n  address: ''\n", encoding="utf-8")
    probe = FakeProbe({"adb version": (0, b"adb 1\n", b""),
                       "adb devices": (0, b"List of devices attached\nemulator-5554\tdevice\n", b""),
                       "connect 127.0.0.1:5557": (0, b"connected to 127.0.0.1:5557\n", b""),   # 5557 三个系统都探测
                       "crontab -l": (1, b"", b"crontab: no crontab for x")})
    checks = {c["name"]: c for c in doctor.run_checks(probe=probe)}
    assert not checks["config"]["ok"]
    assert checks["emulator"]["candidates"] == ["emulator-5554", "127.0.0.1:5557"]


def test_doctor_brief(capsys, monkeypatch):
    from maapilot.tools import doctor
    monkeypatch.setattr(doctor, "PROBE", FakeProbe({"crontab -l": (0, f"40 4,16 * * * {paths.ROOT}/.venv/bin/maapilot daily run --trigger schedule # maapilot-v2 {paths.ROOT}\n*/15 * * * * /x/maapilot job tick\n".encode(), b"")}))
    code, env = run(capsys, "doctor", "--brief")
    assert code == 0 and env["data"]["next"] == "setup" and env["data"]["setup_done"] is False
    from maapilot import __version__
    assert env["data"]["version"]["version"] == __version__
    paths.SETUP.parent.mkdir(parents=True, exist_ok=True)
    paths.SETUP.write_text('{"done_at": "x", "version": 1}', encoding="utf-8")
    paths.GOALS.write_text("## 遥 精2\n- 状态：进行中\n- 对象：干员 遥 → 精2 1级\n\n## 别的\n- 状态：暂停\n", encoding="utf-8")
    code, env = run(capsys, "doctor", "--brief")
    d = env["data"]
    assert d["next"] is None and d["goals_active"] == 1
    for gone in ("schedule", "unseen_problems", "session"):
        assert gone not in d
    if WINDOWS:                                                            # 测试里 schtasks 指到不存在的程序：当作没有旧的定时
        assert d["legacy_schedule"] is None
    else:
        assert d["legacy_schedule"] == {"times": ["04:40", "16:40"], "backend": "cron"}
        assert any("daily unschedule" in l for l in d["lines"])
    assert d["daily_open"] is None
    assert any("目标" in l for l in d["lines"])
    # 没收尾的日常
    from datetime import timedelta
    from maapilot.flows import daily
    from maapilot.tools import common
    daily._write_open({"run_id": "20261020-0440", "trigger": "schedule", "started_at": common.iso(),
                       "depot_before": {"items": {"4001": 1}}})
    code, env = run(capsys, "doctor", "--brief")
    o = env["data"]["daily_open"]
    assert o["run_id"] == "20261020-0440" and o["trigger"] == "schedule" and o["stale"] is False and o["running"] is False
    assert "depot_before" not in o and any("还没收尾" in l and "daily done" in l for l in env["data"]["lines"])
    daily._write_open({"run_id": "20261020-0440", "trigger": "schedule",
                       "started_at": (common.now() - timedelta(hours=7)).isoformat(timespec="seconds")})
    code, env = run(capsys, "doctor", "--brief")
    assert env["data"]["daily_open"]["stale"] is True and any("超过 6 小时" in l for l in env["data"]["lines"])


def test_doctor_fix_copies_ocr_and_config(cfg, tmp_path, monkeypatch):
    from maapilot.tools import doctor
    res = tmp_path / "maares"
    for sub, files in {"PaddleOCR/det": ["inference.onnx"], "PaddleOCR/rec": ["inference.onnx", "keys.txt"]}.items():
        (res / sub).mkdir(parents=True)
        for f in files:
            (res / sub / f).write_text("x", encoding="utf-8")
    ocr = tmp_path / "ocr"
    monkeypatch.setattr(paths, "OCR_MODEL", ocr)
    from maapilot.knowledge import datasync
    monkeypatch.setattr(datasync, "missing_tables", lambda: [])
    from maapilot.crew import maacore
    monkeypatch.setattr(maacore, "find", lambda: ("v99.0.0", tmp_path))          # 不真去下载 MaaCore
    probe = FakeProbe({"dir resource": (0, f"{res}\n".encode(), b"")})
    done = {d["name"]: d for d in doctor.fix(probe)}
    assert sorted(done["ocr_model"]["done"]) == ["det.onnx", "keys.txt", "rec.onnx"]
    assert (paths.PRIVATE / "maa-config" / "profiles" / "default.toml").exists()


def test_doctor_hook_output(capsys, monkeypatch):
    from maapilot.tools import doctor
    monkeypatch.setattr(doctor, "PROBE", FakeProbe({"crontab -l": (0, b"", b"")}))
    code, out = run(capsys, "doctor", "--hook")
    assert code == 0 and set(out) == {"hookSpecificOutput", "systemMessage"}
    assert "随便说一句" in out["systemMessage"]                         # 钩子不让 agent 先开口：提示用户先说一句
    h = out["hookSpecificOutput"]
    assert h["hookEventName"] == "SessionStart"
    ctx = h["additionalContext"]
    body = json.loads(ctx[ctx.index("{"):])
    assert body["data"]["next"] == "setup" and body["ok"] is True


def test_claude_trusted_reads_config(tmp_path, monkeypatch):
    from maapilot.tools.doctor import claude_trusted
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert claude_trusted(tmp_path / "repo") is None
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {str(tmp_path.resolve()): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    assert claude_trusted(tmp_path / "repo") is True                      # 上级目录信任也算
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")
    assert claude_trusted(tmp_path / "repo") is False


@needs_data
def test_main_stage_difficulty_suffix_only_where_tough_exists():
    """MAA 只在有磨难的章节认 `-NORMAL`；第 9 章只有简单 / 标准，带了后缀 MAA 直接拒绝（"Cannot set stage 9-16-NORMAL"）。"""
    from maapilot.knowledge import stages
    assert stages.resolve_stage("9-16").maa_value == "9-16"
    assert stages.resolve_stage("10-12").maa_value == "10-12-NORMAL"
    assert stages.resolve_stage("1-7").maa_value == "1-7"


def test_ocr_sanity_far_below_estimate_is_suspect(monkeypatch):
    """首页 OCR 读得比上一份快照推出来的低很多（真机：130 读成 29）：标 suspect，带推算值和截图。"""
    from maapilot.tools import screen, state
    common_ = state.common
    common_.write_json(state.snap_path("sanity"), {"at": common_.iso(), "current": 120, "max": 205, "source": "skland"})
    monkeypatch.setattr(state, "read_home", lambda game=None: (None, (29, 205), object()))
    monkeypatch.setattr(screen, "capture", lambda g, n: (None, {"path": "private/shots/x.png"}))
    snap = state.read_sanity()
    assert snap["current"] == 29 and snap["suspect"] is True and snap["estimate"] == 120 and snap["shot"]
    monkeypatch.setattr(state, "read_home", lambda game=None: (None, (110, 205), object()))
    assert "suspect" not in state.read_sanity()


def test_maa_cli_tested_range(monkeypatch):
    from maapilot.tools import doctor
    from maapilot import toolchain
    monkeypatch.setattr(toolchain, "MAA_CLI_TESTED", ("0.7.5", "0.7.7"))
    assert doctor._ver("v0.8.0-nightly.1792274176+sha.6b0bc89") == (0, 8, 0) and doctor._ver("?") == ()
    assert [doctor.maa_cli_range(v) for v in ("0.7.4", "0.7.5", "0.7.7", "0.7.10", "0.8.0-nightly.1", "?")] == \
        ["old", None, None, "new", "new", None]
