"""项目 .tools/ 里的 maa-cli 和 adb：下载地址、解压安装、找程序的顺序、tools 命令（不联网：下载换成造好的压缩包）。"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile

import pytest

from maapilot import checksum, cli, config, paths, toolchain
from maapilot.cliutil import CliError
from tests.portable import WINDOWS


@pytest.fixture
def tools(tmp_path, monkeypatch):           # conftest 已经把 .tools 指到不存在的目录；这里换成能装东西的
    monkeypatch.setattr(toolchain, "tools_dir", lambda: tmp_path / ".tools")
    # 造的压缩包当然和真包的哈希不一样：公布的哈希换成 _fetcher 最近一次造的包的（核对本身照样跑）
    monkeypatch.setattr(toolchain, "expected_checksum", lambda name, version, url: (_published["sha256"], None))
    return tmp_path / ".tools"


_published = {"sha256": ""}


def _tar(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o755 << 16
            z.writestr(info, data)
    return buf.getvalue()


def _fetcher(payload: bytes, seen: list):
    _published["sha256"] = hashlib.sha256(payload).hexdigest()

    def fetch(url, dest):
        seen.append(url)
        dest.write_bytes(payload)
    return fetch


def test_urls(monkeypatch):
    monkeypatch.setattr(toolchain.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(toolchain, "MACOS", True)
    monkeypatch.setattr(toolchain, "WINDOWS", False)
    assert toolchain.maa_cli_url("0.7.7").endswith("/v0.7.7/maa_cli-v0.7.7-aarch64-apple-darwin.tar.gz")
    assert toolchain.adb_url().endswith(f"platform-tools_r{toolchain.ADB_VERSION}-darwin.zip")
    monkeypatch.setattr(toolchain.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(toolchain, "MACOS", False)
    monkeypatch.setattr(toolchain, "WINDOWS", True)
    assert toolchain.maa_cli_url("v0.7.7").endswith("/maa_cli-v0.7.7-x86_64-pc-windows-msvc.zip")
    assert toolchain.adb_url().endswith(f"platform-tools_r{toolchain.ADB_VERSION}-win.zip")
    monkeypatch.setattr(toolchain.platform, "machine", lambda: "riscv64")
    with pytest.raises(CliError):
        toolchain.triple()


def test_install_maa_cli_default_tested_version(tools):
    seen = []
    exe = toolchain.exe_name("maa")
    payload = _zip({exe: b"x", "licenses.md": b""}) if WINDOWS else _tar({exe: b"x", "licenses.md": b""})
    r = toolchain.install("maa", fetch=_fetcher(payload, seen))
    assert r["version"] == toolchain.MAA_CLI_TESTED[1] and f"/v{toolchain.MAA_CLI_TESTED[1]}/" in seen[0]
    assert toolchain.managed("maa") == tools / "maa-cli" / exe and toolchain.installed_version("maa") == r["version"]
    assert r["replaced"] is None
    r = toolchain.install("maa", "0.7.6", fetch=_fetcher(payload, seen))             # 换版本：旧的整个换掉
    assert r["replaced"] == toolchain.MAA_CLI_TESTED[1] and toolchain.installed_version("maa") == "0.7.6"
    assert sorted(p.name for p in tools.iterdir()) == ["maa-cli"]                   # 临时目录、旧目录都清掉了


def test_install_adb_keeps_whole_platform_tools(tools):
    exe = toolchain.exe_name("adb")
    payload = _zip({f"platform-tools/{exe}": b"x", "platform-tools/lib64/libc++.so": b"y"})
    r = toolchain.install("adb", fetch=_fetcher(payload, []))
    assert r["version"] == toolchain.ADB_VERSION and toolchain.managed("adb") == tools / "platform-tools" / exe
    assert (tools / "platform-tools" / "lib64" / "libc++.so").exists()
    if not WINDOWS:
        assert (tools / "platform-tools" / exe).stat().st_mode & 0o111


def test_install_rejects_package_without_program(tools):
    with pytest.raises(CliError) as e:
        toolchain.install("adb", fetch=_fetcher(_zip({"readme.txt": b""}), []))
    assert e.value.code == "install_failed" and not (tools / "platform-tools").exists()


def test_resolve_order(tools):
    assert config.tool("maa", {"tools": {"maa": "maa"}}) == "maa"                   # 没装：按名字找
    assert config.adb_path({}) == "adb"
    exe = toolchain.exe_name("maa")
    toolchain.install("maa", fetch=_fetcher(_zip({exe: b"x"}) if WINDOWS else _tar({exe: b"x"}), []))
    managed = str(tools / "maa-cli" / exe)
    assert config.tool("maa", {"tools": {"maa": "maa"}}) == managed                 # 写的是名字：用装好的
    assert config.tool("maa", {}) == managed
    assert config.tool("maa", {"tools": {"maa": "/opt/x/maa"}}) == "/opt/x/maa"     # 写了路径：照它
    assert config.tool("claude", {"tools": {"claude": "claude"}}) == "claude"       # 别的程序不管


def test_tools_status_and_note(tools, capsys, monkeypatch):
    exe = toolchain.exe_name("maa")
    toolchain.install("maa", fetch=_fetcher(_zip({exe: b"x"}) if WINDOWS else _tar({exe: b"x"}), []))
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\ntools: {maa: /opt/homebrew/bin/maa}\n", encoding="utf-8")
    assert cli.main(["tools", "status"]) == 0
    d = json.loads(capsys.readouterr().out)["data"]
    assert d["maa-cli"]["installed"] and d["maa-cli"]["using"] == "/opt/homebrew/bin/maa" and "tools.maa" in d["maa-cli"]["note"]
    assert d["adb"]["installed"] is None and d["maa_cli_tested"] == list(toolchain.MAA_CLI_TESTED)


# --- 下载的包和公布的哈希核对 ---------------------------------------------------------------------------------------

def _maa_payload() -> bytes:
    exe = toolchain.exe_name("maa")
    return _zip({exe: b"x"}) if WINDOWS else _tar({exe: b"x"})


def test_tested_maa_cli_version_has_checksums_for_every_package():
    table = toolchain.MAA_CLI_SHA256[toolchain.MAA_CLI_TESTED[1]]            # 改实测版本的上限时要一起加
    assert set(table) == {f"{a}-{o}" for a in ("aarch64", "x86_64")
                          for o in ("apple-darwin", "pc-windows-msvc", "unknown-linux-gnu")}
    assert all(checksum.SHA256_RE.match(h) for h in table.values())
    assert set(toolchain.ADB_SHA256) == {"darwin", "linux", "win"}
    assert all(checksum.SHA256_RE.match(h) and size > 0 for h, size in toolchain.ADB_SHA256.values())


def test_expected_checksum_from_table_needs_no_network(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("表里有的版本不该联网查")
    monkeypatch.setattr(checksum, "release_asset_checksum", no_network)
    v = toolchain.MAA_CLI_TESTED[1]
    h, size = toolchain.expected_checksum("maa", v, toolchain.maa_cli_url(v))
    assert h == toolchain.MAA_CLI_SHA256[v][toolchain.triple()] and size is None
    assert toolchain.expected_checksum("adb", toolchain.ADB_VERSION, toolchain.adb_url()) == toolchain.ADB_SHA256[toolchain.adb_os()]


def test_expected_checksum_other_version_asks_github(monkeypatch):
    asked = []

    def lookup(repo, tag, name):
        asked.append((repo, tag, name))
        return ("ab" * 32, 123)
    monkeypatch.setattr(checksum, "release_asset_checksum", lookup)
    url = toolchain.maa_cli_url("0.7.6")
    assert toolchain.expected_checksum("maa", "0.7.6", url) == ("ab" * 32, 123)
    assert asked == [(toolchain.MAA_CLI_REPO, "v0.7.6", url.rsplit("/", 1)[-1])]


@pytest.mark.parametrize("lookup", [lambda *a: None, lambda *a: (_ for _ in ()).throw(OSError("rate limited"))],
                         ids=["not_published", "api_unreachable"])
def test_no_checksum_means_no_install(tmp_path, monkeypatch, lookup):
    monkeypatch.setattr(toolchain, "tools_dir", lambda: tmp_path / ".tools")
    monkeypatch.setattr(checksum, "release_asset_checksum", lookup)
    seen = []
    with pytest.raises(CliError) as e:
        toolchain.install("maa", "0.7.6", fetch=_fetcher(_maa_payload(), seen))
    assert e.value.code == "no_checksum" and seen == []                       # 没下载
    assert not (tmp_path / ".tools" / "maa-cli").exists()


def test_checksum_mismatch_installs_nothing_and_keeps_old(tools):
    good = _maa_payload()
    toolchain.install("maa", fetch=_fetcher(good, []))
    old = (tools / "maa-cli" / toolchain.VERSION_FILE).read_text(encoding="utf-8")

    def tampered(url, dest):                                                  # 公布的是 good 的哈希，下到的是别的
        dest.write_bytes(good + b"extra")
    with pytest.raises(CliError) as e:
        toolchain.install("maa", "0.7.6", fetch=tampered)
    assert e.value.code == "checksum_mismatch"
    assert (tools / "maa-cli" / toolchain.VERSION_FILE).read_text(encoding="utf-8") == old
    assert sorted(p.name for p in tools.iterdir()) == ["maa-cli"]             # 下到的包和临时目录都删了


def test_adb_size_is_checked_too(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "tools_dir", lambda: tmp_path / ".tools")
    payload = _zip({f"platform-tools/{toolchain.exe_name('adb')}": b"x"})
    monkeypatch.setattr(toolchain, "ADB_SHA256", {toolchain.adb_os(): (hashlib.sha256(payload).hexdigest(), len(payload) + 1)})
    with pytest.raises(CliError) as e:
        toolchain.install("adb", fetch=lambda url, dest: dest.write_bytes(payload))
    assert e.value.code == "checksum_mismatch" and "大小" in e.value.message
    monkeypatch.setattr(toolchain, "ADB_SHA256", {toolchain.adb_os(): (hashlib.sha256(payload).hexdigest(), len(payload))})
    assert toolchain.install("adb", fetch=lambda url, dest: dest.write_bytes(payload))["version"] == toolchain.ADB_VERSION
