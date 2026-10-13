"""不经 maa-cli、直接调 MaaCore 跑任务（maa-cli 还不认的任务类型用，目前是干员培养 OperProgress）。

maa-cli v0.7.5 的任务类型是写死的枚举，MaaCore v6.19.0 新加的 OperProgress 它跑不了；这里在子进程
（maapilot.crew.maacore_runner）里用 ctypes 加载 MaaCore，把回调一条条收回来。

本体单独装在 `private/cache/maa-core/<版本>/`（`install()` 从 MAA 的 GitHub 发布下载），和 maa-cli 自己的本体分开，
互不影响。资源先加载本体自带的，再叠加 maa-cli 热更新的资源（新干员、新活动），和 maa-cli 一样。

这里不拿锁、不做参数检查：那些在调用方（maapilot.flows.op）。本模块只管"跑一次、给回全部回调"。
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from maapilot import paths

RELEASES_API = "https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights/releases/latest"
LIB_NAMES = ("libMaaCore.dylib", "libMaaCore.so")
VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

# AsstMsg
MSG_SUBTASK_EXTRA_INFO = 20003
MSG_TASKCHAIN_ERROR = 10000
MSG_SUBTASK_ERROR = 20000

# 和 private/maa-config/profiles/default.toml（maa-cli 用的）一致
INSTANCE_OPTIONS = {"touch_mode": "maatouch", "deployment_with_pause": 0, "adb_lite_enabled": 0, "kill_adb_on_exit": 0}

RUNNER_EXIT = {10: "MaaCore 或资源加载失败", 11: "MaaCore 连不上模拟器", 12: "MaaCore 不接受这份任务参数",
               13: "MaaCore 没能开始任务", 14: "任务被叫停"}


def root() -> Path:
    return paths.PRIVATE / "cache" / "maa-core"


def _version_key(name: str) -> tuple[int, ...] | None:
    m = VERSION_RE.match(name)
    return tuple(int(x) for x in m.groups()) if m else None


def find() -> tuple[str, Path] | None:
    """装好的最新本体：(版本, 含 libMaaCore 的目录)；没有 → None。"""
    best = None
    if root().is_dir():
        for d in root().iterdir():
            key = _version_key(d.name)
            if key is None:
                continue
            lib = next((p for n in LIB_NAMES for p in d.rglob(n)), None)
            if lib is not None and (best is None or key > best[0]):
                best = (key, d.name, lib.parent)
    return None if best is None else (best[1], best[2])


def at_least(version: str, minimum: str) -> bool:
    a, b = _version_key(version), _version_key(minimum)
    return a is not None and b is not None and a >= b


def _asset_pattern() -> re.Pattern:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Darwin":
        return re.compile(r"^MAA-v[\d.]+-macos-runtime-universal\.zip$")
    if system == "Linux":
        arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(machine, machine)
        return re.compile(rf"^MAA-v[\d.]+-linux-{re.escape(arch)}\.tar\.gz$")
    raise RuntimeError(f"不支持这个系统（{system}）")


def install(*, timeout_s: float = 600) -> dict:
    """下载 MAA 最新发布的本体到 private/cache/maa-core/<版本>/。已经有这个版本就不下。"""
    with urllib.request.urlopen(urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"}),
                                timeout=30) as r:
        rel = json.load(r)
    tag = rel["tag_name"]
    have = find()
    if have and have[0] == tag:
        return {"version": tag, "dir": str(have[1]), "installed": False}
    pat = _asset_pattern()
    asset = next((a for a in rel.get("assets") or [] if pat.match(a["name"])), None)
    if asset is None:
        raise RuntimeError(f"{tag} 的发布里没有这个系统的本体")
    dest = root() / tag
    tmp = root() / f".{tag}.download"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    archive = tmp / asset["name"]
    with urllib.request.urlopen(asset["browser_download_url"], timeout=timeout_s) as r, archive.open("wb") as f:
        shutil.copyfileobj(r, f)
    out = tmp / "x"
    if archive.name.endswith(".zip"):
        # zipfile 不保留可执行位和符号链接；macOS 自带 unzip 能保留
        if shutil.which("unzip"):
            subprocess.run(["unzip", "-q", str(archive), "-d", str(out)], check=True)
        else:
            zipfile.ZipFile(archive).extractall(out)
    else:
        with tarfile.open(archive) as t:
            t.extractall(out, filter="data")
    shutil.rmtree(dest, ignore_errors=True)
    out.rename(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    found = find()
    return {"version": tag, "dir": str(found[1]) if found else str(dest), "installed": True}


def hot_update_dir() -> Path | None:
    """maa-cli 热更新的资源目录（`maa dir hot-update`）；拿不到 → None。"""
    from maapilot import config
    try:
        out = subprocess.run([config.tool("maa"), "dir", "hot-update"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    p = Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    return p if p and (p / "resource").is_dir() else None


@dataclass
class CoreRun:
    returncode: int | None
    events: list[dict]                          # 子进程自己的进度行
    messages: list[dict]                        # MaaCore 回调 {msg, details}
    log: Path
    duration_s: float
    timed_out: bool = False
    error: str | None = None                    # 子进程起不来、加载 / 连接 / 参数失败
    version: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.returncode == 0

    def extra(self, what: str) -> list[dict]:
        """SubTaskExtraInfo 里 what 为指定值的 details。"""
        return [m["details"].get("details") or {} for m in self.messages
                if m.get("msg") == MSG_SUBTASK_EXTRA_INFO and m["details"].get("what") == what]


def runner_argv() -> list[str]:
    return [sys.executable, "-m", "maapilot.crew.maacore_runner"]


def run_task(task: str, params: dict, *, label: str, timeout_s: float) -> CoreRun:
    """在子进程里跑一个 MaaCore 任务，等它结束（超时整组结束）。调用方已经 configure 过设备。"""
    from maapilot.crew import device, maa
    found = find()
    log = maa.new_log(label)
    if found is None:
        return CoreRun(None, [], [], log, 0.0, error="没装单独的 MaaCore")
    version, lib_dir = found
    lib = next(lib_dir / n for n in LIB_NAMES if (lib_dir / n).exists())
    resources = [str(lib_dir)]
    hot = hot_update_dir()
    if hot is not None:
        resources.append(str(hot))
    conn = device.current()
    user_dir = root() / "userdir"
    user_dir.mkdir(parents=True, exist_ok=True)
    spec = {"lib": str(lib), "resources": resources, "user_dir": str(user_dir),
            "adb": shutil.which(conn.adb) or conn.adb, "address": conn.address,
            "task": task, "params": params, "instance_options": INSTANCE_OPTIONS}
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(runner_argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", start_new_session=True, cwd=str(paths.ROOT))
    except OSError as e:
        return CoreRun(None, [], [], log, 0.0, error=f"起不来 MaaCore 子进程：{e}", version=version)
    timed_out = False
    with maa._stops_with_us(proc):
        try:
            stdout, _ = proc.communicate(json.dumps(spec, ensure_ascii=False), timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            maa._kill_group(proc)
            try:
                stdout, _ = proc.communicate(timeout=maa.KILL_GRACE_S)
            except (subprocess.TimeoutExpired, ValueError):
                stdout = ""
    stdout = stdout or ""
    log.write_text(f"# {task} {json.dumps(params, ensure_ascii=False)}\n# MaaCore {version}，资源 {resources}\n{stdout}",
                   encoding="utf-8")
    events, messages = parse(stdout)
    run = CoreRun(proc.returncode, events, messages, log, round(time.monotonic() - t0, 1), timed_out=timed_out,
                  version=next((e["version"] for e in events if e.get("event") == "version"), version))
    if not timed_out and proc.returncode:
        run.error = RUNNER_EXIT.get(proc.returncode, f"MaaCore 子进程退出码 {proc.returncode}")
    return run


def parse(stdout: str) -> tuple[list[dict], list[dict]]:
    events, messages = [], []
    for line in stdout.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue                            # MaaCore 自己往 stdout 打的东西
        if not isinstance(obj, dict):
            continue
        if "event" in obj:
            events.append(obj)
        elif "msg" in obj and isinstance(obj.get("details"), dict):
            messages.append(obj)
    return events, messages
