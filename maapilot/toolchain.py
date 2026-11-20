"""MaaPilot 自己下载、放在项目 `.tools/` 里的第三方程序：maa-cli 本体（`.tools/maa-cli/`）和 adb（`.tools/platform-tools/`）。
不用 Homebrew / winget：三个系统一样从官方发布下载；maa-cli 默认装实测过的版本（MAA_CLI_TESTED 的上限）。

找程序（config.tool / config.adb_path）：config.yaml 里写的是路径（带目录）→ 就用它；写的是程序名（`maa`、`adb`，模板的默认）→
`.tools/` 里装了就用装好的，没装再按名字在 PATH 上找。所以用 Homebrew 等装好、config 写了绝对路径的照样能用。
MaaCore 和资源由 maa-cli 自己下载，放在 `.tools/maa-data/`（`crew/maa.py` 的 `data_dir`）。
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from maapilot import checksum, paths
from maapilot.cliutil import ENVIRONMENT, FAILED, CliError
from maapilot.oscompat import MACOS, WINDOWS

# 实测过的 maa-cli 版本（发版前把本机升到最新、跑通日常和作业后更新上限）：doctor 低于下限不通过、高于上限提醒；
# tools install 默认装上限这个版本
MAA_CLI_TESTED = ("0.7.5", "0.7.7")
MAA_CLI_URL = ("https://github.com/MaaAssistantArknights/maa-cli/releases/download/"
               "v{v}/maa_cli-v{v}-{triple}.{ext}")
MAA_CLI_REPO = "MaaAssistantArknights/maa-cli"
# 发布包的 sha256（GitHub 发布信息里每个文件的 digest）：{版本: {三元组: sha256}}。装表里的版本不用联网查哈希；
# 改 MAA_CLI_TESTED 的上限时把新版本的加进来（tests/test_toolchain.py 会查）。别的版本装的时候去发布信息里查。
MAA_CLI_SHA256 = {
    "0.7.7": {
        "aarch64-apple-darwin": "b12ddd6deda6f811b9dd6c73b2341cbfaf6e31c481b4bfc550ce330580b007af",
        "x86_64-apple-darwin": "3739d14b02741d9c2d8609d04f609109c8bb59533fabccc1ac54466fca5809f9",
        "aarch64-pc-windows-msvc": "63ba62bc0a08b6b3abddae7e214ad10e12b63699f3d776bceca579b615d7db45",
        "x86_64-pc-windows-msvc": "769719108609b48e18d7ebd88855f6125bd455d3b32d051866dafb8e896254c1",
        "aarch64-unknown-linux-gnu": "dc38518a1b6111bb0b7a8eea63578dacaebc89651bbbc2f7d568c3484e16fb56",
        "x86_64-unknown-linux-gnu": "b582dd79c3f9b42c3a9b4fb6bcf76cc25bfc96600e83028881c97b0c621876a1",
    },
}
# adb 装固定的版本（不跟 Google 的 latest），哈希才对得上。换版本：改 ADB_VERSION，三个包各下一次、
# 和 https://dl.google.com/android/repository/repository2-3.xml 里 platform-tools 的 sha1、大小对过，再把 sha256 写到这里
ADB_VERSION = "37.0.3"
ADB_URL = "https://dl.google.com/android/repository/platform-tools_r{v}-{os}.zip"
ADB_SHA256 = {                                         # {包名里的系统: (sha256, 字节数)}
    "darwin": ("df21339b9371fef9b19264404338dc686f59a88a1dce634b963da344909001d6", 16597626),
    "linux": ("214548146cadb2ca3508143d8a1d4be4c59de5bb837253e3ede89ed74cf450d1", 9571024),
    "win": ("89d108360a3bf2924d3bea837239edaccc22fd6ff5befd3787c4348a9fe4f09d", 8188601),
}
DOWNLOAD_TIMEOUT_S = 60
VERSION_FILE = "VERSION"

# 名字 → .tools 下的目录
DIRS = {"maa": "maa-cli", "adb": "platform-tools"}


def version_tuple(v: str) -> tuple[int, ...]:
    """"0.7.7" / "v0.8.0-nightly.1+sha.x" → (0, 7, 7) / (0, 8, 0)。"""
    m = re.match(r"v?(\d+(?:\.\d+)*)", str(v).strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def tools_dir() -> Path:
    return paths.ROOT / ".tools"


def exe_name(name: str) -> str:
    return f"{name}.exe" if WINDOWS else name


def managed(name: str) -> Path | None:
    """.tools 里装好的 maa / adb；没装 → None。"""
    p = tools_dir() / DIRS[name] / exe_name(name)
    return p if p.is_file() else None


def resolve(configured: str | None, name: str) -> str:
    """config.yaml 里写的（可能没写）→ 实际要执行的程序：写了路径就用它；写的是程序名（或没写）→ .tools 里的，没有再用名字。"""
    c = str(configured or name)
    if os.sep in c or "/" in c or (WINDOWS and "\\" in c):
        return c
    m = managed(name) if name in DIRS and c.lower().removesuffix(".exe") == name else None
    return str(m) if m else c


def installed_version(name: str) -> str | None:
    try:
        return (tools_dir() / DIRS[name] / VERSION_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def triple() -> str:
    """这台电脑对应的 maa-cli 发布包（Rust 的目标三元组）。认不出 → CliError。"""
    m = platform.machine().lower()
    arch = "aarch64" if m in ("arm64", "aarch64") else "x86_64" if m in ("x86_64", "amd64", "x64") else None
    osname = "apple-darwin" if MACOS else "pc-windows-msvc" if WINDOWS else \
        "unknown-linux-gnu" if sys.platform.startswith("linux") else None
    if not arch or not osname:
        raise CliError("unsupported_platform", f"maa-cli 没有这台电脑（{sys.platform} {m}）的发布包", exit=ENVIRONMENT)
    return f"{arch}-{osname}"


def maa_cli_url(version: str) -> str:
    return MAA_CLI_URL.format(v=version.removeprefix("v"), triple=triple(), ext="zip" if WINDOWS else "tar.gz")


def adb_os() -> str:
    return "win" if WINDOWS else "darwin" if MACOS else "linux"


def adb_url() -> str:
    return ADB_URL.format(v=ADB_VERSION, os=adb_os())


def expected_checksum(name: str, version: str, url: str) -> tuple[str, int | None]:
    """要下的这个包公布的 (sha256, 字节数或 None)。maa-cli 表里的版本、adb：写在上面；maa-cli 别的版本：查 GitHub 发布信息。
    查不到 → CliError(no_checksum)：没有哈希可对的程序不装。"""
    if name == "adb":
        return ADB_SHA256[adb_os()]
    known = MAA_CLI_SHA256.get(version, {}).get(triple())
    if known:
        return known, None
    asset = url.rsplit("/", 1)[-1]
    try:
        found = checksum.release_asset_checksum(MAA_CLI_REPO, f"v{version}", asset)
    except (OSError, ValueError) as e:
        raise CliError("no_checksum", f"查不到 maa-cli {version} 的发布信息，没法核对下载的包（{e}）", exit=FAILED,
                       hint=f"网络能上 api.github.com 吗（没登录每小时只能查 60 次）？过一会儿再试，"
                            f"或者装实测过的 {MAA_CLI_TESTED[1]}（不用查）") from None
    if found is None:
        raise CliError("no_checksum", f"maa-cli {version} 的发布里没有 {asset}，或者没给它的 sha256，没法核对", exit=FAILED,
                       hint=f"版本号写对了吗？实测过的是 {MAA_CLI_TESTED[1]}")
    return found


def download(url: str, dest: Path) -> None:
    """下载到 dest（进度写 stderr）。网络不通 → CliError(network)。走系统的代理环境变量（HTTPS_PROXY 等）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "maapilot"})
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got, shown = 0, -1
            while chunk := r.read(1 << 16):
                f.write(chunk)
                got += len(chunk)
                pct = got * 10 // total if total else -1
                if pct != shown and pct >= 0:
                    shown = pct
                    print(f"下载 {dest.name}：{got >> 20}/{total >> 20} MB", file=sys.stderr, flush=True)
    except OSError as e:                               # URLError、超时、断线都是 OSError
        raise CliError("network", f"下载失败：{url}（{e}）", exit=FAILED,
                       hint="网络能上 GitHub / Google 吗？要代理就设 HTTPS_PROXY 再试") from None


def _extract(archive: Path, into: Path) -> None:
    """解压 zip / tar.gz 到 into；zip 里记的可执行权限照样设上（zipfile 自己不管）。"""
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                out = Path(z.extract(info, into))
                mode = (info.external_attr >> 16) & 0o777
                if mode and not info.is_dir() and not WINDOWS:
                    out.chmod(mode)
    else:
        with tarfile.open(archive) as t:
            t.extractall(into, filter="data")


def _find(root: Path, filename: str) -> Path | None:
    hits = sorted(root.rglob(filename), key=lambda p: len(p.parts))
    return hits[0] if hits else None


def _swap_in(new_dir: Path, target: Path) -> None:
    """把装好的目录换到 target：旧的先挪开，换好再删（Windows 上旧程序正被用着时报清楚）。"""
    old = target.with_name(f".{target.name}.old{os.getpid()}")
    try:
        if target.exists():
            target.rename(old)
        new_dir.rename(target)
    except OSError as e:
        if old.exists() and not target.exists():
            old.rename(target)
        raise CliError("install_failed", f"换不了 {target}：{e}", exit=FAILED,
                       hint="有程序正在用它（日常、MAA）？等它结束再装") from None
    shutil.rmtree(old, ignore_errors=True)


def install(name: str, version: str | None = None, *, fetch=download) -> dict:
    """下载并装好 maa（maa-cli）或 adb 到 .tools。→ {name, version, path, url, replaced, sha256}。fetch：测试换成假的。
    下载的包先和公布的 sha256（expected_checksum）对过才解压；对不上、查不到哈希都不装。"""
    if name == "maa":
        version = (version or MAA_CLI_TESTED[1]).removeprefix("v")
        url = maa_cli_url(version)
    elif name == "adb":
        version, url = ADB_VERSION, adb_url()
    else:
        raise CliError("bad_arguments", f"不认识 {name}（有：maa-cli、adb）", exit=2)
    base = tools_dir()
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{DIRS[name]}.", dir=base))
    try:
        archive = work / url.rsplit("/", 1)[-1]
        sha256, size = expected_checksum(name, version, url)
        fetch(url, archive)
        try:
            checksum.check_file(archive, sha256, size)
        except checksum.Mismatch as e:                 # work 整个删掉（finally），对不上的包不留
            raise CliError("checksum_mismatch", f"下载的包和公布的对不上，没有装：{e}", exit=FAILED,
                           hint="多半是没下完或代理改了内容：再试一次；换个网络还是这样就别装，告诉用户") from None
        unpacked = work / "unpacked"
        _extract(archive, unpacked)
        found = _find(unpacked, exe_name(name))
        if found is None:
            raise CliError("install_failed", f"下载的包里没有 {exe_name(name)}：{url}", exit=FAILED)
        if not WINDOWS:
            found.chmod(found.stat().st_mode | 0o755)
        root = found.parent                            # adb 的包里是 platform-tools/adb，连同旁边的库一起留着
        (root / VERSION_FILE).write_text(version + "\n", encoding="utf-8")
        target = base / DIRS[name]
        replaced = installed_version(name) if target.exists() else None
        _swap_in(root, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"name": "maa-cli" if name == "maa" else name, "version": version,
            "path": str(target / exe_name(name)), "url": url, "replaced": replaced, "sha256": sha256}
