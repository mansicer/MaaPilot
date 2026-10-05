"""MAA 详细日志（asst.log）：按时间段读、整理成事实（不下结论）；`maa why` 命令。

日志用真实格式的行拼成（2026-10-02 16:01–16:09 基建失败那次的代表性行：正在提交卡住 → 回到标题页 → 找不到 InfrastBegin）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from maapilot import cli, paths
from maapilot.crew import maalog


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


P = "[Px95034][Tx15413]"


def L(ts: str, level: str, msg: str) -> str:
    return f"[2026-10-02 {ts}][{level}]{P} {msg}"


def start(ts: str, task: str, action: str, algo: str, result: dict, first: list[str]) -> str:
    js = {"class": "asst::ProcessTask", "details": {"action": action, "algorithm": algo, "exec_times": 1,
                                                    "max_times": 2147483647, "result": result, "task": task},
          "first": first, "pre_task": "", "subtask": "ProcessTask", "taskchain": "Infrast", "taskid": 1,
          "uuid": "11111111"}
    return L(ts, "INF", "Assistant::append_callback | SubTaskStart " + json.dumps(js, ensure_ascii=False))


def sub_error(ts: str, first: list[str], cls: str = "asst::ProcessTask") -> str:
    js = {"class": cls, "details": {}, "first": first, "pre_task": "", "subtask": "ProcessTask",
          "taskchain": "Infrast", "taskid": 1, "uuid": "11111111"}
    return L(ts, "INF", "Assistant::append_callback | SubTaskError " + json.dumps(js, ensure_ascii=False))


def trying(ts: str, cur: str, first: list[str], retry: int = 0) -> str:
    js = {"class": "asst::ProcessTask", "cur_task": cur,
          "details": {"cur_retry": retry, "retry_times": 20, "to_be_recognized": first}, "first": first,
          "pre_task": "", "subtask": "ProcessTask", "taskchain": "Infrast", "taskid": 1}
    return L(ts, "INF", json.dumps(js, ensure_ascii=False))


def ocr(ts: str, *texts: str) -> str:
    parts = ", ".join(f"{{ text: {t}, rect: [ 276 (562), 6 (556), 54, 23 ], score: 0.913545 }}" for t in texts)
    return L(ts, "TRC", f"asst::WordOcr [{parts}] by OCR Pipeline , cost 46 ms")


def screencap(ts: str) -> str:
    return L(ts, "INF", "Call ` adb -s 127.0.0.1:5555 exec-out screencap -p ` ret 0 , cost 239 ms , "
                        "stdout size: 447470 , socket size: 0")


def filler(t0: datetime, n: int, step_s: float = 0.05) -> list[str]:
    """时间段之前的大量无关行（让二分真的有东西可分）。"""
    out = []
    for i in range(n):
        t = t0 + timedelta(seconds=i * step_s)
        out.append(f"[{t:%Y-%m-%d %H:%M:%S}.{t.microsecond // 1000:03d}][TRC][Px1][Tx1] ready to sleep 500 #{i}")
    return out


def window_lines() -> list[str]:
    chain = '{"taskchain":"Infrast","taskid":1,"uuid":"11111111"}'
    lines = [
        L("16:01:10.100", "INF", "Assistant::append_callback | TaskChainStart " + chain),
        L("16:01:09.947", "ERR", 'Killing child ` adb -s 127.0.0.1:5555 exec-out "screencap | nc -w 3 10.0.2.160 64468" `, pid: 95079'),
        start("16:06:51.610", "InfrastReceptionMessageBoardWithNew", "ClickSelf", "MatchTemplate",
              {"rect": [1, 2, 3, 4], "score": 0.99, "template": "InfrastReceptionMessageBoardWithNew.png"},
              ["InfrastReceptionReceiveMessageBoard"]),
    ]
    t = datetime(2026, 10, 2, 16, 6, 53, 543000)
    for i in range(39):                                   # 正在提交 卡了 31 秒
        ts = (t + timedelta(seconds=i * 0.8)).strftime("%H:%M:%S.%f")[:12]
        lines.append(start(ts, "LoadingText", "DoNothing", "OcrDetect",
                           {"rect": [728, 654, 187, 20], "score": 0.96, "text": "正在提交"},
                           ["InfrastReceptionReceiveMessageBoard"]))
        lines.append(ocr(ts, "正在提交"))
        lines.append(screencap(ts))
    lines += [
        start("16:07:30.878", "InfrastBottomLeftTab", "ClickRect", "JustReturn", {}, ["InfrastBottomLeftTab"]),
        trying("16:07:37.914", "", ["InfrastClueFriendNew", "ReceptionFlag"]),
        sub_error("16:07:52.209", ["InfrastClueFriendNew", "ReceptionFlag"]),
        L("16:08:13.578", "INF", 'Call ` adb -s 127.0.0.1:5555 shell "dumpsys SurfaceFlinger --latency | head -n 1" ` ret 0'),
        "16666666",                                        # 不带时间戳的续行
        ocr("16:08:30.338", "玩家/", "187"),
        sub_error("16:08:30.338", ["InfrastClueSelfFull"]),
        ocr("16:08:56.295", "__"),
        trying("16:08:56.023", "InfrastBegin", ["InfrastBegin"], 0),
        trying("16:09:17.230", "InfrastBegin", ["InfrastBegin"], 20),
        sub_error("16:09:17.994", ["InfrastBegin"]),
        L("16:09:18.008", "INF", "Assistant::append_callback | TaskChainError " + chain),
        L("16:09:18.008", "INF", 'Assistant::append_callback | AllTasksCompleted {"finished_tasks":[1],"taskchain":"Infrast"}'),
    ]
    return lines


@pytest.fixture
def maa_dir(tmp_path, monkeypatch):
    """假的 maa-cli 日志目录：asst.bak.log（更早的）、asst.log（大量更早的行 + 时间段 + 之后的行）、调试截图。"""
    d = tmp_path / "maa-debug"
    (d / "interface").mkdir(parents=True)
    (d / "other").mkdir()
    (d / "asst.bak.log").write_text("\n".join(filler(datetime(2026, 9, 30, 5, 0), 2000)) + "\n", encoding="utf-8")
    later = [L("16:30:08.466", "WRN", "correct_rect roi is empty"), ocr("16:30:09.000", "开始唤醒")]
    body = filler(datetime(2026, 10, 2, 10, 0), 60000) + window_lines() + later
    (d / "asst.log").write_text("\n".join(body) + "\n", encoding="utf-8")
    for name in ("2026.10.02-16.09.17.997_raw.png", "2026.10.02-15.57.29.422_raw.png",
                 "2026.10.02-16.30.10.1_raw.png"):
        (d / "interface" / name).write_bytes(b"png")
    (d / "other" / "2026.10.02-16.05.00.66_raw.png").write_bytes(b"png")
    (d / "interface" / "notes.txt").write_text("x")
    monkeypatch.setenv(maalog.DIR_ENV, str(d))
    return d


W0, W1 = datetime(2026, 10, 2, 16, 0, 59), datetime(2026, 10, 2, 16, 9, 23)


def test_read_window_only_reads_the_window(maa_dir):
    rows, info = maalog.read_window(W0, W1, maa_dir)
    assert rows[0][0] >= "2026-10-02 16:00:59" and rows[-1][0] <= "2026-10-02 16:09:23"
    assert not any("ready to sleep" in r[3] for r in rows) and not any("16:30" in r[0] for r in rows)
    assert any(r[3].endswith("\n16666666") for r in rows)                 # 续行接在上一行
    assert info["files"] == [str(maa_dir / "asst.log")]                   # bak 整个在时间段之前，不读
    size = (maa_dir / "asst.log").stat().st_size
    assert info["bytes_read"] < size / 4 and not info["truncated"]        # 二分定位，没从头读
    rows2, info2 = maalog.read_window(W0, W1, maa_dir, max_bytes=2000)
    assert info2["truncated"] and len(rows2) < len(rows)


def test_read_window_spans_rotated_file(tmp_path):
    d = tmp_path / "dbg"
    d.mkdir()
    (d / "asst.bak.log").write_text("\n".join(filler(datetime(2026, 10, 2, 9, 0), 100, 1.0)) + "\n")
    (d / "asst.log").write_text("\n".join(filler(datetime(2026, 10, 2, 9, 2), 100, 1.0)) + "\n")
    rows, info = maalog.read_window(datetime(2026, 10, 2, 9, 1, 30), datetime(2026, 10, 2, 9, 2, 30), d)
    assert len(info["files"]) == 2 and rows[0][0].startswith("2026-10-02 09:01:30")
    assert rows[-1][0].startswith("2026-10-02 09:02:30")


def test_summary_lists_the_facts(maa_dir):
    r = maalog.why(W0, W1, root=maa_dir)
    assert "likely_cause" not in r and "events" not in r                 # 只摆事实，不下结论
    assert r["last_match"] == {"at": "16:07:23", "task": "LoadingText", "matched": "正在提交"}
    (wait,) = [w for w in r["waits"] if w["task"] == "LoadingText"]
    assert wait["count"] == 39 and wait["first"] == "16:06:53" and wait["last"] == "16:07:23" and wait["seconds"] == 30
    assert [e["first"] for e in r["errors"]][-1] == ["InfrastBegin"] and r["error_count"] == 3
    texts = {o["text"]: o for o in r["ocr"]}
    assert texts["玩家/ | 187"]["first"] == "16:08:30" and texts["正在提交"]["count"] == 39 and "__" not in texts
    assert [o["text"] for o in r["ocr"]] == ["正在提交", "玩家/ | 187"]                  # 按第一次出现排
    assert r["screencap_failures"] == []                                  # nc 试截图那行不算截图失败
    assert any("Killing child" in e["message"] for e in r["error_lines"])  # 但 ERR 行照列
    assert [s["task"] for s in r["steps"]][-2:] == ["LoadingText", "InfrastBottomLeftTab"]
    assert r["steps"][-2]["repeat"] == 39
    assert r["tried"][-1]["cur_task"] == "InfrastBegin"
    assert [c["event"] for c in r["taskchains"]] == ["TaskChainStart", "TaskChainError"]
    assert r["screenshots"][-1]["path"].endswith("16.09.17.997_raw.png")       # maalog 给的是 MAA 的原位置


def _rows(*lines):
    out = []
    for ln in lines:
        m = maalog.LINE_RE.match(ln)
        out.append((m.group(1), m.group(2), m.group(3), m.group(4)))
    return out


def test_screencap_failures_and_connection_info():
    rows = _rows(
        L("10:00:01.000", "INF", "Call ` adb -s 1:5 exec-out screencap -p ` ret 1 , cost 5 ms , stdout size: 0"),
        L("10:00:02.000", "INF", "Call ` adb -s 1:5 exec-out screencap -p ` ret 1 , cost 6 ms , stdout size: 0"),
        L("10:00:03.000", "INF", "Call ` adb -s 1:5 exec-out screencap -p ` ret 0 , cost 6 ms , stdout size: 9"),
        L("10:00:04.000", "INF", 'Assistant::append_callback | ConnectionInfo {"details":{"why":"x"},"what":"Reconnecting"}'),
        L("10:00:05.000", "INF", 'Assistant::append_callback | ConnectionInfo {"details":{"avg":1},"what":"ScreencapCost"}'),
        ocr("10:00:06.000", "您的账号已在其他设备登录"))
    s = maalog.summarize(rows)
    (f,) = s["screencap_failures"]
    assert f["count"] == 2 and f["first"] == "10:00:01" and f["last"] == "10:00:02"
    assert [c["what"] for c in s["connection"]] == ["Reconnecting"]
    assert s["ocr"] == [{"text": "您的账号已在其他设备登录", "first": "10:00:06", "last": "10:00:06", "count": 1}]
    assert s["steps"] == [] and s["last_match"] is None


# --- maa why 命令 -----------------------------------------------------------------------------------------------------

def _record(**kw):
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    with open(paths.LOGS / "2026-10-02.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def test_maa_why_default_is_last_failed_task(maa_dir, capsys):
    _record(at="2026-10-02T16:09:18+08:00", command="maa infrast", args={}, ok=False, exit=1, duration_s=496.0,
            error="maa_failed", run_id="20261002-1558", shot_now="/x/fail-infrast.png")
    _record(at="2026-10-02T16:24:17+08:00", command="maa mall", args={}, ok=True, exit=0, duration_s=179.9)
    (paths.LOGS / "maa").mkdir()
    (paths.LOGS / "maa" / "20261002-160102-infrast.log").write_text("[2026-10-02 16:09:18 ERROR] Infrast Error\n")
    lock_before = paths.LOCK_FILE.exists()
    code, env = run(capsys, "maa", "why")
    assert code == 0, env
    d = env["data"]
    assert d["task"] == "infrast" and d["record"]["run_id"] == "20261002-1558" and d["shot_now"] == "/x/fail-infrast.png"
    assert d["maa_cli_log"].endswith("20261002-160102-infrast.log") and d["maa_cli_log_tail"] == [
        "[2026-10-02 16:09:18 ERROR] Infrast Error"]
    assert "likely_cause" not in d and any(o["text"] == "玩家/ | 187" for o in d["ocr"])
    shot = d["screenshots"][-1]                                               # 复制进项目，agent 读得到
    assert shot["path"].startswith(str(paths.SHOTS.resolve())) and shot["path"].endswith("-maa-infrast-997.png")
    assert shot["source"].endswith("2026.10.02-16.09.17.997_raw.png")
    assert d["window"]["start"].startswith("2026-10-02T16:00:5")
    assert paths.LOCK_FILE.exists() == lock_before                           # 不拿锁
    code, env = run(capsys, "maa", "why", "--task", "mall", "--last")
    assert code == 0 and env["data"]["record"]["command"] == "maa mall"


def test_maa_why_since_until_and_not_found(maa_dir, capsys):
    code, env = run(capsys, "maa", "why", "--since", "2026-10-02T16:06:00+08:00", "--until", "2026-10-02T16:09:20+08:00")
    assert code == 0 and env["data"]["waits"] and "record" not in env["data"]
    code, env = run(capsys, "maa", "why")
    assert code == 2 and env["error"]["code"] == "not_found"
    code, env = run(capsys, "maa", "why", "--until", "16:00")
    assert code == 2 and env["error"]["code"] == "bad_arguments"


def test_maa_why_without_maa_log_dir(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv(maalog.DIR_ENV, str(tmp_path / "missing"))
    code, env = run(capsys, "maa", "why", "--since", "2026-10-02T16:06:00+08:00")
    assert code == 0 and env["data"]["log"]["dir"] is None and "hint" in env["data"]
    assert env["data"]["lines"] == 0 and env["data"]["screenshots"] == []
