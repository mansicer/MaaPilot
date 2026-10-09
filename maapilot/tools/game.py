"""`maapilot game version / update`：游戏客户端的版本与更新（只支持官服）。

- version：装着的版本（`adb shell dumpsys package <包名>`）和官网最新版（跟随 https://ak.hypergryph.com/downloads/android_lastest
  的跳转，只发一个 `Range: bytes=0-0` 的请求，不下载安装包），不拿锁、只读。
- update：下载安装包到 private/cache/game/（断点续传，先下到 `.part`），拿模拟器锁后关掉游戏、`adb install -r`、
  再读一次版本核对。下载时不拿锁（下载要十几分钟，不挡日常）。不启动游戏：第一次启动由 agent 用 `maapilot maa startup` 做。

联网与 adb 都走本模块的几个小函数（`adb_run`、`http_open`、`disk_free`），测试里换成假的。契约见 docs/cli.md「game」。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from maapilot import config, paths
from maapilot.cliutil import BAD_REQUEST, ENVIRONMENT, FAILED, NEEDS_USER, CliError, log
from maapilot.tools import common

LATEST_URL = "https://ak.hypergryph.com/downloads/android_lastest"
ALLOWED_HOSTS = ("hypergryph.com", "hycdn.cn")      # 跳转后的下载地址只认这两个域名（及其子域名）
USER_AGENT = "Mozilla/5.0 (Linux; Android 12) MaaPilot"
CHUNK = 1 << 20                                     # 下载时每次读 1 MiB
DISK_MARGIN = 1 << 30                               # 下载前要多留 1 GiB
HTTP_TIMEOUT = 30                                   # 单次读写的超时（秒）
INSTALL_TIMEOUT = 30 * 60                           # adb install 最长等 30 分钟

_APK_NAME = re.compile(r"arknights-hg-(\d{3,})\.apk$")


# --- 外部访问（测试里换掉） ------------------------------------------------------------------

def adb_run(argv: list[str], timeout: float = 30) -> tuple[int, str]:
    """跑一条 adb 命令，返回 (退出码, stdout+stderr)。找不到程序 127，超时 124。"""
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        return 127, str(e)
    except subprocess.TimeoutExpired:
        return 124, f"超时（{int(timeout)} 秒）"
    except OSError as e:
        return 126, str(e)
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")


def http_open(url: str, start: int = 0, end: int | None = None):
    """GET url（跟随跳转），带 Range: bytes=start-[end]。返回 urllib 的响应对象（有 .status、.headers、.geturl()、.read()、.close()）。
    网络错误抛 OSError（urllib.error.URLError 是它的子类）。"""
    rng = f"bytes={start}-" + ("" if end is None else str(end))
    req = urllib.request.Request(url, headers={"Range": rng, "User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)


def disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


# --- 纯函数 ----------------------------------------------------------------------------------

def version_from_url(url: str) -> str | None:
    """安装包地址 → 版本号：…/arknights-hg-2781.apk → "2.7.81"（第一位大版本、第二位小版本、其余是补丁号）。对不上 → None。"""
    m = _APK_NAME.search(urlparse(url or "").path)
    if not m:
        return None
    d = m.group(1)
    return f"{int(d[0])}.{int(d[1])}.{int(d[2:])}"


def version_key(v: str | None) -> tuple[int, ...] | None:
    """"2.7.81" → (2, 7, 81)，按数字比较；读不出 → None。"""
    if not v:
        return None
    parts = re.findall(r"\d+", str(v))
    return tuple(int(x) for x in parts) if parts else None


def newer(latest: str | None, installed: str | None) -> bool | None:
    a, b = version_key(latest), version_key(installed)
    if a is None or b is None:
        return None
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def parse_dumpsys(text: str) -> dict | None:
    """`dumpsys package <包名>` 的输出 → {version, code, updated_at}；没装（没有 versionName）→ None。取第一处出现的值。"""
    name = re.search(r"versionName=(\S+)", text or "")
    if not name:
        return None
    code = re.search(r"versionCode=(\d+)", text)
    upd = re.search(r"lastUpdateTime=(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", text)
    return {"version": name.group(1), "code": int(code.group(1)) if code else None,
            "updated_at": upd.group(1) if upd else None}


def allowed_host(url: str) -> bool:
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def total_size(status: int, headers) -> int | None:
    """响应的整个文件大小：206 看 Content-Range 的 `/总数`，200 看 Content-Length。"""
    cr = headers.get("Content-Range") if headers is not None else None
    if status == 206 and cr:
        m = re.search(r"/(\d+)\s*$", cr)
        return int(m.group(1)) if m else None
    cl = headers.get("Content-Length") if headers is not None else None
    if status == 200 and cl and str(cl).isdigit():
        return int(cl)
    return None


def _range_start(headers) -> int | None:
    m = re.match(r"\s*bytes\s+(\d+)-", (headers.get("Content-Range") or "") if headers is not None else "")
    return int(m.group(1)) if m else None


# --- 读版本 ----------------------------------------------------------------------------------

def _check_server() -> tuple[dict, str]:
    cfg = config.load()                                 # 没配置 → 退出码 5
    srv = config.server(cfg)
    if srv != "Official":
        raise CliError("unsupported_server", f"游戏更新目前只支持官服，配置里的服务器是 {srv}",
                       hint="其他服务器请用户自己在应用商店或官网更新", exit=BAD_REQUEST)
    return cfg, config.package(cfg)


def installed_version(package: str) -> dict | None:
    conn = config.connection()                          # 没填地址 → 退出码 5
    rc, out = adb_run([conn.adb, "-s", conn.address, "shell", "dumpsys", "package", package], timeout=30)
    if rc != 0:
        raise CliError("adb_failed", f"adb 读不了模拟器上的游戏版本（退出码 {rc}）：{out.strip()[-300:]}",
                       hint="maapilot doctor", exit=ENVIRONMENT)
    return parse_dumpsys(out)


def latest_version() -> dict:
    """官网最新版：{version, url, size}。只请求 1 个字节，不下载安装包。"""
    try:
        resp = http_open(LATEST_URL, 0, 0)
        try:
            final, status, headers = resp.geturl(), resp.status, resp.headers
        finally:
            resp.close()
    except (OSError, ValueError) as e:
        raise CliError("network", f"连不上官网查最新版本：{type(e).__name__}: {e}",
                       hint="检查网络后再试", exit=FAILED) from None
    if not allowed_host(final):
        raise CliError("unexpected_download", f"官网下载地址跳到了意料之外的网址：{final}",
                       hint="不要下载；告诉用户官网下载地址变了，请用户自己更新", exit=FAILED, data={"url": final})
    return {"version": version_from_url(final), "url": final, "size": total_size(status, headers)}


def version_info() -> dict:
    """`game version` 的数据（日常里也直接调）。出错抛 CliError。"""
    cfg, package = _check_server()
    installed = installed_version(package)
    latest = latest_version()
    if installed is None:
        need = True if latest["version"] else None
    else:
        need = newer(latest["version"], installed["version"])
    return {"installed": installed, "latest": latest, "update_needed": need,
            "accept_terms": config.accept_terms(cfg)}


def cmd_version(args) -> dict:
    return version_info()


# --- 下载 ------------------------------------------------------------------------------------

def cache_dir() -> Path:
    return paths.PRIVATE / "cache" / "game"


def _is_zip(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"PK"
    except OSError:
        return False


def _gb(n: int) -> str:
    return f"{n / (1 << 30):.1f} GB"


def download(url: str, size: int | None) -> Path:
    """下载到 cache_dir()/<文件名>，断点续传（先写 `.part`）。已经有完整的同名安装包就直接用。返回安装包路径。"""
    name = Path(urlparse(url).path).name or "arknights.apk"
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    apk, part = d / name, d / (name + ".part")
    if apk.exists() and size and apk.stat().st_size == size and _is_zip(apk):
        log(f"已有下载好的安装包：{apk.name}")
        return apk
    have = part.stat().st_size if part.exists() else 0
    if size is not None and have >= size:              # 比整个文件还大：不对，重下
        part.unlink()
        have = 0
    need = (size - have if size is not None else 0) + DISK_MARGIN
    free = disk_free(d)
    if free < need:
        raise CliError("disk_full", f"磁盘空间不够：下载还要 {_gb(need - DISK_MARGIN)}（另留 1 GB 余量），现在只剩 {_gb(free)}",
                       hint="请用户清理磁盘后再试", exit=NEEDS_USER,
                       data={"free_bytes": free, "need_bytes": need, "apk_bytes": size})
    try:
        resp = http_open(url, have)
    except (OSError, ValueError) as e:
        raise CliError("network", f"下载失败：{type(e).__name__}: {e}",
                       hint="检查网络后再跑一次，会接着下", exit=FAILED) from None
    try:
        if not allowed_host(resp.geturl()):
            raise CliError("unexpected_download", f"下载地址跳到了意料之外的网址：{resp.geturl()}",
                           hint="不要下载；告诉用户，请用户自己更新", exit=FAILED)
        total = total_size(resp.status, resp.headers) or size
        if resp.status == 206 and _range_start(resp.headers) == have:
            mode = "ab"
        elif resp.status == 200:                       # 服务器不支持续传：从头下
            mode, have = "wb", 0
        else:
            raise CliError("network", f"下载服务器的回应不对（HTTP {resp.status}，{resp.headers.get('Content-Range')}）",
                           hint="再跑一次；还不行就删掉 private/cache/game/ 重下", exit=FAILED)
        if size and total and total != size:
            raise CliError("network", f"下载时安装包大小变了（{size} → {total}），官网可能刚发了新版",
                           hint="重新跑一次 game update", exit=FAILED)
        if have:
            log(f"接着上次下载：已有 {_gb(have)}")
        got, step, t0 = have, max((total or 0) // 20, CHUNK * 50), time.monotonic()
        next_log = got + step
        with open(part, mode) as f:
            while True:
                try:
                    chunk = resp.read(CHUNK)
                except (OSError, ValueError) as e:
                    raise CliError("network", f"下载中断（已下 {_gb(got)}）：{type(e).__name__}: {e}",
                                   hint="再跑一次 game update，会接着下", exit=FAILED,
                                   data={"downloaded_bytes": got, "apk_bytes": total}) from None
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if got >= next_log:
                    pct = f"{got * 100 // total}%" if total else ""
                    speed = (got - have) / max(time.monotonic() - t0, 0.001) / (1 << 20)
                    log(f"下载中 {pct} {_gb(got)}，{speed:.1f} MB/s")
                    next_log = got + step
    finally:
        resp.close()
    got = part.stat().st_size
    if total is not None and got != total:
        raise CliError("download_incomplete", f"下载不完整：{got} / {total} 字节",
                       hint="再跑一次 game update，会接着下", exit=FAILED,
                       data={"downloaded_bytes": got, "apk_bytes": total})
    if not _is_zip(part):
        part.unlink(missing_ok=True)
        raise CliError("bad_apk", "下载下来的不是安装包（不是 zip 文件），已删掉",
                       hint="再跑一次 game update；还不行就请用户自己从官网更新", exit=FAILED)
    part.replace(apk)
    return apk


def _cleanup(keep: Path | None) -> None:
    """删掉缓存目录里的 .part 和别的安装包（keep 除外）。"""
    d = cache_dir()
    if not d.exists():
        return
    for f in d.iterdir():
        if f.is_file() and f != keep and (f.name.endswith(".part") or f.suffix == ".apk"):
            f.unlink(missing_ok=True)


# --- 安装 ------------------------------------------------------------------------------------

_INSTALL_HINTS = {
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "模拟器存储空间不够：请用户在模拟器设置里扩容或清理后，再跑 game update（安装包留着，不用重下）",
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "签名和装着的不一致（装的不是官服包？）：只能卸载重装，会清掉游戏的本地数据，先问用户",
    "INSTALL_FAILED_VERSION_DOWNGRADE": "装着的版本比下载的还新，不用更新",
    "INSTALL_FAILED_NO_MATCHING_ABIS": "模拟器的 CPU 架构不支持这个安装包，请用户自己从官网或应用商店更新",
}


def install_error(out: str) -> str | None:
    m = re.search(r"INSTALL_[A-Z_]+", out or "") or re.search(r"Failure \[[^\]]*\]", out or "")
    return m.group(0) if m else None


def install(apk: Path, package: str) -> None:
    conn = config.connection()
    rc, out = adb_run([conn.adb, "-s", conn.address, "shell", "am", "force-stop", package], timeout=30)
    if rc != 0:
        raise CliError("adb_failed", f"关不了游戏（adb 退出码 {rc}）：{out.strip()[-300:]}",
                       hint="maapilot doctor", exit=ENVIRONMENT)
    log("正在安装（可能要几分钟）…")
    rc, out = adb_run([conn.adb, "-s", conn.address, "install", "-r", str(apk)], timeout=INSTALL_TIMEOUT)
    if rc != 0 or "Success" not in out:
        reason = install_error(out)
        hint = next((h for k, h in _INSTALL_HINTS.items() if reason and k in reason),
                    "截图看看模拟器；装不上就请用户自己从官网或应用商店更新（安装包留着，可以再试）")
        raise CliError("install_failed", f"安装失败：{reason or out.strip()[-300:] or f'adb 退出码 {rc}'}",
                       hint=hint, exit=FAILED,
                       data={"reason": reason, "adb_output": out.strip()[-2000:], "apk": common.rel(apk)})


# --- update ----------------------------------------------------------------------------------

def update(download_only: bool = False, keep: bool = False) -> dict:
    """`game update` 的本体（日常里也直接调）：要更新就下载、拿锁安装、核对。出错抛 CliError。"""
    t0 = time.monotonic()
    info = version_info()
    latest, installed = info["latest"], info["installed"]
    frm = installed["version"] if installed else None
    if info["update_needed"] is None:
        raise CliError("version_unknown", f"比不出要不要更新：装着的 {frm}，官网的安装包 {latest['url']}",
                       hint="请用户自己看看要不要更新", exit=FAILED, data=info)
    if not info["update_needed"]:
        return {"updated": False, "reason": f"已经是最新版 {frm}", "from": frm, "to": frm}
    package = config.package()
    log(f"要更新：{frm} → {latest['version']}，安装包 {_gb(latest['size'] or 0)}")
    apk = download(latest["url"], latest["size"])
    size = apk.stat().st_size
    if download_only:
        _cleanup(apk)
        return {"updated": False, "reason": "只下载，没有安装", "from": frm, "to": latest["version"],
                "apk": common.rel(apk), "apk_bytes": size, "duration_s": round(time.monotonic() - t0, 1)}
    with common.device("game update", job="game update"):
        install(apk, package)
        now = installed_version(package)
    if not now or version_key(now["version"]) != version_key(latest["version"]):
        raise CliError("verify_failed", f"装完读到的版本是 {now and now['version']}，应该是 {latest['version']}",
                       hint="截图看看模拟器；再跑一次 game version 确认", exit=FAILED,
                       data={"installed": now, "latest": latest, "apk": common.rel(apk)})
    _cleanup(apk if keep else None)
    return {"updated": True, "from": frm, "to": now["version"], "apk_bytes": size,
            "duration_s": round(time.monotonic() - t0, 1)}


@common.logged
def cmd_update(args) -> dict:
    return update(download_only=args.download_only, keep=args.keep)


def register(sub) -> None:
    g = sub.add_parser("game", help="游戏客户端：看版本、下载并安装更新（只支持官服）")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("version", help="装着的版本、官网最新版、要不要更新（只读，不下载安装包）")
    p.set_defaults(func=cmd_version, command="game version")
    p = gs.add_parser("update", help="下载最新安装包并装到模拟器上（十几分钟；装前关掉游戏，装完不启动）")
    p.add_argument("--download-only", action="store_true", help="只下载，不安装（不拿模拟器锁）")
    p.add_argument("--keep", action="store_true", help="装完留着安装包（默认删掉）")
    p.set_defaults(func=cmd_update, command="game update")
