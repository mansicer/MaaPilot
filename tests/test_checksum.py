"""下载核对（maapilot/checksum.py）：哈希的算法、GitHub 发布信息里的 digest、单独装的 MaaCore、游戏数据表。不联网。"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile

import pytest

from maapilot import checksum, paths, toolchain
from maapilot.crew import maacore
from maapilot.knowledge import datasync


def test_git_blob_sha_matches_git(tmp_path):
    data = b'{"a": 1}\n'
    f = tmp_path / "x.json"
    f.write_bytes(data)
    try:
        want = subprocess.run(["git", "hash-object", str(f)], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("这台机器没有 git")
    assert checksum.git_blob_sha(data) == want
    assert checksum.git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"      # git 的空文件


def test_parse_digest():
    h = "ab" * 32
    assert checksum.parse_digest(f"sha256:{h}") == h and checksum.parse_digest(f"SHA256:{h.upper()}") == h
    for bad in (None, "", h, f"sha1:{h[:40]}", "sha256:xyz", f"sha256:{h}00", 123):
        assert checksum.parse_digest(bad) is None


def test_check_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    h = hashlib.sha256(b"hello").hexdigest()
    checksum.check_file(f, h)
    checksum.check_file(f, h.upper(), 5)
    with pytest.raises(checksum.Mismatch, match="大小"):
        checksum.check_file(f, h, 6)
    with pytest.raises(checksum.Mismatch, match="sha256"):
        checksum.check_file(f, "0" * 64, 5)


def test_release_asset_checksum():
    h = "cd" * 32
    rel = {"assets": [{"name": "a.zip", "digest": f"sha256:{h}", "size": 10}, {"name": "old.zip", "digest": None, "size": 3}]}
    urls = []

    def get(url):
        urls.append(url)
        return rel
    assert checksum.release_asset_checksum("o/r", "v1", "a.zip", get=get) == (h, 10)
    assert urls == ["https://api.github.com/repos/o/r/releases/tags/v1"]
    assert checksum.release_asset_checksum("o/r", "v1", "old.zip", get=get) is None    # 老发布没有 digest
    assert checksum.release_asset_checksum("o/r", "v1", "nope.zip", get=get) is None
    assert checksum.release_asset_checksum("o/r", "v1", "a.zip", get=lambda url: {"message": "Not Found"}) is None


# --- 单独装的 MaaCore ------------------------------------------------------------------------------------------------

def _core_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(maacore.LIB_NAMES[0], b"lib")
    return buf.getvalue()


@pytest.fixture
def core(tmp_path, monkeypatch):
    """假的 MAA 发布：一个 zip（这台机器的包名），发布信息可改。→ (发布信息里的 asset, 下载到的内容可改的 dict)"""
    payload = _core_zip()
    monkeypatch.setattr(maacore, "root", lambda: tmp_path / "maa-core")
    monkeypatch.setattr(maacore, "cli_core", lambda: None)
    monkeypatch.setattr(maacore, "_asset_pattern", lambda: __import__("re").compile(r"^core\.zip$"))
    monkeypatch.setattr(maacore.shutil, "which", lambda name: None)                    # 用 zipfile 解，不靠系统的 unzip
    asset = {"name": "core.zip", "browser_download_url": "https://example.invalid/core.zip",
             "digest": "sha256:" + hashlib.sha256(payload).hexdigest(), "size": len(payload)}
    served = {"bytes": payload}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(maacore.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(json.dumps({"tag_name": "v9.9.9", "assets": [asset]}).encode()))
    monkeypatch.setattr(toolchain, "download", lambda url, dest: dest.write_bytes(served["bytes"]))
    return asset, served


def test_maacore_install_verified(core, tmp_path):
    r = maacore.install()
    assert r["installed"] and r["version"] == "v9.9.9"
    assert (tmp_path / "maa-core" / "v9.9.9" / maacore.LIB_NAMES[0]).read_bytes() == b"lib"


def test_maacore_install_rejects_wrong_bytes(core, tmp_path):
    _asset, served = core
    served["bytes"] = served["bytes"] + b"x"
    with pytest.raises(checksum.Mismatch):
        maacore.install()
    assert not (tmp_path / "maa-core" / "v9.9.9").exists()
    assert list((tmp_path / "maa-core").iterdir()) == []                               # 下到的包也删了


def test_maacore_install_refuses_release_without_digest(core, tmp_path):
    asset, _served = core
    asset["digest"] = None
    with pytest.raises(RuntimeError, match="sha256"):
        maacore.install()
    assert not (tmp_path / "maa-core").exists()                                        # 没下载


# --- 游戏数据表 ------------------------------------------------------------------------------------------------------

def test_fetch_verified_uses_raw_when_it_matches():
    data = b'{"x": 1}'

    def no_blob(sha):
        raise AssertionError("对得上就不该再取 blob")
    assert datasync.fetch_verified("t.json", checksum.git_blob_sha(data),
                                   get=lambda url, timeout=60: data, get_blob=no_blob) == data


def test_fetch_verified_falls_back_to_blob_when_raw_is_stale():
    new, stale = b'{"x": 2}', b'{"x": 1}'
    sha = checksum.git_blob_sha(new)
    asked = []

    def blob(s):
        asked.append(s)
        return new
    assert datasync.fetch_verified("t.json", sha, get=lambda url, timeout=60: stale, get_blob=blob) == new
    assert asked == [sha]


@pytest.mark.parametrize("blob", [lambda s: b"still wrong", lambda s: (_ for _ in ()).throw(OSError("403 rate limit"))],
                         ids=["blob_wrong", "blob_unreachable"])
def test_fetch_verified_mismatch(blob):
    with pytest.raises(checksum.Mismatch):
        datasync.fetch_verified("t.json", checksum.git_blob_sha(b"new"), get=lambda url, timeout=60: b"old", get_blob=blob)


def test_sync_keeps_old_table_when_content_does_not_match(monkeypatch, capsys):
    monkeypatch.setattr(paths, "DATA", paths.PRIVATE / "data")
    paths.DATA.mkdir(parents=True)
    (paths.DATA / "stage_table.json").write_text('{"old": true}', encoding="utf-8")
    monkeypatch.setattr(datasync, "fetch_remote_shas", lambda files=None: {"stage_table.json": checksum.git_blob_sha(b"new")})
    monkeypatch.setattr(datasync, "missing_tables", lambda: [])
    monkeypatch.setattr(datasync, "_get", lambda url, timeout=60: b"something else")
    monkeypatch.setattr(datasync, "_get_blob", lambda sha: b"also something else")
    monkeypatch.setattr(datasync, "_rebuild_recipes_if_stale", lambda updated: False)
    r = datasync.sync()
    assert r["unverified"] == ["stage_table.json"] and r["updated"] == []
    assert json.loads((paths.DATA / "stage_table.json").read_text(encoding="utf-8")) == {"old": True}
    assert "stage_table.json" not in datasync.read_manifest()                          # 没记进去：下次还会再下
    assert "stage_table.json" in capsys.readouterr().err
