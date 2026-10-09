"""`maapilot game version / update`：版本号解析、比版本、下载（断点续传、校验）、安装、核对。

全部用假的 HTTP 和假的 adb（不联网、不碰模拟器）。
"""
from __future__ import annotations

import json

import pytest

from maapilot import cli, config, paths
from maapilot.tools import game

APK_URL = "https://ak.hycdn.cn/announce/Android/xx/78.0.0_abc/arknights-hg-2781.apk"
BODY = b"PK\x03\x04" + bytes(range(256)) * 40          # 假安装包（zip 开头）

DUMPSYS = """Packages:
  Package [com.hypergryph.arknights] (abc):
    versionCode=2771 minSdk=24 targetSdk=33
    versionName=2.7.71
    lastUpdateTime=2026-09-01 12:00:00
"""


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, json.loads(out.out)


class FakeResp:
    def __init__(self, url, status, headers, body=b"", fail_after=None):
        self.url, self.status, self.headers = url, status, headers
        self.body, self.pos, self.fail_after = body, 0, fail_after
        self.closed = False

    def geturl(self):
        return self.url

    def read(self, n=-1):
        if self.fail_after is not None and self.pos >= self.fail_after:
            raise OSError("connection reset")
        n = len(self.body) - self.pos if n < 0 else n
        if self.fail_after is not None:
            n = min(n, self.fail_after - self.pos)
        out = self.body[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def close(self):
        self.closed = True


class FakeNet:
    """按 Range 回应 BODY 的假下载服务器。"""

    def __init__(self, url=APK_URL, body=BODY, fail_after=None, ranges=True):
        self.url, self.body, self.fail_after, self.ranges = url, body, fail_after, ranges
        self.calls = []

    def __call__(self, url, start=0, end=None):
        self.calls.append((url, start, end))
        total = len(self.body)
        if not self.ranges:
            return FakeResp(self.url, 200, {"Content-Length": str(total)}, self.body)
        stop = total - 1 if end is None else end
        part = self.body[start:stop + 1]
        fail = None if self.fail_after is None else max(0, self.fail_after - start)
        return FakeResp(self.url, 206, {"Content-Range": f"bytes {start}-{stop}/{total}",
                                        "Content-Length": str(len(part))}, part, fail)


class FakeAdb:
    def __init__(self, versions=("2.7.71",), install_out="Performing Streamed Install\nSuccess\n", install_rc=0):
        self.versions = list(versions)
        self.install_out, self.install_rc = install_out, install_rc
        self.calls = []

    def __call__(self, argv, timeout=30):
        self.calls.append(argv)
        if "dumpsys" in argv:
            v = self.versions.pop(0) if len(self.versions) > 1 else self.versions[0]
            return (0, DUMPSYS.replace("2.7.71", v)) if v else (0, "Unable to find package: com.hypergryph.arknights\n")
        if "force-stop" in argv:
            return 0, ""
        if "install" in argv:
            return self.install_rc, self.install_out
        raise AssertionError(argv)


@pytest.fixture
def env(_tmp_private, monkeypatch):
    _tmp_private.mkdir(parents=True, exist_ok=True)
    (_tmp_private / "config.yaml").write_text("emulator:\n  address: 127.0.0.1:5555\n  adb: adb\nserver: Official\n",
                                             encoding="utf-8")
    net, adb = FakeNet(), FakeAdb()
    monkeypatch.setattr(game, "http_open", net)
    monkeypatch.setattr(game, "adb_run", adb)
    monkeypatch.setattr(game, "disk_free", lambda p: 100 << 30)
    return net, adb


# --- 纯函数 ----------------------------------------------------------------------------------

def test_version_from_url():
    assert game.version_from_url(APK_URL) == "2.7.81"
    assert game.version_from_url("https://ak.hycdn.cn/a/arknights-hg-2701.apk") == "2.7.1"
    assert game.version_from_url("https://ak.hycdn.cn/a/arknights-hg-31012.apk") == "3.1.12"
    assert game.version_from_url("https://ak.hycdn.cn/a/arknights.apk") is None
    assert game.version_from_url("https://ak.hycdn.cn/a/arknights-hg-27.apk") is None
    assert game.version_from_url("") is None


def test_newer_compares_numerically():
    assert game.newer("2.7.81", "2.7.71") is True
    assert game.newer("2.7.71", "2.7.71") is False
    assert game.newer("2.10.1", "2.9.99") is True
    assert game.newer("2.7", "2.7.0") is False
    assert game.newer(None, "2.7.71") is None


def test_parse_dumpsys():
    assert game.parse_dumpsys(DUMPSYS) == {"version": "2.7.71", "code": 2771, "updated_at": "2026-09-01 12:00:00"}
    assert game.parse_dumpsys("Unable to find package: x") is None


def test_allowed_host_and_size():
    assert game.allowed_host(APK_URL)
    assert game.allowed_host("https://ak.hypergryph.com/x.apk")
    assert not game.allowed_host("https://evilhycdn.cn/x.apk")
    assert not game.allowed_host("http://ak.hycdn.cn/x.apk")
    assert game.total_size(206, {"Content-Range": "bytes 0-0/1861585573"}) == 1861585573
    assert game.total_size(200, {"Content-Length": "42"}) == 42
    assert game.total_size(206, {}) is None


def test_accept_terms_config():
    assert config.accept_terms({"game": {"accept_terms": True}}) is True
    assert config.accept_terms({"game": {"accept_terms": False}}) is False
    assert config.accept_terms({"game": {"accept_terms": "yes"}}) is None
    assert config.accept_terms({}) is None
    assert config.DEFAULTS["game"]["accept_terms"] is None


# --- version ---------------------------------------------------------------------------------

def test_version(env, capsys):
    net, adb = env
    code, out = run(capsys, "game", "version")
    assert code == 0, out
    d = out["data"]
    assert d["installed"] == {"version": "2.7.71", "code": 2771, "updated_at": "2026-09-01 12:00:00"}
    assert d["latest"] == {"version": "2.7.81", "url": APK_URL, "size": len(BODY)}
    assert d["update_needed"] is True and d["accept_terms"] is None
    assert net.calls == [(game.LATEST_URL, 0, 0)]
    assert adb.calls[0] == ["adb", "-s", "127.0.0.1:5555", "shell", "dumpsys", "package", "com.hypergryph.arknights"]
    assert not (paths.PRIVATE / "lock.json").exists()


def test_version_not_installed_and_up_to_date(env, capsys, monkeypatch):
    monkeypatch.setattr(game, "adb_run", FakeAdb(versions=(None,)))
    code, out = run(capsys, "game", "version")
    assert code == 0 and out["data"]["installed"] is None and out["data"]["update_needed"] is True
    monkeypatch.setattr(game, "adb_run", FakeAdb(versions=("2.7.81",)))
    code, out = run(capsys, "game", "version")
    assert out["data"]["update_needed"] is False


def test_version_errors(env, capsys, monkeypatch, _tmp_private):
    def boom(*a, **k):
        raise OSError("no route")
    monkeypatch.setattr(game, "http_open", boom)
    code, out = run(capsys, "game", "version")
    assert code == 1 and out["error"]["code"] == "network"

    monkeypatch.setattr(game, "http_open", FakeNet(url="https://example.com/arknights-hg-2781.apk"))
    code, out = run(capsys, "game", "version")
    assert code == 1 and out["error"]["code"] == "unexpected_download"

    monkeypatch.setattr(game, "adb_run", lambda argv, timeout=30: (1, "error: device offline"))
    code, out = run(capsys, "game", "version")
    assert code == 5 and out["error"]["code"] == "adb_failed"

    (_tmp_private / "config.yaml").write_text("emulator:\n  address: x\nserver: YoStarEN\n", encoding="utf-8")
    code, out = run(capsys, "game", "version")
    assert code == 2 and out["error"]["code"] == "unsupported_server"


# --- update ----------------------------------------------------------------------------------

def test_update_up_to_date(env, capsys, monkeypatch):
    monkeypatch.setattr(game, "adb_run", FakeAdb(versions=("2.7.81",)))
    code, out = run(capsys, "game", "update")
    assert code == 0 and out["data"]["updated"] is False


def test_update_full(env, capsys, monkeypatch):
    net, _ = env
    adb = FakeAdb(versions=("2.7.71", "2.7.81"))
    monkeypatch.setattr(game, "adb_run", adb)
    code, out = run(capsys, "game", "update")
    assert code == 0, out
    d = out["data"]
    assert d["updated"] is True and d["from"] == "2.7.71" and d["to"] == "2.7.81" and d["apk_bytes"] == len(BODY)
    stop = next(i for i, a in enumerate(adb.calls) if "force-stop" in a)
    assert next(i for i, a in enumerate(adb.calls) if "install" in a) == stop + 1
    install = [a for a in adb.calls if "install" in a][0]
    assert install[-2:] == ["-r", str(game.cache_dir() / "arknights-hg-2781.apk")]
    assert list(game.cache_dir().iterdir()) == []          # 装完删掉安装包
    assert not (paths.PRIVATE / "lock.json").exists() or json.loads((paths.PRIVATE / "lock.json").read_text()) is None


def test_update_resumes_partial_download(env, capsys, monkeypatch):
    net = FakeNet(fail_after=3000)
    monkeypatch.setattr(game, "http_open", net)
    code, out = run(capsys, "game", "update", "--download-only")
    assert code == 1 and out["error"]["code"] == "network"
    part = game.cache_dir() / "arknights-hg-2781.apk.part"
    assert part.stat().st_size == 3000

    net2 = FakeNet()
    monkeypatch.setattr(game, "http_open", net2)
    code, out = run(capsys, "game", "update", "--download-only")
    assert code == 0, out
    assert net2.calls[-1] == (APK_URL, 3000, None)
    apk = game.cache_dir() / "arknights-hg-2781.apk"
    assert apk.read_bytes() == BODY and not part.exists()
    assert out["data"]["updated"] is False and out["data"]["apk_bytes"] == len(BODY)


def test_update_restarts_when_no_range_support(env, capsys, monkeypatch):
    d = game.cache_dir()
    d.mkdir(parents=True)
    (d / "arknights-hg-2781.apk.part").write_bytes(b"garbage")
    monkeypatch.setattr(game, "http_open", FakeNet(ranges=False))
    code, out = run(capsys, "game", "update", "--download-only")
    assert code == 0, out
    assert (d / "arknights-hg-2781.apk").read_bytes() == BODY


def test_update_rejects_non_zip(env, capsys, monkeypatch):
    monkeypatch.setattr(game, "http_open", FakeNet(body=b"<html>" + b"x" * 100))
    code, out = run(capsys, "game", "update", "--download-only")
    assert code == 1 and out["error"]["code"] == "bad_apk"
    assert list(game.cache_dir().iterdir()) == []


def test_update_disk_full(env, capsys, monkeypatch):
    monkeypatch.setattr(game, "disk_free", lambda p: 10)
    code, out = run(capsys, "game", "update")
    assert code == 3 and out["error"]["code"] == "disk_full"
    assert out["data"]["free_bytes"] == 10 and out["data"]["need_bytes"] == len(BODY) + game.DISK_MARGIN


def test_update_install_failed_keeps_apk(env, capsys, monkeypatch):
    adb = FakeAdb(install_rc=1, install_out="adb: failed to install x: Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]")
    monkeypatch.setattr(game, "adb_run", adb)
    code, out = run(capsys, "game", "update")
    assert code == 1 and out["error"]["code"] == "install_failed"
    assert out["data"]["reason"] == "INSTALL_FAILED_INSUFFICIENT_STORAGE"
    assert "存储空间" in out["error"]["hint"]
    assert (game.cache_dir() / "arknights-hg-2781.apk").exists()


def test_update_verify_failed(env, capsys):
    code, out = run(capsys, "game", "update")              # 假 adb 装完还读到 2.7.71
    assert code == 1 and out["error"]["code"] == "verify_failed"


def test_update_keep_and_busy(env, capsys, monkeypatch):
    from maapilot.crew import lock
    held = lock.acquire("test")
    try:
        code, out = run(capsys, "game", "update")
        assert code == 4 and out["error"]["code"] == "busy"
    finally:
        held.release()
    monkeypatch.setattr(game, "adb_run", FakeAdb(versions=("2.7.71", "2.7.81")))
    code, out = run(capsys, "game", "update", "--keep")    # 刚才已经下好：直接用
    assert code == 0, out
    assert (game.cache_dir() / "arknights-hg-2781.apk").exists()
