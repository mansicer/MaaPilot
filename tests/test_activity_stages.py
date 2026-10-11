"""活动关导航：MAA 的活动列表（StageActivityV2.json，假的）→ stages / maa_cmd / data。不碰模拟器，不读本机 MAA 的缓存。"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from maapilot import cli
from maapilot.cliutil import CliError
from maapilot.knowledge import maa_activity, stages
from maapilot.knowledge.calendar import GAME_TZ
from maapilot.tools import data as datamod
from maapilot.tools import maa_cmd

NOW = datetime.now(GAME_TZ)


def _t(d: datetime) -> str:
    return d.strftime("%Y/%m/%d %H:%M:%S")


def yw(start=None, end=None, stages_=("YW-8", "YW-7", "YW-6", "YW-4")) -> dict:
    start = start or NOW - timedelta(days=2)
    end = end or NOW + timedelta(days=10)
    return {"MinimumRequired": "v6.17.0",
            "Activity": {"Tip": "SideStory「昨日海」", "StageName": "昨日海", "UtcStartTime": _t(start),
                         "UtcExpireTime": _t(end), "TimeZone": 8},
            "Stages": [{"Display": s, "Value": s, "Drop": "x"} for s in stages_]}


def reopen_at() -> dict:
    return {"MinimumRequired": "v6.14.0",
            "Activity": {"Tip": "SideStory「墟」复刻", "StageName": "墟", "UtcStartTime": _t(NOW - timedelta(days=1)),
                         "UtcExpireTime": _t(NOW + timedelta(days=3)), "TimeZone": 8},
            "Stages": [{"Display": "SSReopen-AT", "Value": "SSReopen-AT"}, {"Display": "AT-8", "Value": "AT-8"}]}


@pytest.fixture
def maa_file(tmp_path, monkeypatch):
    """写一份假的活动列表并指过去；tasks.json 默认没有。返回写文件的函数。"""
    n = [0]
    monkeypatch.setenv(maa_activity.TASKS_FILE_ENV, str(tmp_path / "no-tasks.json"))

    def write(side: dict, server="Official", tasks: dict | None = None):
        n[0] += 1                                            # 每次换个文件名：缓存按路径 + 修改时间
        p = tmp_path / f"StageActivityV2-{n[0]}.json"
        p.write_text(json.dumps({server: {"sideStoryStage": side}}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv(maa_activity.ACTIVITY_FILE_ENV, str(p))
        if tasks is not None:
            tp = tmp_path / f"tasks-{n[0]}.json"
            tp.write_text(json.dumps(tasks), encoding="utf-8")
            monkeypatch.setenv(maa_activity.TASKS_FILE_ENV, str(tp))
        return p
    return write


def test_parse_times_are_local_to_timezone(maa_file):
    maa_file({"YW": yw(start=datetime(2026, 10, 9, 12, 0, tzinfo=GAME_TZ),
                       end=datetime(2026, 10, 23, 3, 59, 59, tzinfo=GAME_TZ))})
    (a,) = maa_activity.activities()
    assert a.start == datetime(2026, 10, 9, 12, 0, tzinfo=GAME_TZ)
    assert a.to_dict()["prefix"] == "YW" and a.name == "昨日海" and a.reopen is False
    assert a.stages == ("act52side_04", "act52side_06", "act52side_07", "act52side_08")
    assert a.open_at(datetime(2026, 10, 9, 12, 0, tzinfo=GAME_TZ))
    assert not a.open_at(datetime(2026, 10, 9, 11, 59, tzinfo=GAME_TZ))


def test_listed_stage_is_ss_current(maa_file):
    maa_file({"YW": yw()})
    ref = stages.resolve_stage("YW-8")
    assert ref.nav_kind == "ss_current" and ref.maa_value == "YW-8" and ref.navigable and ref.reason is None
    assert stages.resolve_stage("YW-8", events=False).nav_kind == "not_navigable"


def test_unlisted_stage_of_open_activity(maa_file):
    maa_file({"YW": yw()})
    ref = stages.resolve_stage("YW-3")
    assert ref.nav_kind == "not_navigable" and ref.maa_value is None
    assert ref.reason == "MAA 只支持「昨日海」的 YW-4、YW-6、YW-7、YW-8，YW-3 不在里面"


def test_tasks_json_navigation_task_makes_stage_navigable(maa_file):
    maa_file({"YW": yw()}, tasks={"YW-3": {}, "YW-3@SideStoryStage": {"text": ["YW-3"]}, "AB-1@SideStoryStage": {}})
    ref = stages.resolve_stage("YW-3")
    assert ref.nav_kind == "ss_current" and ref.maa_value == "YW-3"


def test_activity_not_in_maa_list_yet(maa_file):
    maa_file({"SSReopen": reopen_at()})
    ref = stages.resolve_stage("YW-8")
    assert ref.nav_kind == "not_navigable"
    assert ref.reason == "MAA 还不认这个活动的关卡（MAA 的活动列表里还没有「昨日海」）"


def test_activity_ended_or_not_started(maa_file):
    maa_file({"YW": yw(start=NOW - timedelta(days=20), end=NOW - timedelta(days=1))})
    assert "已经结束" in stages.resolve_stage("YW-8").reason
    maa_file({"YW": yw(start=NOW + timedelta(days=1), end=NOW + timedelta(days=10))})
    assert "还没开始" in stages.resolve_stage("YW-8").reason


def test_reopen_is_ss_reopen(maa_file):
    maa_file({"SSReopen": reopen_at()})
    ref = stages.resolve_stage("AT-8")
    assert ref.nav_kind == "ss_reopen" and ref.maa_value == "AT-8"


def test_other_server_section_ignored(maa_file):
    maa_file({"YW": yw()}, server="YoStarEN")
    ref = stages.resolve_stage("YW-8")
    assert ref.nav_kind == "not_navigable" and "还没有「昨日海」" in ref.reason


def test_no_file_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv(maa_activity.ACTIVITY_FILE_ENV, str(tmp_path / "none.json"))
    ref = stages.resolve_stage("YW-8")
    assert ref.nav_kind == "not_navigable" and ref.reason is None
    assert maa_cmd.resolve_stage("YW-8")[0] == "YW-8"                 # 没有列表：照旧原样交给 MAA


def test_maa_cmd_resolve_stage(maa_file):
    maa_file({"YW": yw()})
    value, info = maa_cmd.resolve_stage("YW-8")
    assert value == "YW-8" and info["nav_kind"] == "ss_current"
    with pytest.raises(CliError) as e:
        maa_cmd.resolve_stage("YW-3")
    assert e.value.code == "maa_unsupported_stage" and e.value.exit == 1
    assert "YW-4、YW-6、YW-7、YW-8" in e.value.message and "手动进关卡页" in e.value.hint

    maa_file({"SSReopen": reopen_at()})
    with pytest.raises(CliError) as e:
        maa_cmd.resolve_stage("YW-3")
    assert e.value.message == "MAA 还不认这个活动的关卡（MAA 的活动列表里还没有「昨日海」）"
    assert e.value.hint == "MAA 一般活动开始后一两天更新；明天再试，或者手动进关卡页用代理"
    assert maa_cmd.resolve_stage("1-7")[0] == "1-7"
    assert maa_cmd.resolve_stage("XYZ-9")[0] == "XYZ-9"                # 数据表里没有：原样交给 MAA


def test_data_stage_shows_activity(maa_file, capsys):
    maa_file({"YW": yw()})
    assert cli.main(["data", "stage", "YW-3"]) == 0
    d = json.loads(capsys.readouterr().out)["data"]
    assert d["nav_kind"] == "not_navigable" and "YW-3 不在里面" in d["not_navigable_reason"]
    act = d["maa_activity"]
    assert act["in_maa_list"] and act["name"] == "昨日海" and act["open_now"] is True
    assert act["supported"] == ["YW-4", "YW-6", "YW-7", "YW-8"] and act["min_maa"] == "v6.17.0"
    assert cli.main(["data", "stage", "YW-8"]) == 0
    d = json.loads(capsys.readouterr().out)["data"]
    assert d["nav_kind"] == "ss_current" and d["maa_value"] == "YW-8" and "not_navigable_reason" not in d
    assert cli.main(["data", "stage", "1-7"]) == 0
    assert "maa_activity" not in json.loads(capsys.readouterr().out)["data"]


def test_best_stages_includes_navigable_event_stages(maa_file):
    maa_file({"YW": yw()})
    lv = SimpleNamespace(rates={"act52side_08": {"31103": 0.5}, "act52side_03": {"31103": 0.9},
                                "main_01-07": {"31103": 0.1}},
                         ap={"act52side_08": 21, "act52side_03": 12, "main_01-07": 6}, samples={})
    rows = datamod.best_stages("31103", lv)
    assert [r["stage"] for r in rows] == ["YW-8", "1-7"]               # YW-3 MAA 导航不了，不列
    assert rows[0]["event_end"] == maa_activity.activities()[0].end.isoformat(timespec="minutes")
    assert "event_end" not in rows[1]


def test_calc_event_prefers_high_tier_maa_stages(maa_file):
    """calc event：MAA 能刷的在前，主要掉落等级高的在前；活动代币不算材料。"""
    from maapilot.tools import calc
    maa_file({"YW": yw()})
    out = calc.event_stages()
    acts = out["activities"]
    assert acts and acts[0]["name"] == "昨日海"
    rows = acts[0]["stages"]
    can = [r for r in rows if r["maa_can_fight"]]
    assert rows[:len(can)] == can
    tiers = [r["top_tier"] or 0 for r in can]
    assert tiers == sorted(tiers, reverse=True)
    assert all("瞻望镜" not in d["name"] for r in rows for d in r["drops"])
