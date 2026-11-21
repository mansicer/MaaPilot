"""用户反馈：起草只写本机；上传的内容是骨架和脱敏后的文字；给用户看过的那一版才能发。"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from maapilot import cli, paths
from maapilot.tools import feedback, scrub


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "checks", lambda: [{"name": "config", "ok": True, "detail": "模拟器 10.0.0.5:5555"}])
    monkeypatch.setattr(scrub, "known_values", lambda: [(str(tmp_path), "<家目录>", "路径"), ("博士甲", "<昵称>", "账号"),
                                                        ("alice", "<用户>", scrub.USERNAME)])
    f = tmp_path / "session.md"
    f.write_text(f"用户说日常停了。我看了 {tmp_path}/private/log，昵称 博士甲，邮箱 a@b.com，alice 在 alice.top 上。\n",
                 encoding="utf-8")
    return f


def draft(capsys, session, *more):
    code, env = run(capsys, "feedback", "draft", "--text", "日常跑到一半停了", "--session-file", str(session),
                    "--harness", "Claude Code 桌面版", "--model", "claude-x", "--category", "bug", *more)
    assert code == 0, env
    return env["data"]


def bundle(fid: str):
    return paths.PRIVATE / "feedback" / fid / "bundle"


# --- 脱敏 ------------------------------------------------------------------------------------

def test_scrubber_replaces_known_values_and_shapes():
    s = scrub.Scrubber(masks=["张三"], known=[("/home/bob/proj", "<项目>", "路径"), ("/home/bob", "<家目录>", "路径"),
                                               ("bob", "<用户>", scrub.USERNAME), ("12345678", "<UID>", "账号")])
    out = s.apply("在 /home/bob/proj/private 和 /home/bob/.cache；uid 12345678；bob 说张三的邮箱是 x.y@mail.com；"
                  "连 192.168.1.20:5555 和 emulator-5554；C:\\Users\\carol\\x；bobby 和 bob.com 不动")
    assert "<项目>/private" in out and "<家目录>/.cache" in out and "<UID>" in out and "<已隐去>" in out
    assert "x.y@mail.com" not in out and "192.168.1.20" not in out and "emulator-5554" not in out and "carol" not in out
    assert "bobby" in out and "bob.com" in out and " bob 说" not in out and "<用户> 说" in out
    assert s.counts["路径"] >= 3 and s.counts["账号"] == 1 and s.counts["指定的字样"] == 1


def test_scrubber_keeps_what_debugging_needs():
    s = scrub.Scrubber(known=[])
    text = "maapilot maa fight 1-7 退出码 1 maa_failed，版本 0.3.2（e4b208c），MaaCore v6.21.4，2026-11-20T04:40:00"
    assert s.apply(text) == text and s.counts == {}


def test_scrubber_json_leaves_keys_alone():
    s = scrub.Scrubber(known=[("secret-name", "<昵称>", "账号")])
    assert s.apply_json({"secret-name": ["secret-name", 3, {"a": "x secret-name"}]}) == \
        {"secret-name": ["<昵称>", 3, {"a": "x <昵称>"}]}


# --- 起草 ------------------------------------------------------------------------------------

def test_draft_writes_only_locally_and_scrubs(capsys, session, tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "http_post", lambda *a, **k: pytest.fail("起草不该联网"))
    d = draft(capsys, session)
    assert d["status"] == "draft" and d["includes"]["session"] and not d["includes"]["raw_session"]
    text = (bundle(d["id"]) / "session.md").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "博士甲" not in text and "a@b.com" not in text
    assert "<家目录>/private/log" in text and "alice.top" in text and "<用户> 在" in text
    info = json.loads((bundle(d["id"]) / "feedback.json").read_text(encoding="utf-8"))
    assert info["text"] == "日常跑到一半停了" and info["harness"]["name"] == "Claude Code 桌面版"
    assert info["model"] == {"name": "claude-x", "effort": None}
    assert info["auto"]["checks"][0]["detail"] == "模拟器 <地址>"
    assert d["scrubbed"]["路径"] >= 1 and d["scrubbed"]["账号"] == 1


def test_journal_and_daily_carry_only_the_skeleton(capsys, session):
    paths.LOGS.mkdir(parents=True)
    rows = [{"at": "t1", "command": "state depot", "args": {"x": 1}, "ok": True, "exit": 0, "duration_s": 2.0,
             "result": {"lmd": 123456, "orundum": 600}},
            {"at": "t2", "command": "op upgrade", "args": {"name": "遥"}, "ok": False, "exit": 1, "duration_s": 9.1,
             "error": "RuntimeError: /home/bob/x 读不了", "message": "遥 的等级读不出", "run_id": "r1"}]
    (paths.LOGS / "2026-11-20.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                                 encoding="utf-8")
    paths.REPORTS.mkdir(parents=True)
    (paths.REPORTS / "2026-11-20-0440.json").write_text(json.dumps({
        "run_id": "r1", "trigger": "schedule", "status": "done", "ok": False, "failed": ["infrast"],
        "tasks": [{"task": "infrast", "ok": False, "code": "maa_failed", "error": "…", "log": "/home/bob/log"}],
        "depot_change": {"lmd": -5000}, "goals_summary": "遥 精2", "handoff": {"task": "infrast", "log": "/x"}}),
        encoding="utf-8")
    d = draft(capsys, session)
    raw = (bundle(d["id"]) / "feedback.json").read_text(encoding="utf-8")
    auto = json.loads(raw)["auto"]
    assert auto["journal"] == [
        {"at": "t1", "command": "state depot", "ok": True, "exit": 0, "duration_s": 2.0, "error": None},
        {"at": "t2", "command": "op upgrade", "ok": False, "exit": 1, "duration_s": 9.1, "error": "RuntimeError",
         "run_id": "r1"}]
    assert auto["last_daily"]["tasks"] == [{"task": "infrast", "ok": False, "code": "maa_failed"}]
    assert auto["last_daily"]["handoff_task"] == "infrast"
    for leak in ("123456", "orundum", "遥", "/home/bob", "depot_change", "goals_summary"):
        assert leak not in raw, leak


def test_optional_parts_only_when_asked(capsys, session, tmp_path):
    (paths.LOGS / "maa").mkdir(parents=True)
    (paths.LOGS / "maa" / "20261120-044000-infrast.log").write_text("a\n" * 500 + "最后一行\n", encoding="utf-8")
    paths.SHOTS.mkdir(parents=True)
    (paths.SHOTS / "x.png").write_bytes(b"\x89PNG fake")
    d = draft(capsys, session)
    assert d["includes"]["maa_log"] is False and d["includes"]["shots"] == []
    raw = tmp_path / "raw.txt"
    raw.write_text("工具调用 1：maapilot doctor → 退出码 5\n", encoding="utf-8")
    d = draft(capsys, session, "--maa-log", "--shot", str(paths.SHOTS / "x.png"), "--raw-session-file", str(raw),
              "--contact", "qq 1")
    inc = d["includes"]
    assert inc["maa_log"] and inc["shots"] == ["1.png"] and inc["raw_session"] and inc["contact"]
    log = (bundle(d["id"]) / "maa-log.txt").read_text(encoding="utf-8")
    assert log.splitlines()[0] == "# 20261120-044000-infrast.log" and log.count("\n") == feedback.MAA_LOG_LINES + 1
    assert "最后一行" in log


def test_draft_refuses_bad_input(capsys, session, tmp_path):
    outside = tmp_path / "x.png"
    outside.write_bytes(b"png")
    base = ["feedback", "draft", "--session-file", str(session), "--harness", "h", "--model", "m"]
    for extra, code in ((["--text", "x", "--shot", str(outside)], "bad_file"),
                        (["--text", "  "], "bad_arguments"),
                        (["--text", "x", "--category", "nope"], "bad_arguments")):
        rc, env = run(capsys, *base, *extra)
        assert rc == 2 and env["error"]["code"] == code, env
    rc, env = run(capsys, "feedback", "draft", "--text", "x", "--session-file", str(tmp_path / "none"),
                  "--harness", "h", "--model", "m")
    assert rc == 2 and env["error"]["code"] == "bad_file"
    rc, env = run(capsys, "feedback", "draft", "--text", "x", "--session-file", str(session), "--harness", "h")
    assert rc == 2 and env["error"]["code"] == "bad_arguments"          # 模型必须写


# --- 查看、修改 ------------------------------------------------------------------------------

def test_show_part_and_redact_change_the_digest(capsys, session, tmp_path):
    raw = tmp_path / "raw.txt"
    raw.write_text("我在公司 Acme 的电脑上\n", encoding="utf-8")
    d = draft(capsys, session, "--raw-session-file", str(raw), "--contact", "c")
    rc, env = run(capsys, "feedback", "show", d["id"], "--part", "raw-session")
    assert rc == 0 and env["data"]["digest"] == d["digest"] and "Acme" in env["data"]["part"]["text"]
    rc, env = run(capsys, "feedback", "redact", d["id"], "--mask", "Acme", "--remove", "contact", "--text", "新的原话")
    new = env["data"]
    assert rc == 0 and new["digest"] != d["digest"] and new["masks"] == 1 and not new["includes"]["contact"]
    assert "Acme" not in (bundle(d["id"]) / "session-raw.txt").read_text(encoding="utf-8")
    info = json.loads((bundle(d["id"]) / "feedback.json").read_text(encoding="utf-8"))
    assert info["text"] == "新的原话" and info["contact"] is None
    rc, env = run(capsys, "feedback", "redact", d["id"], "--remove", "raw-session")
    assert rc == 0 and not env["data"]["includes"]["raw_session"] and not (bundle(d["id"]) / "session-raw.txt").exists()
    rc, env = run(capsys, "feedback", "show", d["id"], "--part", "raw-session")
    assert rc == 2 and env["error"]["code"] == "no_part"


def test_list_and_discard(capsys, session):
    d = draft(capsys, session)
    rc, env = run(capsys, "feedback", "list")
    assert [x["id"] for x in env["data"]["drafts"]] == [d["id"]]
    rc, env = run(capsys, "feedback", "discard", d["id"])
    assert rc == 0 and not (paths.PRIVATE / "feedback" / d["id"]).exists()
    rc, env = run(capsys, "feedback", "show", d["id"])
    assert rc == 2 and env["error"]["code"] == "no_draft"
    rc, env = run(capsys, "feedback", "show", "../x")
    assert rc == 2 and env["error"]["code"] == "bad_id"


# --- 发送 ------------------------------------------------------------------------------------

class Server:
    """假的服务器：默认像真的一样回 {"ok": true, "id": <清单里的编号>}；给了 reply 就回它。"""

    def __init__(self, status=200, reply=None):
        self.status, self.reply, self.calls = status, reply, []

    def __call__(self, url, body, headers, timeout=60):
        self.calls.append((url, body, headers))
        if self.reply is not None:
            return self.status, self.reply
        boundary = headers["Content-Type"].split("boundary=")[1].encode()
        manifest = json.loads(body.split(b"--" + boundary)[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
        return self.status, json.dumps({"ok": True, "id": manifest["id"]}).encode()


def test_send_uploads_exactly_the_bundle(capsys, session, monkeypatch):
    srv = Server()
    monkeypatch.setattr(feedback, "http_post", srv)
    d = draft(capsys, session)
    rc, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])
    assert rc == 0 and env["data"]["server_id"] == d["id"]
    url, body, headers = srv.calls[0]
    assert url == feedback.URL and headers["X-MaaPilot-Feedback"] == "1"
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    boundary = headers["Content-Type"].split("boundary=")[1].encode()
    parts = body.split(b"--" + boundary)
    manifest = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
    blob = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
    assert manifest["id"] == d["id"] and manifest["digest"] == d["digest"] and manifest["bytes"] == len(blob)
    assert manifest["preview"] == "日常跑到一半停了"
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert sorted(z.namelist()) == ["feedback.json", "session.md"] == sorted(manifest["files"])
        assert z.read("session.md") == (bundle(d["id"]) / "session.md").read_bytes()
    rc, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])       # 不发第二遍
    assert rc == 2 and env["error"]["code"] == "already_sent" and len(srv.calls) == 1
    rc, env = run(capsys, "feedback", "discard", d["id"])
    assert rc == 2 and env["error"]["code"] == "already_sent"


def test_send_refuses_a_draft_changed_after_the_user_saw_it(capsys, session, monkeypatch):
    monkeypatch.setattr(feedback, "http_post", lambda *a, **k: pytest.fail("不该发"))
    d = draft(capsys, session)
    with open(bundle(d["id"]) / "session.md", "a", encoding="utf-8") as f:
        f.write("后来加的一句\n")
    rc, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])
    assert rc == 2 and env["error"]["code"] == "draft_changed" and env["data"]["digest"] != d["digest"]


def test_send_refused_during_a_daily_run(capsys, session, monkeypatch):
    from maapilot.tools import confirm
    monkeypatch.setattr(feedback, "http_post", lambda *a, **k: pytest.fail("不该发"))
    d = draft(capsys, session)
    monkeypatch.setattr(confirm, "daily_in_progress", lambda: "r1")
    rc, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])
    assert rc == 3 and env["error"]["code"] == "needs_user"


@pytest.mark.parametrize("status,reply,code,rc", [
    (429, b'{"ok": false, "error": "rate_limited"}', "rate_limited", 1),
    (429, b'{"ok": false, "error": "daily_limit"}', "server_full", 1),
    (413, b"nope", "too_big", 2),
    (500, b"nope", "upload_failed", 1),
    (400, b'{"ok": false, "error": "bad_id"}', "rejected", 2),             # 服务器明确不收：不能叫 agent 原样重发
    (409, b'{"ok": false, "error": "id_taken"}', "rejected", 2),
    (200, b"<html>please log in</html>", "upload_failed", 1),              # 要登录的 Wi-Fi 返回的网页不算发出去了
    (200, b'{"ok": true, "id": "someone-else"}', "upload_failed", 1),
    (200, b'{"id": "x"}', "upload_failed", 1),
])
def test_send_failures_keep_the_draft(capsys, session, monkeypatch, status, reply, code, rc):
    monkeypatch.setattr(feedback, "http_post", Server(status=status, reply=reply))
    d = draft(capsys, session)
    got, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])
    assert got == rc and env["error"]["code"] == code
    assert run(capsys, "feedback", "show", d["id"])[1]["data"]["status"] == "draft"


def test_send_network_error_and_size_limit(capsys, session, monkeypatch):
    def down(*a, **k):
        raise OSError("no route")
    monkeypatch.setattr(feedback, "http_post", down)
    d = draft(capsys, session)
    rc, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])
    assert rc == 1 and env["error"]["code"] == "network"
    monkeypatch.setattr(feedback, "MAX_BYTES", 10)
    rc, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])
    assert rc == 2 and env["error"]["code"] == "too_big"


def test_pack_is_stable(capsys, session):
    d = draft(capsys, session)
    dd = paths.PRIVATE / "feedback" / d["id"]
    assert feedback.pack(dd) == feedback.pack(dd) and feedback.digest(dd) == d["digest"]


# --- 审核里找出来的问题 ----------------------------------------------------------------------

def test_non_utf8_files_are_refused_not_passed_through(capsys, session, tmp_path):
    bad = tmp_path / "s16.md"
    bad.write_text("路径 /home/x 邮箱 a@b.com\n", encoding="utf-16")              # PowerShell 的 > 写出来的就是这样
    rc, env = run(capsys, "feedback", "draft", "--text", "x", "--session-file", str(bad), "--harness", "h", "--model", "m")
    assert rc == 2 and env["error"]["code"] == "bad_file" and "UTF-8" in env["error"]["message"]
    assert not (paths.PRIVATE / "feedback").exists() or not list((paths.PRIVATE / "feedback").iterdir())
    bom = tmp_path / "bom.md"
    bom.write_bytes(b"\xef\xbb\xbf" + "带 BOM 的\n".encode())
    d = draft(capsys, bom)
    assert (bundle(d["id"]) / "session.md").read_text(encoding="utf-8") == "带 BOM 的\n"


def test_session_file_cannot_be_another_private_file(capsys, session):
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    (paths.PRIVATE / "skland.json").write_text('{"token": "x"}', encoding="utf-8")
    rc, env = run(capsys, "feedback", "draft", "--text", "x", "--session-file", str(paths.PRIVATE / "skland.json"),
                  "--harness", "h", "--model", "m")
    assert rc == 2 and env["error"]["code"] == "bad_file"
    ok = paths.PRIVATE / "feedback" / "session-1.md"                         # private/feedback/ 下的可以
    ok.parent.mkdir(parents=True, exist_ok=True)
    ok.write_text("经过\n", encoding="utf-8")
    assert draft(capsys, ok)["includes"]["session"]


def test_short_names_are_scrubbed_and_one_char_is_reported():
    s = scrub.Scrubber(masks=["阿米"], known=[("博士", "<昵称>", "账号"), ("li", "<用户>", scrub.USERNAME),
                                              ("X", "<昵称>", "账号"), ("玩家#1234", "<昵称>", "账号"), ("玩家", "<昵称>", "账号")])
    out = s.apply("博士和阿米在 li 的电脑上；list 和 alice 不动；玩家#1234 也叫 玩家；X 光")
    assert "博士" not in out and "阿米" not in out and " li " not in out and "list" in out and "alice" in out
    assert "玩家" not in out and "X 光" in out and s.skipped == ["账号"]
    assert scrub.Scrubber(known=[("阿米", "<昵称>", "账号")]).apply('{"n": "\\u963f\\u7c73"}') == '{"n": "<昵称>"}'


def test_one_char_mask_is_refused(capsys, session):
    rc, env = run(capsys, "feedback", "draft", "--text", "x", "--session-file", str(session), "--harness", "h",
                  "--model", "m", "--mask", "张")
    assert rc == 2 and env["error"]["code"] == "bad_arguments"


def test_email_is_recognised_before_the_username_and_paths_ignore_case():
    s = scrub.Scrubber(known=[("alice", "<用户>", scrub.USERNAME), ("C:\\Users\\Alice\\proj", "<项目>", scrub.PATH)])
    out = s.apply("alice@alice-family.org；c:\\users\\alice\\proj\\x；ALICE 的 alice.log；alice.top")
    assert "alice-family" not in out and out.startswith("<邮箱>；<项目>\\x；<用户> 的 <用户>.log；alice.top")


def test_git_bash_and_wsl_paths_and_other_shapes():
    out = scrub.Scrubber(known=[]).apply("/c/Users/carol/a /mnt/d/Users/dave/b /home/erin/c 13812345678 "
                                         "aa:bb:cc:dd:ee:ff MAA v5.12.3.1 999.1.1.1 10.0.0.5")
    for gone in ("carol", "dave", "erin", "13812345678", "aa:bb:cc:dd:ee:ff", "10.0.0.5"):
        assert gone not in out, gone
    assert "v5.12.3.1" in out and "999.1.1.1" in out


def test_scrubber_is_fast_on_long_runs():
    import time
    t = time.monotonic()
    scrub.Scrubber(known=[]).apply("a" * 300_000 + " " + "0123456789abcdef" * 20_000)
    assert time.monotonic() - t < 2


def test_journal_command_never_carries_arguments(capsys, session):
    paths.LOGS.mkdir(parents=True)
    rows = [{"at": "2026-11-20T04:40:00+08:00", "command": "goals 阿米的秘密", "ok": False, "exit": 2},
            {"at": "t2", "command": "gaols 13812345678", "ok": False, "exit": 2, "error": "乱写的 错误"},
            {"at": "t3", "command": "maa fight", "ok": True, "exit": 0},
            {"at": "t4", "command": "doctor", "ok": True, "exit": 0}]
    (paths.LOGS / "2026-11-20.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                                 encoding="utf-8")
    d = draft(capsys, session)
    raw = (bundle(d["id"]) / "feedback.json").read_text(encoding="utf-8")
    assert [r["command"] for r in json.loads(raw)["auto"]["journal"]] == ["goals", "?", "maa fight", "doctor"]
    assert "阿米的秘密" not in raw and "13812345678" not in raw and "乱写" not in raw


def test_structural_fields_and_contact_are_not_scrubbed(capsys, session):
    d = draft(capsys, session, "--mask", "bug", "--mask", "fb-", "--contact", "me@example.com")
    info = json.loads((bundle(d["id"]) / "feedback.json").read_text(encoding="utf-8"))
    assert info["category"] == "bug" and info["id"] == d["id"] and info["contact"] == "me@example.com"
    assert info["schema"] == 1 and info["created_at"].startswith("20")


def test_text_from_a_file_keeps_shell_characters(capsys, session, tmp_path):
    t = tmp_path / "text.md"
    t.write_text('他说"不行"，$(rm -rf) 和 `x`\n第二行\n', encoding="utf-8")
    rc, env = run(capsys, "feedback", "draft", "--text-file", str(t), "--session-file", str(session),
                  "--harness", "h", "--model", "m")
    assert rc == 0
    info = json.loads((bundle(env["data"]["id"]) / "feedback.json").read_text(encoding="utf-8"))
    assert info["text"] == '他说"不行"，$(rm -rf) 和 `x`\n第二行'
    rc, env = run(capsys, "feedback", "draft", "--session-file", str(session), "--harness", "h", "--model", "m")
    assert rc == 2 and env["error"]["code"] == "bad_arguments"            # 原话必须有


def test_malformed_report_does_not_block_a_draft(capsys, session):
    paths.REPORTS.mkdir(parents=True)
    (paths.REPORTS / "2026-11-20-0440.json").write_text('["not", "a", "dict"]', encoding="utf-8")
    assert draft(capsys, session)["includes"]["last_daily"] is False
    (paths.REPORTS / "2026-11-20-0441.json").write_text('{"tasks": "oops", "failed": 3, "run_id": "含 空格"}', encoding="utf-8")
    d = draft(capsys, session)
    info = json.loads((bundle(d["id"]) / "feedback.json").read_text(encoding="utf-8"))
    assert info["auto"]["last_daily"]["tasks"] == [] and info["auto"]["last_daily"]["run_id"] is None


def test_failed_draft_leaves_nothing_behind(capsys, session, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(feedback, "journal", boom)
    rc, env = run(capsys, "feedback", "draft", "--text", "x", "--session-file", str(session), "--harness", "h", "--model", "m")
    assert rc == 1 and not list((paths.PRIVATE / "feedback").iterdir())


def test_stray_files_are_not_uploaded_and_do_not_change_the_digest(capsys, session, monkeypatch):
    srv = Server()
    monkeypatch.setattr(feedback, "http_post", srv)
    d = draft(capsys, session)
    (bundle(d["id"]) / ".DS_Store").write_bytes(b"junk")
    (bundle(d["id"]) / "notes.txt").write_text("别的文件", encoding="utf-8")
    rc, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])
    assert rc == 0
    boundary = srv.calls[0][2]["Content-Type"].split("boundary=")[1].encode()
    blob = srv.calls[0][1].split(b"--" + boundary)[2].split(b"\r\n\r\n", 1)[1][:-2]
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert sorted(z.namelist()) == ["feedback.json", "session.md"]


def test_broken_drafts_cannot_be_sent_but_can_be_discarded(capsys, session, monkeypatch):
    monkeypatch.setattr(feedback, "http_post", lambda *a, **k: pytest.fail("不该发"))
    d = draft(capsys, session)
    (bundle(d["id"]) / "feedback.json").write_text("{not json", encoding="utf-8")
    for cmd in (["send", d["id"], "--digest", d["digest"]], ["redact", d["id"], "--mask", "某某"], ["show", d["id"]]):
        rc, env = run(capsys, "feedback", *cmd)
        assert rc == 2 and env["error"]["code"] == "bad_draft", cmd
    d2 = draft(capsys, session)
    (paths.PRIVATE / "feedback" / d2["id"] / "state.json").write_text("garbage", encoding="utf-8")
    rc, env = run(capsys, "feedback", "send", d2["id"], "--digest", d2["digest"])
    assert rc == 2 and env["error"]["code"] == "bad_draft"
    rc, env = run(capsys, "feedback", "list")
    assert {x["id"]: x["status"] for x in env["data"]["drafts"]} == {d["id"]: "broken", d2["id"]: "broken"}
    for fid in (d["id"], d2["id"]):
        assert run(capsys, "feedback", "discard", fid)[0] == 0


def test_old_drafts_are_refused_before_the_server_would(capsys, session, monkeypatch):
    from datetime import timedelta
    monkeypatch.setattr(feedback, "http_post", lambda *a, **k: pytest.fail("不该发"))
    d = draft(capsys, session)
    real = feedback.common.now
    monkeypatch.setattr(feedback.common, "now", lambda: real() + timedelta(hours=49))
    rc, env = run(capsys, "feedback", "show", d["id"])
    assert env["data"]["expired"] is True
    rc, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])
    assert rc == 2 and env["error"]["code"] == "draft_expired"


def test_connection_dropped_mid_reply_is_a_network_error(capsys, session, monkeypatch):
    import http.client

    def dropped(*a, **k):
        raise http.client.IncompleteRead(b"")
    monkeypatch.setattr(feedback, "http_post", dropped)
    d = draft(capsys, session)
    rc, env = run(capsys, "feedback", "send", d["id"], "--digest", d["digest"])
    assert rc == 1 and env["error"]["code"] == "network"


def test_redact_conflicting_options_and_summary_flags(capsys, session, tmp_path, monkeypatch):
    raw = tmp_path / "raw.txt"
    raw.write_text("x\n", encoding="utf-8")
    d = draft(capsys, session, "--raw-session-file", str(raw))
    assert d["url_is_default"] is True and d["expired"] is False and d["not_scrubbed"] == []
    rc, env = run(capsys, "feedback", "redact", d["id"], "--remove", "raw-session", "--raw-session-file", str(raw))
    assert rc == 2 and env["error"]["code"] == "bad_arguments"
    monkeypatch.setenv(feedback.URL_ENV, "https://elsewhere.example/feedback")
    assert run(capsys, "feedback", "show", d["id"])[1]["data"]["url_is_default"] is False
