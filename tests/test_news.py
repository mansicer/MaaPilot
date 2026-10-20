"""官方公告（`news`）：解析时间段、分类、日程时间点、`news told`（日常进行中也能记）、非国服不看国服日程。不联网。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from maapilot import cli, config
from maapilot.flows import daily
from maapilot.knowledge import news
from maapilot.knowledge.calendar import GAME_TZ

PUB = datetime(2026, 10, 7, 12, 0, tzinfo=GAME_TZ)

EVENT = {"cid": "6774", "title": "【昨日海】\\n活动即将开启", "updatedAt": 1790669362, "displayTime": "2026-10-03",
         "content": "<p>一、全新SideStory「昨日海」，活动关卡开启</p><p>活动时间：10月09日 12:00 - 10月30日 03:59</p>"
                    "<p>◆「雷亚-伊比利亚」开启时间：10月09日 12:00 - 10月23日 03:59</p>"
                    "<p>◆「“我们的伊比利亚”」开启时间：10月16日 16:00 - 10月23日 03:59</p>"}
MAINT = {"cid": "5699", "title": "10月09日\\n版本更新公告", "updatedAt": 1791276413, "displayTime": "2026-10-07",
         "content": "《明日方舟》计划将于2026年10月09日06:00至12:00对客户端进行版本更新停机维护。本次更新为强制更新，"
                    "更新结束后，需玩家对游戏客户端进行重新下载和安装。<br>维护时间：2026年10月09日06:00 ~ 12:00"}


def at(mon, day, h, m=0):
    return datetime(2026, mon, day, h, m, tzinfo=GAME_TZ)


def cache(*raws):
    d = {"at": at(10, 9, 5).isoformat(), "items": [news.item_from(r, published=PUB) for r in raws]}
    news.save(d)
    return d


def test_windows_and_kinds():
    ev = news.item_from(EVENT, published=PUB)
    assert ev["kind"] == "活动" and ev["title"] == "【昨日海】 活动即将开启"
    assert [(w["start"][5:16], w["end"][5:16]) for w in ev["windows"]] == [
        ("10-09T12:00", "10-30T03:59"), ("10-09T12:00", "10-23T03:59"), ("10-16T16:00", "10-23T03:59")]
    assert ev["windows"][2]["label"] == "“我们的伊比利亚”"
    m = news.item_from(MAINT, published=PUB)
    assert m["kind"] == "维护" and m["forced_update"] is True
    assert (m["maintenance"]["start"], m["maintenance"]["end"]) == (at(10, 9, 6).isoformat(), at(10, 9, 12).isoformat())
    assert news.kind_of("「制作组通讯」 #69期", "活动开启……") == "其他"


def test_year_rollover():
    w = news.windows("活动时间：12月28日 16:00 - 01月11日 03:59", datetime(2026, 12, 20, tzinfo=GAME_TZ))
    assert w[0]["start"].startswith("2026-12-28") and w[0]["end"].startswith("2027-01-11")


def test_schedule_lines_due_and_told(capsys):
    d = cache(EVENT, MAINT)
    assert news.maintenance_now(d, at(10, 9, 7))["forced_update"] is True
    assert news.maintenance_now(d, at(10, 9, 12, 1)) is None
    lines = news.lines(d, at(10, 9, 7))
    assert lines[0].startswith("正在维护（到 10月9日 12:00）") and "强制更新" in lines[0]
    assert any(l.startswith("10月9日 12:00 开始：昨日海") for l in lines)
    keys = [e["key"] for e in news.events(d)]
    assert len(keys) == len(set(keys))                                  # 同一时刻开始的几段只算一个
    assert [e["key"] for e in news.due(d, at(10, 9, 11))] == ["6774:公告"]      # 还没开始：先说一声有新活动
    due = news.due(d, at(10, 9, 12, 30))
    assert {e["what"] for e in due} == {"维护结束", "开始"}
    news.mark_told([e["key"] for e in due], at(10, 9, 12, 31))
    assert news.due(None, at(10, 9, 12, 32)) == []
    assert news.due(None, at(10, 16, 17))[0]["label"] == "“我们的伊比利亚”"


def test_told_rejects_unknown_key(capsys):
    cache(EVENT)
    code = cli.main(["news", "told", "nope"])
    assert code == 2 and json.loads(capsys.readouterr().out)["error"]["code"] == "unknown_event"


def test_new_announcements_are_due_before_they_start():
    d = cache(EVENT, MAINT)
    due = news.due(d, at(10, 8, 9))
    assert [(e["what"], e["cid"]) for e in due] == [("公告", "6774"), ("公告", "5699")]      # 按发布先后
    m = due[1]
    assert m["kind"] == "维护" and m["start"] == at(10, 9, 6).isoformat() and m["forced_update"] is True
    news.mark_told(["6774:公告"], at(10, 8, 9, 1))
    assert [e["key"] for e in news.due(None, at(10, 8, 9, 2))] == ["5699:公告"]
    assert [e["key"] for e in news.due(None, at(10, 9, 12, 30))] == ["6774:2026-10-09T12:00:00+08:00", "5699:维护结束"]


def test_reminders_last_a_few_days_not_forever():
    d = cache(EVENT, MAINT)
    assert {e["what"] for e in news.due(d, at(10, 11, 11))} == {"维护结束", "开始"}         # 两天没开会话也补上
    assert news.due(d, at(10, 13, 13)) == []                                               # 过了三天就不再提
    assert [e["what"] for e in news.due(d, at(10, 16, 17))] == ["开始"]                     # 后一段开始


def test_announcement_skipped_once_its_event_was_told():
    d = cache(EVENT)
    d["told"] = {"6774:2026-10-09T12:00:00+08:00": "x"}
    news.save(d)
    assert news.due(None, at(10, 8, 9)) == []


def _server(monkeypatch, srv):
    real = config.load_or_defaults
    monkeypatch.setattr(config, "load_or_defaults", lambda: {**real(), "server": srv})


def test_refresh_keeps_told_written_while_fetching(monkeypatch):
    cache(EVENT)

    def slow_fetch(now, get):                                            # 拉公告期间，用户那边 news told 了
        news.mark_told(["6774:公告"])
        return news.load()["items"]
    monkeypatch.setattr(news, "fetch", slow_fetch)
    news.refresh(at(10, 9, 6))
    d = news.load()
    assert "6774:公告" in d["told"] and d["at"] == at(10, 9, 6).isoformat()


@pytest.mark.parametrize("srv,applies", [("Official", True), ("Bilibili", True), ("YoStarEN", False),
                                         ("YoStarJP", False), ("txwy", False)])
def test_applies(monkeypatch, srv, applies):
    _server(monkeypatch, srv)
    assert news.applies() is applies


def test_non_cn_server_skips_maintenance_and_news(monkeypatch, capsys):
    _server(monkeypatch, "YoStarEN")
    cache(EVENT, MAINT)
    monkeypatch.setattr(news, "refresh", lambda: pytest.fail("不该拉"))
    monkeypatch.setattr(news, "stale", lambda d=None, now=None: True)
    monkeypatch.setattr(news, "maintenance_now", lambda d=None, now=None: {"cid": "5699"})
    assert daily.refresh_news() is False and daily.maintenance_now() is None
    code = cli.main(["news", "list"])
    assert code == 2 and json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported_server"


def test_bilibili_uses_cn_schedule(monkeypatch):
    _server(monkeypatch, "Bilibili")
    monkeypatch.setattr(news, "maintenance_now", lambda d=None, now=None: {"cid": "5699"})
    assert daily.maintenance_now() == {"cid": "5699"}


def test_news_told_allowed_during_open_daily(monkeypatch, capsys):
    """agent 在会话里说了就是说过了：日常在跑、没收尾时也能 news told（v0.2.0 去掉了定时日常里的限制）。"""
    cache(EVENT)
    monkeypatch.setattr(daily, "running", lambda: {"run_id": "20261004-1640", "pid": 1})
    monkeypatch.setattr(daily, "open_daily", lambda: {"run_id": "20261004-1640", "stale": False})
    monkeypatch.setenv("MAAPILOT_DAILY_RUN", "20261004-1640")              # 旧版的环境变量也不再看
    assert cli.main(["news", "told", "6774:公告"]) == 0
    assert "6774:公告" in news.load()["told"]
    capsys.readouterr()


@pytest.mark.parametrize("server,shown", [("Official", True), ("Bilibili", True), ("YoStarEN", False)])
def test_news_brief_for_cn_servers(monkeypatch, server, shown):
    from maapilot.tools import doctor
    monkeypatch.setattr(config, "load_or_defaults", lambda: {"server": server})
    monkeypatch.setattr(news, "load", lambda: {"at": "t", "items": [{"key": "k"}]})
    monkeypatch.setattr(news, "lines", lambda d: ["x"])
    monkeypatch.setattr(news, "due", lambda d: [])
    assert (doctor.news_brief() is not None) is shown
