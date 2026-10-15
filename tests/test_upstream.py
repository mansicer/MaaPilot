"""外部数据源的格式核对：每个来源的真实响应样本（tests/fixtures/upstream/，裁过、去掉了个人数据）要能通过，
删掉一个程序用到的字段就要报 upstream_format、写明来源和字段，并记进 private/state/sources.json；
doctor --brief 和日常汇报从那里读出"有问题的来源"。不联网、不碰模拟器。

样本来源（2026-10-13）：森空岛是按代码用到的字段写的假数据（没有调过接口）；MAA 活动列表 / 任务表是 maa-cli 缓存里的文件；
maa-cli 日志是一次真的刷图日志，Summary 按 maa-cli 的写法；公告、安装包地址是公开接口拉一次的结果；企鹅物流只留形状
（数是编的：它的数据不随仓库分发）；一图流、游戏数据表从本机缓存裁的。
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from maapilot import paths
from maapilot.cliutil import CliError
from maapilot.knowledge import datasync, live, maa_activity, news, sources
from maapilot.knowledge.sources import UpstreamFormat

FIX = Path(__file__).parent / "fixtures" / "upstream"


def fx(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def health(source: str) -> dict:
    return sources.load().get(source) or {}


def bad_fields() -> dict:
    return {u["source"]: u["message"] for u in sources.unhealthy()}


# --- 健康记录 ---------------------------------------------------------------------------------------------------------

def test_health_record_ok_then_bad_then_ok():
    t = datetime(2026, 10, 13, 5, 0).astimezone()
    sources.ok("penguin", now=t)
    assert sources.unhealthy() == []
    sources.bad("penguin", UpstreamFormat("penguin", "matrix[].times"), effect="掉率用旧的", now=t + timedelta(minutes=1))
    (u,) = sources.unhealthy()
    assert u["source"] == "penguin" and u["name"] == "企鹅物流" and "缺 matrix[].times" in u["message"]
    assert u["line"] == "企鹅物流的数据格式变了（缺 matrix[].times），掉率用旧的"
    e = health("penguin")
    assert e["last_ok"] and e["last_error"]["code"] == "upstream_format" and len(e["recent"]) == 1
    sources.ok("penguin", now=t + timedelta(minutes=2))                 # 修好了：又读成了
    assert sources.unhealthy() == []


def test_errors_between_only_in_window():
    t = datetime(2026, 10, 13, 5, 0).astimezone()
    sources.bad("bulletin", "官方公告的数据格式变了（缺 list）", now=t)
    sources.bad("skland", "森空岛的数据格式变了（缺 status.ap）", effect="理智改用首页识别", now=t + timedelta(hours=2))
    got = sources.errors_between((t - timedelta(minutes=5)).isoformat(), (t + timedelta(minutes=30)).isoformat())
    assert got == ["官方公告的数据格式变了（缺 list）"]
    got = sources.errors_between((t + timedelta(hours=1)).isoformat(), None)
    assert got == ["森空岛的数据格式变了（缺 status.ap），理智改用首页识别"]


# --- 森空岛 -----------------------------------------------------------------------------------------------------------

def test_skland_fixture_accepted():
    from maapilot.crew import skland
    info = fx("skland_player_info.json")
    ops = skland.to_box(info)
    assert ops["阿米娅"]["skills"][1]["mastery"] == 3 and ops["阿米娅"]["modules"][1]["equipped"] is True
    assert skland.sanity_of(info, now_ts=1000)["max"] == 135
    assert skland.annihilation_of(info)["total"] == 1800


@pytest.mark.parametrize("mutate, field", [
    (lambda i: i["status"]["ap"].pop("current"), "status.ap.current"),
    (lambda i: i["status"].pop("ap"), "status.ap"),
])
def test_skland_sanity_rejects(mutate, field):
    from maapilot.crew import skland
    info = fx("skland_player_info.json")
    mutate(info)
    with pytest.raises(UpstreamFormat) as e:
        skland.sanity_of(info)
    assert e.value.code == "upstream_format" and e.value.source == "skland" and field in e.value.message


@pytest.mark.parametrize("mutate, field", [
    (lambda i: i.pop("chars"), "chars"),
    (lambda i: i["chars"][0].pop("evolvePhase"), "chars[].evolvePhase"),
    (lambda i: i["chars"][0]["skills"][1].pop("specializeLevel"), "chars[].skills[].specializeLevel"),
    (lambda i: [c.pop("equip") for c in i["chars"]], "chars[].equip"),
    (lambda i: i.pop("charInfoMap"), "charInfoMap"),
])
def test_skland_box_rejects(mutate, field):
    from maapilot.crew import skland
    info = fx("skland_player_info.json")
    mutate(info)
    with pytest.raises(UpstreamFormat) as e:
        skland.to_box(info)
    assert f"缺 {field}）" in e.value.message


def test_skland_annihilation_rejects():
    from maapilot.crew import skland
    info = fx("skland_player_info.json")
    del info["campaign"]["reward"]["total"]
    with pytest.raises(UpstreamFormat, match="campaign.reward.total"):
        skland.annihilation_of(info)


# --- MAA 的活动列表、任务表 -----------------------------------------------------------------------------------------

def test_maa_activity_fixture_accepted():
    doc = fx("maa_stage_activity.json")
    assert set(maa_activity.check(doc, "Official")) == {"SSReopen", "YW"}
    assert maa_activity.check(doc, "Bilibili") is doc["Official"]["sideStoryStage"]   # B 服用官服那一节
    assert maa_activity.check_tasks(fx("maa_tasks.json"))


@pytest.mark.parametrize("mutate, field", [
    (lambda d: d["Official"].pop("sideStoryStage"), "Official.sideStoryStage"),
    (lambda d: d["Official"]["sideStoryStage"]["YW"]["Activity"].pop("UtcStartTime"), "YW.Activity.UtcStartTime"),
    (lambda d: d["Official"]["sideStoryStage"]["YW"].pop("Stages"), "YW.Stages"),
    (lambda d: d["Official"]["sideStoryStage"]["YW"]["Stages"][0].pop("Value"), "YW.Stages[].Value"),
])
def test_maa_activity_rejects_and_records(mutate, field, tmp_path, monkeypatch):
    doc = fx("maa_stage_activity.json")
    mutate(doc)
    with pytest.raises(UpstreamFormat) as e:
        maa_activity.check(doc, "Official")
    assert field in e.value.message
    p = tmp_path / f"act-{abs(hash(field))}.json"                 # 换文件名：读文件按路径 + 修改时间缓存
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(maa_activity.ACTIVITY_FILE_ENV, str(p))
    maa_activity.activities("Official")
    assert field in bad_fields()["maa_activity"]
    assert maa_activity.problem("Official") == e.value.message


def test_maa_tasks_rejects_and_records(tmp_path, monkeypatch):
    with pytest.raises(UpstreamFormat):
        maa_activity.check_tasks(["YW-8@SideStoryStage"])
    p = tmp_path / "tasks-bad.json"
    p.write_text(json.dumps({"YW-8@SideStoryStage": ["YW-8"]}), encoding="utf-8")
    assert maa_activity._task_names(str(p), p.stat().st_mtime) == frozenset()
    assert "YW-8@SideStoryStage" in bad_fields()["maa_tasks"]


# --- maa-cli 的输出 ---------------------------------------------------------------------------------------------------

def _run(stdout: str, log: Path, rc: int = 0):
    from maapilot.crew import maa
    r = maa.MaaRun(["maa"], rc, stdout, log, 1.0)
    r.parsed = maa.parse_output(stdout, log)
    return r


def test_maa_fight_fixture_accepted():
    from maapilot.tools import maa_cmd
    r = _run((FIX / "maa_fight_stdout.txt").read_text(encoding="utf-8"), FIX / "maa_fight.log")
    assert r.ok and r.parsed["fights"] == 2 and r.parsed["sanity_last"] == [99, 205]
    assert r.parsed["drops"]["近卫芯片"] == 1
    assert maa_cmd.output_problem("fight", r) is None
    data = {}
    maa_cmd._note_output("fight", r, data)
    assert "upstream_format" not in data and health("maa_cli")["last_ok"]


def test_maa_fight_without_known_lines_is_flagged(tmp_path):
    from maapilot.tools import maa_cmd
    log = tmp_path / "fight.log"
    text = (FIX / "maa_fight.log").read_text(encoding="utf-8")
    log.write_text(text.replace("Current sanity", "Sanity now").replace("Mission started", "Battle begins"),
                   encoding="utf-8")
    r = _run((FIX / "maa_fight_stdout.txt").read_text(encoding="utf-8"), log)
    assert r.ok
    data = {}
    maa_cmd._note_output("fight", r, data)
    assert "Current sanity" in data["upstream_format"] and "Current sanity" in bad_fields()["maa_cli"]
    # 剿灭找不到入口 / 本周拿满时 MAA 也报完成、日志里没有这两行：不算格式问题
    assert maa_cmd.output_problem("annihilation", r) is None


def test_maa_summary_missing_is_flagged():
    from maapilot.tools import maa_cmd
    r = _run("all done\n", FIX / "maa_fight.log")
    e = maa_cmd.output_problem("recruit", r)
    assert e is not None and "Summary" in e.message
    assert maa_cmd.output_problem("copilot", r) is None                     # 作业不查 Summary
    assert maa_cmd.output_problem("recruit", _run("all done\n", FIX / "maa_fight.log", rc=1)) is None   # 失败时另有报错


# --- 官方公告 ---------------------------------------------------------------------------------------------------------

def _fake_get(list_data, item_data):
    def get(url):
        return copy.deepcopy(list_data if url == news.LIST_URL else item_data)
    return get


def test_bulletin_fixture_accepted():
    lst, item = fx("bulletin_list.json")["data"], fx("bulletin_item.json")["data"]
    lst["list"][0].update(cid=item["cid"], category=1, title=item["title"])    # 列表里放一条活动公告，要拉全文
    now = datetime(2026, 10, 13, 12, 0).astimezone()
    out = news.fetch(now, get=_fake_get(lst, item))
    act = next(i for i in out if i["cid"] == item["cid"])
    assert act["kind"] == "活动" and act["windows"][0]["start"].startswith("2026-10-16T16:00")
    assert health("bulletin")["last_ok"]


@pytest.mark.parametrize("where, key", [("list", "displayTime"), ("list", "cid"), ("item", "content")])
def test_bulletin_rejects_and_records(where, key):
    lst, item = fx("bulletin_list.json")["data"], fx("bulletin_item.json")["data"]
    lst["list"][0].update(cid=item["cid"], category=1, title=item["title"])
    (lst["list"][0] if where == "list" else item).pop(key)
    with pytest.raises(UpstreamFormat) as e:
        news.fetch(datetime(2026, 10, 13, 12, 0).astimezone(), get=_fake_get(lst, item))
    assert key in e.value.message and key in bad_fields()["bulletin"]


def test_news_list_reports_upstream_format(monkeypatch):
    from maapilot.tools import news_cmd
    monkeypatch.setattr(news_cmd, "_official", lambda: None)
    monkeypatch.setattr(news, "refresh", lambda: (_ for _ in ()).throw(UpstreamFormat("bulletin", "list")))
    with pytest.raises(CliError) as e:
        news_cmd.listing(refresh=True)
    assert e.value.code == "upstream_format" and "官方公告" in e.value.message


# --- 官网安装包地址 ---------------------------------------------------------------------------------------------------

class _Resp:
    def __init__(self, url, status=206, headers=None):
        self.url, self.status, self.headers = url, status, headers or {}

    def geturl(self):
        return self.url

    def close(self):
        pass


def test_game_download_fixture_accepted(monkeypatch):
    from maapilot.tools import game
    g = fx("game_download.json")
    monkeypatch.setattr(game, "http_open", lambda *a, **k: _Resp(g["final_url"], 206, {"Content-Range": g["content_range"]}))
    out = game.latest_version()
    assert out["version"] == "2.7.81" and out["size"] == 1861585573
    assert health("game_download")["last_ok"]


def test_game_download_rejects_and_records(monkeypatch):
    from maapilot.tools import game
    url = fx("game_download.json")["final_url"].replace("arknights-hg-2781.apk", "Arknights_78.0.apk")
    monkeypatch.setattr(game, "http_open", lambda *a, **k: _Resp(url))
    with pytest.raises(CliError) as e:
        game.latest_version()
    assert e.value.code == "upstream_format" and "安装包文件名" in e.value.message
    assert "game_download" in bad_fields()


# --- 企鹅物流、一图流 -------------------------------------------------------------------------------------------------

def test_penguin_and_yituliu_fixtures_accepted():
    live.check_penguin(fx("penguin_matrix.json"), fx("penguin_stages.json"))
    live.check_values(fx("yituliu_values.json"))


@pytest.mark.parametrize("mutate, field", [
    (lambda m, s, v: m["matrix"][0].pop("times"), "matrix[].times"),
    (lambda m, s, v: s[0].pop("existence"), "stages[].existence"),
    (lambda m, s, v: m.pop("matrix"), "matrix"),
])
def test_penguin_rejects_and_records(mutate, field, monkeypatch):
    m, s = fx("penguin_matrix.json"), fx("penguin_stages.json")
    mutate(m, s, None)
    with pytest.raises(UpstreamFormat) as e:
        live.check_penguin(m, s)
    assert field in e.value.message
    monkeypatch.setattr(live, "fetch_penguin", lambda server: (m, s))
    monkeypatch.setattr(live, "fetch_values", lambda: fx("yituliu_values.json"))
    monkeypatch.setattr(paths, "DATA", paths.PRIVATE / "data")
    r = live.sync("CN")
    assert field in r["penguin"] and r["values"] == "ok"
    assert field in bad_fields()["penguin"] and "yituliu" not in bad_fields()


def test_yituliu_rejects_and_records(monkeypatch):
    v = fx("yituliu_values.json")
    del v["data"][0]["itemValueAp"]
    with pytest.raises(UpstreamFormat, match="itemValueAp"):
        live.check_values(v)
    monkeypatch.setattr(live, "fetch_penguin", lambda server: (fx("penguin_matrix.json"), fx("penguin_stages.json")))
    monkeypatch.setattr(live, "_request", lambda *a, **k: v)
    monkeypatch.setattr(paths, "DATA", paths.PRIVATE / "data")
    r = live.sync("CN")
    assert r["penguin"] == "ok" and "itemValueAp" in r["values"]
    assert "itemValueAp" in bad_fields()["yituliu"] and health("penguin")["last_ok"]


# --- 游戏数据表 -------------------------------------------------------------------------------------------------------

TABLES = sorted(p.stem for p in (FIX / "gamedata").glob("*.json"))


def test_every_table_has_a_fixture():
    assert set(TABLES) == set(datasync.REQUIRED_TABLES + datasync.OPTIONAL_TABLES)


@pytest.mark.parametrize("table", TABLES)
def test_gamedata_fixture_accepted(table):
    datasync.check_table(f"{table}.json", fx(f"gamedata/{table}.json"))


@pytest.mark.parametrize("table, mutate, field", [
    ("character_table", lambda t: t["char_002_amiya"].pop("phases"), "character_table[].phases"),
    ("item_table", lambda t: t.pop("items"), "item_table.items"),
    ("stage_table", lambda t: t["stages"]["main_01-07"].pop("apCost"), "stage_table.stages[].apCost"),
    ("gamedata_const", lambda t: t.pop("evolveGoldCost"), "gamedata_const.evolveGoldCost"),
    ("uniequip_table", lambda t: t["equipDict"]["uniequip_002_amiya"].pop("itemCost"), "uniequip_table.equipDict[].itemCost"),
    ("building_data", lambda t: t["workshopFormulas"]["1"].pop("costs"), "building_data.workshopFormulas[].costs"),
    ("skill_table", lambda t: t["skchr_amiya_2"].pop("levels"), "skill_table[].levels"),
])
def test_gamedata_rejects(table, mutate, field):
    t = fx(f"gamedata/{table}.json")
    mutate(t)
    with pytest.raises(UpstreamFormat) as e:
        datasync.check_table(f"{table}.json", t)
    assert f"缺 {field}）" in e.value.message


def test_gamedata_sync_keeps_old_table_and_records(monkeypatch):
    monkeypatch.setattr(paths, "DATA", paths.PRIVATE / "data")
    (paths.DATA).mkdir(parents=True)
    (paths.DATA / "stage_table.json").write_text('{"old": true}', encoding="utf-8")
    bad = fx("gamedata/stage_table.json")
    del bad["stages"]["main_01-07"]["code"]
    monkeypatch.setattr(datasync, "fetch_remote_shas", lambda files=None: {"stage_table.json": "new"})
    monkeypatch.setattr(datasync, "missing_tables", lambda: [])
    monkeypatch.setattr(datasync, "_get", lambda url, timeout=60: json.dumps(bad).encode())
    with pytest.raises(UpstreamFormat, match="stage_table.stages\\[\\].code"):
        datasync.sync()
    assert json.loads((paths.DATA / "stage_table.json").read_text(encoding="utf-8")) == {"old": True}
    assert "stage_table" in bad_fields()["gamedata"]


# --- 在哪里看得到：doctor --brief、日常汇报 ---------------------------------------------------------------------------

def test_doctor_brief_lists_only_unhealthy(monkeypatch):
    from maapilot.tools import doctor
    sources.ok("penguin")
    sources.bad("skland", UpstreamFormat("skland", "status.ap"), effect="理智改用首页识别")
    paths.SETUP.parent.mkdir(parents=True, exist_ok=True)
    paths.SETUP.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(doctor, "schedule_info", lambda probe: {"installed": True, "times": ["04:40"]})
    b = doctor.brief()
    assert [s["source"] for s in b["sources"]] == ["skland"]
    assert any("森空岛的数据格式变了（缺 status.ap），理智改用首页识别" in l for l in b["lines"])


def test_report_alerts_include_sources_seen_during_run():
    from maapilot.flows import report
    start = datetime.now().astimezone() - timedelta(minutes=10)
    sources.bad("bulletin", "早就有的问题", now=start - timedelta(days=1))
    sources.bad("skland", UpstreamFormat("skland", "status.ap"), effect="理智改用首页识别", now=start + timedelta(minutes=1))
    d = {"run_id": "20261013-0440", "trigger": "schedule", "status": "done", "ok": True,
         "started_at": start.isoformat(timespec="seconds"), "finished_at": None, "tasks": [], "failed": [],
         "alerts": [], "guard": {"ok": True}, "depot_change": []}
    md = report.render(d)
    assert "## 要注意" in md and "森空岛的数据格式变了（缺 status.ap），理智改用首页识别" in md
    assert "早就有的问题" not in md
    assert any("森空岛" in a for a in report.problem_brief(d)["alerts"])
