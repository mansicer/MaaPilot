"""maa-cli 在各系统上的对接：缓存目录、profile 的 adb_path、本地作业的 file:// URI。"""
import subprocess
import sys
from pathlib import Path

import pytest

from maapilot.crew import maa
from maapilot.knowledge import maa_activity
from maapilot.tools import maa_cmd


@pytest.fixture
def no_cache(monkeypatch):
    maa_activity._cache_dir.cache_clear()
    monkeypatch.delenv(maa_activity.ACTIVITY_FILE_ENV, raising=False)
    monkeypatch.delenv(maa_activity.TASKS_FILE_ENV, raising=False)
    yield
    maa_activity._cache_dir.cache_clear()


def test_cache_dir_asks_maa_cli(monkeypatch, tmp_path, no_cache):
    calls = []
    monkeypatch.setattr(maa_activity.shutil, "which", lambda name: "/opt/maa")

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=f"warn: something\n{tmp_path / 'c'}\n", stderr="")
    monkeypatch.setattr(maa_activity.subprocess, "run", run)
    assert maa_activity.activity_file() == tmp_path / "c" / "StageActivityV2.json"
    assert maa_activity.tasks_file() == tmp_path / "c" / "resource" / "tasks" / "tasks.json"
    assert calls == [["/opt/maa", "dir", "cache"]]           # 只问一次


@pytest.mark.parametrize("how", ["missing", "fails", "raises"])
def test_cache_dir_falls_back_to_guess(monkeypatch, no_cache, how):
    monkeypatch.setattr(maa_activity.shutil, "which", lambda name: None if how == "missing" else "/opt/maa")

    def run(argv, **kw):
        if how == "raises":
            raise subprocess.TimeoutExpired(argv, 15)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")
    monkeypatch.setattr(maa_activity.subprocess, "run", run)
    assert maa_activity._cache_dir() == maa_activity._guess_cache_dir()


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_guess_cache_dir_per_os(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(maa_activity.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    want = {"darwin": tmp_path / "home" / "Library" / "Caches" / "com.loong.maa",
            "win32": tmp_path / "local" / "loong" / "maa" / "cache",
            "linux": tmp_path / "home" / ".cache" / "maa"}[platform]
    assert maa_activity._guess_cache_dir() == want


def _cfg(private: Path, adb: str):
    private.mkdir(parents=True, exist_ok=True)
    (private / "config.yaml").write_text(f"emulator:\n  address: '127.0.0.1:16384'\n  adb: '{adb}'\n", encoding="utf-8")


def _adb_path(profile: Path):
    import tomllib
    return tomllib.loads(profile.read_text(encoding="utf-8"))["connection"].get("adb_path")


def test_install_config_writes_adb_path(_tmp_private, tmp_path):
    adb = tmp_path / "MuMu Player" / "shell" / ("adb.exe" if sys.platform == "win32" else "adb")
    adb.parent.mkdir(parents=True)
    adb.write_text("")
    adb.chmod(0o755)
    _cfg(_tmp_private, str(adb))
    assert maa.install_config() == list(maa.CONFIG_FILES)
    prof = maa.config_dir() / "profiles" / "default.toml"
    assert _adb_path(prof) == str(adb)
    text = prof.read_text(encoding="utf-8")
    assert 'config = "General"' in text and "touch_mode" in text      # 模板的其余内容不动
    assert maa.sync_adb_path() is False                               # 已经对上了：不再写
    # 换了 adb：已有的 profile 也跟着改（不重复写一行）
    other = tmp_path / "other" / adb.name
    other.parent.mkdir()
    other.write_text("")
    other.chmod(0o755)
    _cfg(_tmp_private, str(other))
    assert maa.install_config() == []
    assert _adb_path(prof) == str(other)
    assert prof.read_text(encoding="utf-8").count("adb_path") == 1


def test_adb_path_windows_backslashes_are_valid_toml(_tmp_private, monkeypatch):
    _cfg(_tmp_private, "adb")
    monkeypatch.setattr(maa, "_resolved_adb", lambda: r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe")
    maa.install_config()
    assert _adb_path(maa.config_dir() / "profiles" / "default.toml") == r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe"


def test_adb_not_found_leaves_profile_alone(_tmp_private, monkeypatch):
    _cfg(_tmp_private, "no-such-adb-xyz")
    maa.install_config()
    prof = maa.config_dir() / "profiles" / "default.toml"
    assert _adb_path(prof) is None
    assert prof.read_bytes() == (maa.paths.TEMPLATES / "maa-config" / "profiles" / "default.toml").read_bytes()


def test_copilot_file_uri_roundtrip(tmp_path):
    job = tmp_path / "作业 1.json"
    job.write_text("{}", encoding="utf-8")
    for uri in (job.as_uri(), str(job)):
        argv, info = maa_cmd._copilot_argv({"uri": uri})
        assert argv == ["copilot", str(job.resolve())]


@pytest.mark.parametrize("uri, want", [
    ("file:///C:/Users/a/x.json", "C:/Users/a/x.json"),
    ("file:///Users/a/%E4%BD%9C%E4%B8%9A.json", "/Users/a/作业.json"),
    ("file://localhost/tmp/x.json", "/tmp/x.json"),
    ("file://server/share/x.json", "//server/share/x.json"),
])
def test_local_path_parses_file_uris(uri, want):
    assert maa_cmd._local_path(uri) == Path(want)
