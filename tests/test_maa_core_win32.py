"""PC 客户端跑 MAA 任务的那条路（maa_cmd._run_core + CoreMaaRun）：结果要长得跟 maa-cli 那条一样。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from maapilot.tools import maa_cmd


@dataclass
class FakeCoreRun:
    """maacore.CoreRun 的替身：只带 _run_core 用到的字段。"""
    returncode: int | None = 0
    events: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    log: Path | None = None
    duration_s: float = 12.5
    timed_out: bool = False
    error: str | None = None
    core_log: Path | None = None
    log_start: int = 0
    task_errors: list = field(default_factory=list)
    chain_failed: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.returncode == 0


def _patch(monkeypatch, fake):
    from maapilot.crew import maacore
    monkeypatch.setattr(maacore, "run_task", lambda *a, **kw: fake)


def test_ok_run_looks_like_maa_cli(monkeypatch, tmp_path):
    log = tmp_path / "award.log"
    log.write_text("# stub\n", encoding="utf-8")
    _patch(monkeypatch, FakeCoreRun(log=log, duration_s=27.9))
    run = maa_cmd._run_core("award", "Award", {}, timeout_s=60)
    assert run.ok is True
    assert run.duration_s == 27.9
    assert run.log == log
    assert run.parsed["tasks"] == [{"name": "award", "status": "Completed"}]
    assert run.offline is False


def test_failed_run_reports_error_status(monkeypatch, tmp_path):
    log = tmp_path / "award.log"
    log.write_text("boom\n", encoding="utf-8")
    _patch(monkeypatch, FakeCoreRun(returncode=12, error="任务参数被拒", log=log))
    run = maa_cmd._run_core("award", "Award", {}, timeout_s=60)
    assert run.ok is False
    assert run.parsed["tasks"][0]["status"] == "Error"
    assert run.error == "任务参数被拒"


def test_offline_detected_from_messages(monkeypatch, tmp_path):
    log = tmp_path / "startup.log"
    log.write_text("GameOffline happened\n", encoding="utf-8")
    _patch(monkeypatch, FakeCoreRun(returncode=1, log=log,
                                    messages=[{"msg": 3, "details": {"what": "GameOffline"}}]))
    run = maa_cmd._run_core("startup", "StartUp", {}, timeout_s=60)
    assert run.offline is True


def test_core_run_ok_requires_completed_tasks():
    run = maa_cmd.CoreMaaRun(argv=[], returncode=0, stdout="", log=Path("x.log"), duration_s=1.0,
                             parsed={"tasks": [{"name": "award", "status": "Error"}]})
    assert run.ok is False
    run2 = maa_cmd.CoreMaaRun(argv=[], returncode=0, stdout="", log=Path("x.log"), duration_s=1.0,
                              parsed={"tasks": [{"name": "award", "status": "Completed"}]})
    assert run2.ok is True


def test_timed_out_run_is_not_ok():
    run = maa_cmd.CoreMaaRun(argv=[], returncode=0, stdout="", log=Path("x.log"), duration_s=60.0,
                             timed_out=True, parsed={"tasks": [{"name": "fight", "status": "Completed"}]})
    assert run.ok is False


def test_copilot_pc_passes_support_options_to_maacore(monkeypatch, tmp_path):
    from maapilot.crew import maacore

    plan = tmp_path / "example.json"
    plan.write_text('{"stage_name":"act55side_02"}', encoding="utf-8")
    core_dir = tmp_path / "core"
    monkeypatch.setattr(maacore, "root", lambda: core_dir)
    opts = {"uri": str(plan), "formation": True, "formation_index": 4,
            "support": 2, "support_name": "娜仁图亚", "loop_times": 2}
    _, info = maa_cmd._copilot_argv(opts)
    params = maa_cmd._copilot_params(info, opts)
    assert Path(params["filename"]).read_bytes() == plan.read_bytes()
    assert params["formation"] is True
    assert params["formation_index"] == 4
    assert params["support_unit_usage"] == 2
    assert params["support_unit_name"] == "娜仁图亚"
    assert params["loop_times"] == 2


# --- 识别（state box / depot）：MaaCore 回报的名字是 "OperBoxInfo" 这种 -------------------------------

class FakeRecognizeRun:
    def __init__(self, extra, ok=True):
        self._extra = extra
        self.ok = ok
        self.error = None
        self.log = Path("recog.log")
        self.timed_out = False
        self.returncode = 0

    def extra(self, what):
        return self._extra.get(what, [])


def test_recognize_uses_info_suffix(monkeypatch):
    """MaaCore 的 SubTaskExtraInfo 名字是 kinds + "Info"（OperBox → OperBoxInfo）。"""
    from maapilot.crew import maa, maacore
    fake = FakeRecognizeRun({"OperBoxInfo": [{"own_opers": [{"name": "银灰", "level": 90}]}]})
    monkeypatch.setattr(maacore, "run_task", lambda *a, **kw: fake)
    got, run = maa._recognize_core("OperBox", 60)
    assert got["details"]["own_opers"][0]["name"] == "银灰"
    assert run is fake


def test_recognize_raises_with_core_run_error(monkeypatch):
    from maapilot.crew import maa, maacore
    fake = FakeRecognizeRun({}, ok=False)
    fake.error = "连不上"
    monkeypatch.setattr(maacore, "run_task", lambda *a, **kw: fake)
    try:
        maa._recognize_core("OperBox", 60)
    except RuntimeError as e:
        assert "连不上" in str(e)
    else:
        raise AssertionError("应该抛 RuntimeError")


# --- 刷图/剿灭：PC 端没有 maa-cli 的那些日志行，改用 MaaCore 回调算 ------------------------------------

class FakeFightRun:
    def __init__(self, extras):
        self._extras = extras

    def extra(self, what):
        return self._extras.get(what, [])


def test_fight_counts_from_stage_drops():
    cr = FakeFightRun({
        "StageDrops": [
            {"stage": {"stageCode": "1-7"}, "stats": [{"itemId": "30012", "itemName": "源岩", "addQuantity": 2},
                                                      {"itemId": "4001", "itemName": "龙门币", "addQuantity": 121}]},
            {"stage": {"stageCode": "1-7"}, "stats": [{"itemId": "30012", "itemName": "源岩", "addQuantity": 3}]},
        ],
        "SanityBeforeStage": [{"current": 480, "max": 200}, {"current": 466, "max": 200}],
    })
    got = maa_cmd._fight_from_core(cr)
    assert got["fights"] == 2
    assert got["drops"] == {"源岩": 5, "龙门币": 121}
    assert got["sanity_first"] == (480, 200)
    assert got["sanity_last"] == (466, 200)


def test_fight_without_callbacks_is_empty():
    assert maa_cmd._fight_from_core(FakeFightRun({})) == {}


def test_fight_survives_odd_payloads():
    cr = FakeFightRun({"StageDrops": [{"stats": "oops"}], "SanityBeforeStage": [{"current": "?"}]})
    got = maa_cmd._fight_from_core(cr)
    assert got.get("fights") == 1          # 有一条 StageDrops 就算打了一场
    assert "drops" not in got
    assert "sanity_first" not in got


def test_run_core_merges_fight_stats(monkeypatch, tmp_path):
    log = tmp_path / "fight.log"
    log.write_text("x\n", encoding="utf-8")
    fake = FakeCoreRun(log=log)
    fake.extra = lambda what: {"StageDrops": [{"stats": [{"itemName": "源岩", "addQuantity": 1}]}],
                               "SanityBeforeStage": [{"current": 400, "max": 200}]}.get(what, [])
    from maapilot.crew import maacore
    monkeypatch.setattr(maacore, "run_task", lambda *a, **kw: fake)
    run = maa_cmd._run_core("fight", "Fight", {}, timeout_s=60)
    assert run.parsed["fights"] == 1
    assert run.parsed["drops"] == {"源岩": 1}
    assert run.parsed["sanity_first"] == (400, 200)


def test_pc_fight_is_not_flagged_as_upstream_change():
    """PC 端没有 maa-cli 的日志行：不能因此记一条"上游格式变了"。"""
    run = maa_cmd.CoreMaaRun(argv=[], returncode=0, stdout="", log=Path("x.log"), duration_s=1.0,
                             parsed={"tasks": [{"name": "fight", "status": "Completed"}]})
    assert maa_cmd.output_problem("fight", run) is None


def test_task_chain_error_marks_failure(monkeypatch, tmp_path):
    """MaaCore 退出码 0，但回调里有 TaskChainError/SubTaskError → 这次任务其实是失败的。"""
    log = tmp_path / "recruit.log"
    log.write_text("x\n", encoding="utf-8")
    fake = FakeCoreRun(log=log)
    fake.chain_failed = [{"msg": 10000, "details": {"taskchain": "Recruit"}}]
    _patch(monkeypatch, fake)
    run = maa_cmd._run_core("recruit", "Recruit", {}, timeout_s=60)
    assert run.ok is False
    assert "Recruit" in (run.error or "")
    assert run.parsed["tasks"][0]["status"] == "Error"


# --- 剿灭：没有代理指挥 ---------------------------------------------------------------------------

def test_no_agent_record_detected(tmp_path):
    log = tmp_path / "fight.log"
    log.write_text('{"msg": 20001, "details": {"template": "UnableToAgent2.png"}}', encoding="utf-8")

    class Run:
        pass

    r = Run(); r.log = log
    assert maa_cmd._no_agent_record(r) is True


def test_no_agent_record_false_for_other_failures(tmp_path):
    log = tmp_path / "fight.log"
    log.write_text('{"msg": 20000, "details": {}}', encoding="utf-8")

    class Run:
        pass

    r = Run(); r.log = log
    assert maa_cmd._no_agent_record(r) is False
