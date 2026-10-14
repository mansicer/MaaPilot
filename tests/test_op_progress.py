"""op progress：参数 → MaaCore 的 plan、回调 → 结果；单独 MaaCore 的查找与子进程。不碰模拟器、不加载真的 MaaCore。"""
import json
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest

from maapilot import cli, paths
from maapilot.cliutil import CliError
from maapilot.crew import maacore
from maapilot.flows import op

ANGEL = {"rarity": 6, "profession": "SNIPER", "max_levels": [50, 80, 90], "skills": 3}
FANG = {"rarity": 3, "profession": "PIONEER", "max_levels": [40, 55], "skills": 1}


@pytest.fixture
def ops(monkeypatch):
    table = {"能天使": ANGEL, "芬": FANG}

    def check(name):
        if name not in table:
            raise CliError("unknown_name", f"认不出干员「{name}」", exit=2)
        return table[name]
    monkeypatch.setattr(op, "check_operator", check)


def detail(result, result_detail, **extra) -> dict:
    return {"msg": maacore.MSG_SUBTASK_EXTRA_INFO, "details": {
        "what": "OperProgressDetail", "details": {"name": "能天使", "role": "Sniper", "result": result,
                                                  "result_detail": result_detail, **extra}}}


def summary(success=0, failed=0, skipped=0) -> dict:
    return {"msg": maacore.MSG_SUBTASK_EXTRA_INFO, "details": {
        "what": "OperProgressSummary", "details": {"success": success, "failed": failed, "skipped": skipped,
                                                   "finished": []}}}


def core_run(*messages, returncode=0, **kw) -> maacore.CoreRun:
    return maacore.CoreRun(returncode, [], list(messages), Path("private/log/maa/x.log"), 12.0,
                           version="v6.19.0", **kw)


# --- 参数 ---------------------------------------------------------------------------------

def test_plan_has_role_and_steps_in_core_order(ops):
    plan, steps = op.progress_plan("能天使", 2, 7, ["3:2", "1:1"])
    assert plan == {"name": "能天使", "role": "SNIPER", "elite": 2, "skill_level": 7, "skill_mastery": [1, 0, 2]}
    assert steps == ["精英化到精2", "技能升到 7 级", "技能1 专1", "技能3 专2"]


@pytest.mark.parametrize("args,msg", [
    ((None, None, None), "至少给"),
    ((3, None, None), "--elite"),
    ((None, 1, None), "--skill-level"),
    ((None, None, ["3"]), "技能:专几"),
    ((None, None, ["3:4"]), "技能:专几"),
])
def test_plan_rejects_bad_arguments(ops, args, msg):
    with pytest.raises(CliError) as e:
        op.progress_plan("能天使", *args)
    assert e.value.exit == 2 and msg in e.value.message


def test_plan_checks_operator_limits(ops):
    with pytest.raises(CliError) as e:
        op.progress_plan("芬", 2, None, None)                      # 三星没有精二
    assert "最高精1" in e.value.message
    with pytest.raises(CliError) as e:
        op.progress_plan("芬", None, None, ["2:1"])
    assert "没有第 2 个技能" in e.value.message


# --- 回调 → 结果 ------------------------------------------------------------------------------

def test_all_satisfied_is_success(ops):
    _, steps = op.progress_plan("能天使", 2, 7, ["3:2"])
    run = core_run(detail("Success", "AlreadySatisfied", elite=2), detail("Success", "AlreadySatisfied"),
                   detail("Success", "AlreadySatisfied", skill_mastery=[0, 0, 2]), summary(success=3))
    d = op.progress_result("能天使", steps, run)
    assert [r["step"] for r in d["steps"]] == steps
    assert {r["detail"] for r in d["steps"]} == {"AlreadySatisfied"}
    assert d["steps"][2]["skill_mastery"] == [0, 0, 2] and d["training_started"] is False
    assert d["summary"] == {"success": 3, "failed": 0, "skipped": 0} and d["core"] == "v6.19.0"


def test_mastery_started_one_rank(ops):
    _, steps = op.progress_plan("能天使", None, None, ["3:3"])
    d = op.progress_result("能天使", steps, core_run(detail("Success", "PrerequisiteTraining", skill_mastery=[0, 0, 2]),
                                                     summary(success=1)))
    assert d["training_started"] is True and d["steps"][0]["text"].startswith("开了前一级专精")


def test_enum_numbers_are_named(ops):
    _, steps = op.progress_plan("能天使", 2, None, None)
    d = op.progress_result("能天使", steps, core_run(detail(0, 1, elite=2), summary(success=1)))
    assert d["steps"][0]["result"] == "Success" and d["steps"][0]["detail"] == "AlreadySatisfied"


@pytest.mark.parametrize("res,det,code", [
    ("Failed", "ResourceInsufficient", "short_materials"),
    ("Failed", "PrerequisiteNotMet", "prerequisite_not_met"),
    ("Skipped", "TrainingRoomBusy", "training_busy"),
    ("Failed", "RecognitionFailed", "shortcut_failed"),
])
def test_failures_map_to_codes(ops, res, det, code):
    _, steps = op.progress_plan("能天使", None, None, ["3:3"])
    with pytest.raises(CliError) as e:
        op.progress_result("能天使", steps, core_run(detail(res, det), summary(failed=1)))
    assert e.value.code == code and e.value.exit == 1
    assert e.value.data["steps"][0]["step"] == "技能3 专3"


def test_single_failure_with_several_steps_is_locating(ops):
    """找干员失败（RecognitionFailed / Interrupt 也可能）只有一条，后面的步骤都没做：不能算成第一步。"""
    _, steps = op.progress_plan("能天使", 2, 7, ["3:2"])
    with pytest.raises(CliError) as e:
        op.progress_result("能天使", steps, core_run(detail("Failed", "RecognitionFailed"), summary(failed=1)))
    assert e.value.data["steps"][0]["step"] == "找干员" and "找干员" in e.value.message


def test_no_summary_or_runner_error(ops):
    _, steps = op.progress_plan("能天使", 2, None, None)
    with pytest.raises(CliError) as e:
        op.progress_result("能天使", steps, core_run())
    assert e.value.code == "maa_failed" and "没给结果" in e.value.message
    with pytest.raises(CliError) as e:
        op.progress_result("能天使", steps, core_run(returncode=11, error="MaaCore 连不上模拟器"))
    assert e.value.code == "maa_failed" and e.value.exit == 1
    with pytest.raises(CliError) as e:
        op.progress_result("能天使", steps, core_run(returncode=10, error="MaaCore 或资源加载失败"))
    assert e.value.code == "maacore_broken" and e.value.exit == 5
    with pytest.raises(CliError) as e:
        op.progress_result("能天使", steps, core_run(returncode=None, timed_out=True))
    assert "超时" in e.value.message


# --- 命令：同意、本体、回首页 --------------------------------------------------------------------

@pytest.fixture
def fake_device(monkeypatch):
    from maapilot.tools import common

    class Lk:
        @contextmanager
        def keepalive(self):
            yield
    calls = []

    @contextmanager
    def device(owner, job=None):
        calls.append(owner)
        yield Lk()
    monkeypatch.setattr(common, "device", device)
    return calls


def _write_box(elite: int):
    paths.STATE.mkdir(parents=True, exist_ok=True)
    (paths.STATE / "box.json").write_text(json.dumps({"operators": {"能天使": {"elite": elite}}}), encoding="utf-8")


def test_elite2_of_6star_needs_catalyst_consent(ops, fake_device, capsys):
    _write_box(1)
    assert cli.main(["op", "progress", "能天使", "--elite", "2"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "needs_consent" and "--allow-catalyst" in out["error"]["hint"]
    assert fake_device == []                                       # 没碰模拟器


def test_already_elite2_or_consented_goes_on(ops, fake_device, monkeypatch, capsys):
    monkeypatch.setattr(maacore, "find", lambda: None)
    _write_box(2)
    assert cli.main(["op", "progress", "能天使", "--elite", "2"]) == 5      # 过了同意这关，卡在没装本体
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "maacore_missing"
    _write_box(1)
    assert cli.main(["op", "progress", "能天使", "--elite", "2", "--allow-catalyst"]) == 5
    capsys.readouterr()
    monkeypatch.setattr(maacore, "find", lambda: ("v6.18.0", Path("x")))
    assert cli.main(["op", "progress", "能天使", "--skill-level", "7"]) == 5
    assert "现在是 v6.18.0" in json.loads(capsys.readouterr().out)["error"]["message"]
    assert fake_device == []


def test_goes_home_before_and_after(ops, fake_device, monkeypatch, capsys):
    from maapilot.tools import maa_cmd
    monkeypatch.setattr(maacore, "find", lambda: ("v6.19.0", Path("x")))
    homes = []
    monkeypatch.setattr(maa_cmd, "go_home", lambda: homes.append(1) or True)
    seen = {}

    def run_task(task, params, **kw):
        seen.update(task=task, params=params, homes=len(homes))
        return core_run(detail("Success", "AlreadySatisfied"), summary(success=1))
    monkeypatch.setattr(maacore, "run_task", run_task)
    assert cli.main(["op", "progress", "能天使", "--skill-level", "7"]) == 0
    d = json.loads(capsys.readouterr().out)["data"]
    assert seen == {"task": "OperProgress", "homes": 1,
                    "params": {"plans": [{"name": "能天使", "role": "SNIPER", "skill_level": 7}]}}
    assert len(homes) == 2 and d["home"] is True and fake_device == ["op progress"]
    rows = [json.loads(l) for l in (paths.LOGS).glob("*.jsonl").__next__().read_text().splitlines()]
    assert rows[-1]["command"] == "op progress" and rows[-1]["result"]["steps"] == ["技能升到 7 级：AlreadySatisfied"]


def test_not_home_does_not_start(ops, fake_device, monkeypatch, capsys):
    from maapilot.tools import maa_cmd
    monkeypatch.setattr(maacore, "find", lambda: ("v6.19.0", Path("x")))
    monkeypatch.setattr(maa_cmd, "go_home", lambda: False)
    monkeypatch.setattr(maacore, "run_task", lambda *a, **k: pytest.fail("不该跑"))
    assert cli.main(["op", "progress", "能天使", "--skill-level", "7"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not_home"


# --- maacore ------------------------------------------------------------------------------------

def test_find_picks_newest_version_with_lib(monkeypatch):
    root = maacore.root()
    for v, has in (("v6.18.0", True), ("v6.19.0", True), ("v6.20.0", False), ("junk", True)):
        d = root / v / f"MAA-{v}-x"
        d.mkdir(parents=True)
        if has:
            (d / "libMaaCore.dylib").write_bytes(b"")
    version, lib_dir = maacore.find()
    assert version == "v6.19.0" and lib_dir == root / "v6.19.0" / "MAA-v6.19.0-x"
    assert maacore.at_least("v6.19.0", "v6.19.0") and not maacore.at_least("v6.9.9", "v6.19.0")


def test_parse_skips_noise():
    out = '\n'.join(['{"event": "version", "version": "v6.19.0"}', "MaaCore 自己打的东西", "[1, 2]",
                     '{"msg": 20003, "details": {"what": "OperProgressSummary", "details": {"success": 1}}}'])
    events, messages = maacore.parse(out)
    assert events == [{"event": "version", "version": "v6.19.0"}] and len(messages) == 1


def _fake_core(monkeypatch, tmp_path, body: str):
    """把子进程换成一个假的 runner 脚本；本体目录、设备也是假的。"""
    lib_dir = maacore.root() / "v6.19.0" / "rt"
    lib_dir.mkdir(parents=True)
    (lib_dir / "libMaaCore.dylib").write_bytes(b"")
    script = tmp_path / "fake_runner.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setattr(maacore, "runner_argv", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(maacore, "hot_update_dir", lambda: None)
    from maapilot.crew import device
    device.configure(device.Connection(address="127.0.0.1:5555", adb="adb"))


def test_run_task_collects_callbacks_and_writes_log(monkeypatch, tmp_path):
    _fake_core(monkeypatch, tmp_path, """
        import json, sys
        spec = json.loads(sys.stdin.read())
        print(json.dumps({"event": "version", "version": "v6.19.0"}))
        print(json.dumps({"msg": 20003, "details": {"what": "OperProgressSummary",
                                                    "details": {"success": 1, "echo": spec["params"]}}}))
    """)
    run = maacore.run_task("OperProgress", {"plans": [{"name": "能天使"}]}, label="t", timeout_s=30)
    assert run.ok and run.version == "v6.19.0"
    assert run.extra("OperProgressSummary") == [{"success": 1, "echo": {"plans": [{"name": "能天使"}]}}]
    assert "OperProgress" in run.log.read_text(encoding="utf-8")


def test_run_task_runner_error_and_timeout(monkeypatch, tmp_path):
    _fake_core(monkeypatch, tmp_path, """
        import sys, time
        if sys.stdin.read() and len(sys.argv) > 0:
            time.sleep(30)
    """)
    run = maacore.run_task("OperProgress", {}, label="t", timeout_s=1)
    assert run.timed_out and not run.ok


def test_run_task_exit_code_explained(monkeypatch, tmp_path):
    _fake_core(monkeypatch, tmp_path, "import sys; sys.stdin.read(); sys.exit(11)\n")
    run = maacore.run_task("OperProgress", {}, label="t", timeout_s=30)
    assert run.error == "MaaCore 连不上模拟器" and not run.ok


def test_run_task_without_core():
    run = maacore.run_task("OperProgress", {}, label="t", timeout_s=30)
    assert run.error == "没装单独的 MaaCore"


# --- 专精认不出页面 → 旧的专精命令兜底 ------------------------------------------------------------

def _progress_env(monkeypatch, run):
    from maapilot.tools import maa_cmd
    monkeypatch.setattr(maacore, "find", lambda: ("v6.19.0", Path("x")))
    monkeypatch.setattr(maa_cmd, "go_home", lambda: True)
    monkeypatch.setattr(maacore, "run_task", lambda *a, **k: run)


NOT_FOUND = "[2026-10-14 16:57:16.543][WRN][Px1][Tx1] select_training_trainee | oper not found 能天使"


def test_trainee_not_found_falls_back_to_op_mastery(ops, fake_device, monkeypatch, capsys):
    from maapilot.tools import live_box
    _progress_env(monkeypatch, core_run(detail("Failed", "RecognitionFailed"), summary(failed=1),
                                        warnings=[NOT_FOUND]))
    calls = []

    def mastery(name, skill, rank):
        calls.append((name, skill, rank))
        return {"operator": name, "skill": skill, "status": "started", "rank": 3, "hours": 24}
    monkeypatch.setattr(op, "mastery", mastery)
    assert cli.main(["op", "progress", "能天使", "--mastery", "3:3"]) == 0
    d = json.loads(capsys.readouterr().out)["data"]
    assert calls == [("能天使", 3, 3)] and d["training_started"] is True
    assert d["fallback"]["command"] == "op mastery" and "基建设施里工作" in d["fallback"]["reason"]
    assert d["steps"][0]["detail"] == "RecognitionFailed"                # MaaCore 的原话留着
    assert live_box.load()["能天使"]["mastery"] == {"3": 2}               # 开了专3 → 至少专2


def test_fallback_failure_reports_both(ops, fake_device, monkeypatch, capsys):
    _progress_env(monkeypatch, core_run(detail("Failed", "RecognitionFailed"), summary(failed=1)))

    def mastery(name, skill, rank):
        raise CliError("short_materials", "材料不够，没开专精：x", data={"status": "blocked: x"})
    monkeypatch.setattr(op, "mastery", mastery)
    assert cli.main(["op", "progress", "能天使", "--mastery", "3:2"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "short_materials"
    assert "改用旧的专精命令也没做成" in out["error"]["message"] and "MAA 认不出页面" in out["error"]["message"]
    assert out["data"]["fallback"]["status"] == "blocked: x" and out["data"]["steps"][0]["step"] == "技能3 专2"


@pytest.mark.parametrize("args,rows", [
    (["--mastery", "3:3"], [detail("Failed", "ResourceInsufficient")]),           # 别的失败不兜底
    (["--elite", "2", "--allow-catalyst", "--mastery", "3:1"],                      # 精英化认不出页面不进训练室
     [detail("Failed", "RecognitionFailed"), detail("Skipped", "Interrupt")]),
])
def test_no_fallback_otherwise(ops, fake_device, monkeypatch, capsys, args, rows):
    _progress_env(monkeypatch, core_run(*rows, summary(failed=1)))
    monkeypatch.setattr(op, "mastery", lambda *a: pytest.fail("不该兜底"))
    assert cli.main(["op", "progress", "能天使", *args]) == 1


def test_run_task_collects_core_log_warnings(monkeypatch, tmp_path):
    log = maacore.root() / "userdir" / "debug" / "asst.log"
    log.parent.mkdir(parents=True)
    log.write_text("[t][WRN][P][T] old warning\n", encoding="utf-8")
    _fake_core(monkeypatch, tmp_path, f"""
        import sys
        sys.stdin.read()
        with open({str(log)!r}, "a", encoding="utf-8") as f:
            f.write("[t][INF][P][T] fine\\n{NOT_FOUND}\\n")
    """)
    run = maacore.run_task("OperProgress", {}, label="t", timeout_s=30)
    assert run.warnings == [NOT_FOUND]
