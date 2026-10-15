"""maa 任务失败时的取证：当场截图、MAA 自己在这段时间存的截图（maa-cli 日志目录下的调试截图）。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest

from maapilot import cli, paths
from maapilot.crew import lock, maalog
from maapilot.tools import common
from tests.test_tools import FakeGame, cfg  # noqa: F401  复用假的 maa 与假的模拟器


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def game(cfg, monkeypatch):
    g = FakeGame()
    monkeypatch.setattr(common, "connect", lambda ocr=False, readonly=False: g.connected(readonly))
    return g


@pytest.fixture
def maa_dir(tmp_path, monkeypatch):
    """假的 maa-cli 日志目录：几个子目录里的调试截图（文件名是本地时间，毫秒可能不补零）。"""
    d = tmp_path / "maa-debug"
    (d / "interface").mkdir(parents=True)
    (d / "other").mkdir()
    for name in ("2026.10.02-16.09.17.997_raw.png", "2026.10.02-15.57.29.422_raw.png",
                 "2026.10.02-16.30.10.1_raw.png"):
        (d / "interface" / name).write_bytes(b"png")
    (d / "other" / "2026.10.02-16.05.00.66_raw.png").write_bytes(b"png")
    (d / "interface" / "notes.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv(maalog.DIR_ENV, str(d))
    return d


W0, W1 = datetime(2026, 10, 2, 16, 0, 59), datetime(2026, 10, 2, 16, 9, 23)


def test_screenshots_in_window(maa_dir):
    shots = maalog.screenshots(W0, W1 + timedelta(seconds=30), maa_dir)
    assert [s["kind"] for s in shots] == ["other", "interface"]
    assert shots[-1]["path"].endswith("2026.10.02-16.09.17.997_raw.png") and os.path.isabs(shots[-1]["path"])
    assert shots[0]["at"].startswith("2026-10-02T16:05:00")
    assert maalog.screenshots(W0, W1, maa_dir, limit=1)[0]["kind"] == "interface"
    assert maalog.debug_dir() == maa_dir


def test_import_shots_copies_into_project(maa_dir, tmp_path):
    shots = maalog.screenshots(W0, W1 + timedelta(seconds=30), maa_dir)
    got = maalog.import_shots(shots, "infrast")
    assert [os.path.basename(g["path"]) for g in got] == ["20261002-160500-maa-infrast-066.png",
                                                         "20261002-160917-maa-infrast-997.png"]
    assert all(g["path"].startswith(str(paths.SHOTS.resolve())) and os.path.exists(g["path"]) for g in got)
    assert got[1]["source"] == shots[1]["path"] and got[1]["at"] == shots[1]["at"]
    again = maalog.import_shots(shots, "infrast")                         # 再来一次：用已有的副本，不重复
    assert [g["path"] for g in again] == [g["path"] for g in got] and len(list(paths.SHOTS.iterdir())) == 2
    missing = maalog.import_shots([{"path": str(tmp_path / "nope" / "2026.10.02-16.00.00.1_raw.png")}], "x")
    assert missing[0]["path"] is None and "复制不了" in missing[0]["error"]


def test_maa_failure_collects_evidence(cfg, game, maa_dir, capsys, monkeypatch):
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    now = datetime.now()
    recent = [f"{t:%Y.%m.%d-%H.%M.%S}.{t.microsecond // 1000}_raw.png" for t in (now - timedelta(seconds=1),)]
    (maa_dir / "other" / recent[0]).write_bytes(b"png")                 # 这次任务里 MAA 存的
    old = now - timedelta(minutes=5)
    (maa_dir / "interface" / f"{old:%Y.%m.%d-%H.%M.%S}.1_raw.png").write_bytes(b"png")   # 任务开始前的，不要
    (cfg / "stdout.txt").write_text("Summary\n[infrast] 16:01:02 - 16:09:18 (8m) Error\n", encoding="utf-8")
    (cfg / "log.txt").write_text("[2026-10-02 16:09:18 ERROR] Infrast Error\n", encoding="utf-8")
    (cfg / "rc").write_text("1", encoding="utf-8")
    code, env = run(capsys, "maa", "infrast")
    assert code == 1 and env["error"]["code"] == "maa_failed"
    ev = env["data"]["evidence"]
    assert ev["shot_now"].endswith("-fail-infrast.png") and os.path.exists(ev["shot_now"])
    # MAA 的截图在项目外（无头 agent 读不了）：复制进 private/shots/，原来的位置在 source
    (shot,) = ev["maa_shots"]
    assert shot["source"].endswith(recent[0]) and shot["kind"] == "other"
    assert shot["path"].startswith(str(paths.SHOTS.resolve())) and "-maa-infrast-" in shot["path"]
    assert open(shot["path"], "rb").read() == b"png"
    assert "missing" not in ev and ev["why"] == "maapilot maa why --task infrast"
    assert "_log" not in env["data"]
    rows = [json.loads(l) for l in next(paths.LOGS.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["shot_now"] == ev["shot_now"]


def test_evidence_failure_never_fails_the_command(cfg, capsys, tmp_path, monkeypatch):
    """模拟器连不上、找不到 MAA 的日志目录：命令照常按 maa 的结果报错，取证只记下没拿到什么。"""
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    monkeypatch.setenv(maalog.DIR_ENV, str(tmp_path / "missing"))
    (cfg / "stdout.txt").write_text("Summary\n[recruit] 10:00:00 - 10:01:00 (1m) Error\n", encoding="utf-8")
    (cfg / "log.txt").write_text("[x] GameOffline\n", encoding="utf-8")
    (cfg / "rc").write_text("1", encoding="utf-8")
    code, env = run(capsys, "maa", "recruit")
    assert code == 1 and env["error"]["code"] == "offline"
    ev = env["data"]["evidence"]
    assert ev["shot_now"] is None and ev["maa_shots"] == [] and len(ev["missing"]) == 2


def test_success_has_no_evidence(cfg, capsys, monkeypatch):
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    (cfg / "stdout.txt").write_text("Summary\n[mall] 10:00:00 - 10:01:00 (1m) Completed\n", encoding="utf-8")
    code, env = run(capsys, "maa", "mall")
    assert code == 0 and "evidence" not in env["data"]
