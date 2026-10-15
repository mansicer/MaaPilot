"""`daily watch` 补交漏掉的日常、日程提醒只醒一次、拉公告失败退避、told 与 refresh 不互相覆盖、
非国服不看国服日程、定时日常里不能 `news told`。不联网、不碰模拟器。"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from maapilot import cli, config
from maapilot.cliutil import CliError
from maapilot.flows import daily, report as reportmod, watch
from maapilot.flows._common import now
from maapilot.knowledge import news

from tests.test_news import EVENT, MAINT, at, cache
from tests.test_watch import rep


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(daily, "schedule_info", lambda: {"installed": True, "times": ["04:40"], "old_entries": []})
    monkeypatch.setattr(daily, "running", lambda: None)


def _server(monkeypatch, srv):
    real = config.load_or_defaults
    monkeypatch.setattr(config, "load_or_defaults", lambda: {**real(), "server": srv})


def _due_at(monkeypatch, t):
    monkeypatch.setattr(news, "stale", lambda d=None, now=None: False)
    real_due = news.due
    monkeypatch.setattr(news, "due", lambda d=None, now=None: real_due(d, t))


# --- 1. 两个 watch 之间跑完的日常 ---------------------------------------------------------------------------------------

def test_run_finished_between_watches_is_returned_at_once():
    rep("20261004-0440", finished=now() - timedelta(hours=3))
    rep("20261004-1640", finished=now() - timedelta(minutes=5), tasks=[{"task": "recruit", "ok": True}])
    reportmod.mark_seen("20261004-0440")
    d = watch.watch(5, sleep=lambda s: pytest.fail("不该等"))
    assert d["kind"] == "daily" and d["run_id"] == "20261004-1640" and d["seen"] is False
    reportmod.mark_seen("20261004-1640")                                # 说完 report seen：下一个 watch 不再交
    assert watch.watch(0, sleep=lambda s: None)["kind"] == "timeout"


def test_unseen_problem_between_watches_exits_1():
    rep("20261004-1640", status="done", ok=False, failed=["mall"], finished=now() - timedelta(hours=1),
        tasks=[{"task": "mall", "ok": False, "error": "信用商店卡住了"}])
    with pytest.raises(CliError) as e:
        watch.watch(5, sleep=lambda s: pytest.fail("不该等"))
    assert e.value.code == "daily_problem" and e.value.data["run_id"] == "20261004-1640"


def test_old_or_running_runs_are_not_returned():
    rep("20261002-1640", finished=now() - timedelta(hours=30))
    rep("20261004-1640", status="running", ok=None)
    assert watch.watch(0, sleep=lambda s: None)["kind"] == "timeout"


# --- 2. 日程提醒：同一个时间点只让 watch 醒一次；拉不下来别一直拉 -------------------------------------------------------

def test_news_wakes_once_per_key(monkeypatch):
    cache(EVENT, MAINT)
    _due_at(monkeypatch, at(10, 9, 12, 30))
    out = watch.watch(5, sleep=lambda s: pytest.fail("不该等"))
    assert out["kind"] == "news" and len(out["due"]) == 2
    # agent 还没 news told 就重挂：不再立刻醒；启动检查的 due 照样列着
    assert watch.watch(0, sleep=lambda s: None)["kind"] == "timeout"
    assert len(news.due()) == 2 and set(news.load()["handed"]) == {e["key"] for e in out["due"]}


def test_news_refresh_backs_off_after_failure(monkeypatch):
    cache(EVENT)
    monkeypatch.setattr(news, "stale", lambda d=None, now=None: True)
    monkeypatch.setattr(news, "due", lambda d=None, now=None: [])
    calls = []

    def boom():
        calls.append(1)
        raise OSError("断网")
    monkeypatch.setattr(news, "refresh", boom)
    t = {"v": 1000.0}
    monkeypatch.setattr(watch.time, "monotonic", lambda: t["v"])
    state: dict = {}
    for _ in range(5):                                                   # 20 秒一轮：只拉第一次
        assert watch._news_due(state) is None
        t["v"] += 20
    assert calls == [1]
    t["v"] += watch.NEWS_RETRY_S
    watch._news_due(state)
    assert calls == [1, 1]


# --- 3. told 和 refresh 同时写缓存 -------------------------------------------------------------------------------------

def test_refresh_keeps_told_written_while_fetching(monkeypatch):
    cache(EVENT)

    def slow_fetch(now, get):                                            # 拉公告期间，用户那边 news told 了
        news.mark_told(["6774:公告"])
        return news.load()["items"]
    monkeypatch.setattr(news, "fetch", slow_fetch)
    news.refresh(at(10, 9, 6))
    d = news.load()
    assert "6774:公告" in d["told"] and d["at"] == at(10, 9, 6).isoformat()


# --- 4. 非国服：不按国服公告跳过日常、不提醒 ----------------------------------------------------------------------------

@pytest.mark.parametrize("srv,applies", [("Official", True), ("Bilibili", True), ("YoStarEN", False),
                                         ("YoStarJP", False), ("txwy", False)])
def test_applies(monkeypatch, srv, applies):
    _server(monkeypatch, srv)
    assert news.applies() is applies


def test_non_cn_server_skips_maintenance_and_news(monkeypatch, capsys):
    _server(monkeypatch, "YoStarEN")
    cache(EVENT, MAINT)
    _due_at(monkeypatch, at(10, 9, 12, 30))
    monkeypatch.setattr(news, "refresh", lambda: pytest.fail("不该拉"))
    monkeypatch.setattr(news, "stale", lambda d=None, now=None: True)
    monkeypatch.setattr(news, "maintenance_now", lambda d=None, now=None: {"cid": "5699"})
    assert daily.refresh_news() is False and daily.maintenance_now() is None
    assert watch.watch(0, sleep=lambda s: None)["kind"] == "timeout"
    code = cli.main(["news", "list"])
    assert code == 2 and json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported_server"


def test_bilibili_uses_cn_schedule(monkeypatch):
    _server(monkeypatch, "Bilibili")
    monkeypatch.setattr(news, "maintenance_now", lambda d=None, now=None: {"cid": "5699"})
    assert daily.maintenance_now() == {"cid": "5699"}


# --- 5. 定时日常叫起的 agent 不能 news told ----------------------------------------------------------------------------

def test_news_told_refused_when_unattended(monkeypatch, capsys):
    from maapilot.flows.daily.base import DAILY_ENV
    cache(EVENT)
    monkeypatch.setenv(DAILY_ENV, "20261004-1640")
    code = cli.main(["news", "told", "6774:公告"])
    env = json.loads(capsys.readouterr().out)
    assert code == 2 and env["error"]["code"] == "not_allowed"
    assert not news.load().get("told")
    monkeypatch.delenv(DAILY_ENV)
    assert cli.main(["news", "told", "6774:公告"]) == 0
    assert "6774:公告" in news.load()["told"]
