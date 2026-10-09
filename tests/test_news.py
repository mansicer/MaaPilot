"""官方公告（`news`）：解析时间段、分类、日程时间点、`news told`、`daily watch` 在日程到点时结束。不联网。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from maapilot import cli
from maapilot.flows import daily, watch
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
    assert news.due(d, at(10, 9, 11)) == []
    due = news.due(d, at(10, 9, 12, 30))
    assert {e["what"] for e in due} == {"维护结束", "开始"}
    news.mark_told([e["key"] for e in due], at(10, 9, 12, 31))
    assert news.due(None, at(10, 9, 12, 32)) == []
    assert news.due(None, at(10, 16, 17))[0]["label"] == "“我们的伊比利亚”"


def test_told_rejects_unknown_key(capsys):
    cache(EVENT)
    code = cli.main(["news", "told", "nope"])
    assert code == 2 and json.loads(capsys.readouterr().out)["error"]["code"] == "unknown_event"


def test_watch_ends_on_due_schedule(monkeypatch):
    monkeypatch.setattr(daily, "running", lambda: None)
    monkeypatch.setattr(daily, "schedule_info", lambda: {"installed": True, "times": ["04:40"], "old_entries": []})
    cache(EVENT, MAINT)
    monkeypatch.setattr(news, "stale", lambda d=None, now=None: False)
    real_due = news.due
    monkeypatch.setattr(news, "due", lambda d=None, now=None, within_h=24: real_due(d, at(10, 9, 12, 30)))
    out = watch.watch(timeout_min=1, sleep=lambda s: pytest.fail("不该等"))
    assert out["kind"] == "news" and {e["what"] for e in out["due"]} == {"维护结束", "开始"}
