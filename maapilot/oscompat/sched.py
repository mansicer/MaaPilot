"""v0.1 的系统定时（v0.2.0 起不再装）：只读、只删本项目留下的条目。macOS / Linux 是 cron，Windows 是任务计划程序（schtasks.exe）。

- cron：crontab 里本项目的行带标记 `# maapilot-v2 <仓库路径>`，只删这些行，别的行一个字节都不动；更旧的 MaaPilot 的行
  （`maapilot job tick`、`bin/daily.sh`）列为 old，不删。测试用 MAAPILOT_CRONTAB 换掉 crontab 程序。
- schtasks：一个仓库一个任务 `\\MaaPilot\\daily-<仓库路径的 sha1 前 8 位>`，描述 `maapilot-daily <仓库路径>` 是"本项目的"标记。
  `\\MaaPilot\\` 下别的任务（别的目录的 MaaPilot）列为 old，不删。只读 schtasks 的 CSV 里的任务名和 /XML 的内容，
  不解析本地化的表格文字。测试用 MAAPILOT_SCHTASKS 换掉 schtasks 程序。
- fake：MAAPILOT_SCHEDULER=fake:<json 路径>，条目存在一个 JSON 文件里（测试用，各系统一样）。
"""
from __future__ import annotations

import codecs
import csv
import hashlib
import io
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol

from maapilot import paths
from maapilot.cliutil import ENVIRONMENT, CliError
from maapilot.oscompat import MACOS, WINDOWS, fs

TAG = "# maapilot-v2"
OLD_MARKS = ("maapilot job tick", "bin/daily.sh")       # 旧版 MaaPilot 的定时任务
_CRON_HM = re.compile(r"^\s*(\d{1,2})\s+(\d{1,2}(?:,\d{1,2})*)\s+\*\s+\*\s+\*\s")
SCHEDULER_ENV = "MAAPILOT_SCHEDULER"
SCHTASKS_ENV = "MAAPILOT_SCHTASKS"

# runner(argv, input, timeout) -> (退出码, stdout, stderr)，一律字节；doctor 换成走它的 Probe
Runner = Callable[[list, "bytes | None", float], "tuple[int, bytes, bytes]"]


@dataclass
class Entry:
    times: list[str]          # 排好序的 "HH:MM"
    command: str              # 执行什么（给人看）
    mine: bool                # 本项目（这个仓库）的
    ident: str                # cron 的整行 / 任务名
    old: bool = False         # 旧版 MaaPilot 的定时任务（schtasks：别的目录的 MaaPilot）
    enabled: bool | None = None       # 任务启用着没有（cron 没有这个概念：None）
    settings: dict | None = None      # schtasks：{run_if_missed, wake}


class Backend(Protocol):
    name: str

    def available(self) -> tuple[bool, str | None]: ...
    def read(self) -> list[Entry]: ...
    def remove(self, *, dry_run: bool = False) -> dict: ...


def _default_run(argv: list, input: bytes | None, timeout: float) -> tuple[int, bytes, bytes]:
    kw = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    if sys.platform == "win32":          # 不看 WINDOWS：测试改它模拟 Windows   # pragma: no cover - Windows only
        from maapilot.oscompat import procs
        kw["creationflags"] = procs.CREATE_NO_WINDOW                 # pythonw 起的日常里查定时也不弹黑窗口
    p = subprocess.run(argv, capture_output=True, timeout=timeout, env={**os.environ, "PYTHONUTF8": "1"}, **kw)
    return p.returncode, p.stdout or b"", p.stderr or b""


def decode(b: bytes) -> str:
    """Windows 程序的输出：有 UTF-16 的 BOM（或看得出是 UTF-16）就按 UTF-16，否则先试 UTF-8，再按系统代码页。"""
    if b.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return b.decode("utf-16", "replace")
    if len(b) >= 4 and b[1] == 0 and b[3] == 0 and b[0] != 0:          # 没有 BOM 的 UTF-16 LE
        return b.decode("utf-16-le", "replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in (_oem_encoding(), locale.getencoding()):
        if enc:
            try:
                return b.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
    return b.decode("latin-1")


def _oem_encoding() -> str | None:
    """Windows 控制台程序写进管道用的 OEM 代码页（中文系统 cp936，英文 cp437）；别的系统 None。"""
    if not WINDOWS:
        return None
    try:                                                              # pragma: no cover - Windows only
        import ctypes
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except (ImportError, OSError, AttributeError):                    # pragma: no cover
        return None


# --- cron ------------------------------------------------------------------------------------------------------------

class CronBackend:
    name = "cron"

    def __init__(self, run: Runner | None = None):
        self.run = run or _default_run

    @staticmethod
    def program() -> str:
        """crontab 程序；测试用 MAAPILOT_CRONTAB 换成假的。"""
        return os.environ.get("MAAPILOT_CRONTAB") or "crontab"

    def available(self) -> tuple[bool, str | None]:
        return True, None

    # 文本层（纯函数）
    @staticmethod
    def mine(line: str) -> bool:
        """本仓库的行（新旧写法都认：结尾都是 `# maapilot-v2 <仓库>`）。"""
        return TAG in line and line.rstrip().endswith(f"{TAG} {paths.ROOT}")

    @staticmethod
    def old(line: str) -> bool:
        s = line.strip()
        return bool(s) and not s.startswith("#") and TAG not in s and any(m in s for m in OLD_MARKS)

    @staticmethod
    def times_of(lines: list[str]) -> list[str]:
        out = []
        for ln in lines:
            m = _CRON_HM.match(ln)
            if m:
                out += [f"{int(h):02d}:{int(m.group(1)):02d}" for h in m.group(2).split(",")]
        return sorted(out)

    # crontab 程序
    def read_text(self) -> str:
        try:
            rc, out, err = self.run([self.program(), "-l"], None, 30)
        except (OSError, subprocess.SubprocessError) as e:
            raise CliError("no_crontab", f"读不了 crontab：{e}", exit=ENVIRONMENT) from None
        err = decode(err)
        if rc != 0:
            if "no crontab" in err.lower():
                return ""
            raise CliError("no_crontab", f"读不了 crontab：{err.strip()}", exit=ENVIRONMENT)
        return out.decode("utf-8", "replace")

    def write_text(self, text: str) -> None:
        try:
            rc, _, err = self.run([self.program(), "-"], text.encode("utf-8"), 60)
        except (OSError, subprocess.SubprocessError) as e:
            raise CliError("crontab_failed", f"写不了 crontab：{e}", exit=ENVIRONMENT) from None
        if rc != 0:
            raise CliError("crontab_failed", f"写不了 crontab：{decode(err).strip()}",
                           hint="macOS 弹窗问终端能不能改 crontab 时要点允许" if MACOS else None, exit=ENVIRONMENT)

    def split(self, text: str) -> tuple[list[str], list[str]]:
        lines = text.splitlines()
        return [ln for ln in lines if self.mine(ln)], [ln for ln in lines if self.old(ln)]

    def read(self) -> list[Entry]:
        mine, old = self.split(self.read_text())
        return ([Entry(self.times_of([ln]), ln, True, ln) for ln in mine]
                + [Entry(self.times_of([ln]), ln, False, ln, old=True) for ln in old])

    def remove(self, *, dry_run=False) -> dict:
        cur = self.read_text()
        mine, old = self.split(cur)
        keep = [ln for ln in cur.splitlines() if not self.mine(ln)]
        text = "\n".join(keep) + ("\n" if keep else "")
        if mine and not dry_run:
            self.write_text(text)
        return {"removed": mine, "old_entries": old, "preview": text}


# --- Windows 任务计划程序 -------------------------------------------------------------------------------------------

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
DESC = "maapilot-daily"                      # 描述 `maapilot-daily <仓库路径>`：本项目的任务
FOLDER = "\\MaaPilot\\"
_BOUNDARY_HM = re.compile(r"T(\d{2}):(\d{2})")


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def parse_xml(data: bytes | str) -> dict:
    """读任务定义（纯函数）：{description, times, command, arguments, workdir, enabled, run_if_missed, wake}。
    times 只算启用着的、每天的触发器（StartBoundary 的时分）。"""
    text = decode(data) if isinstance(data, bytes) else data
    text = text.lstrip("\ufeff")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise CliError("schtasks_failed", f"任务计划程序给的任务定义读不懂：{e}", exit=ENVIRONMENT) from None

    def find(path: str) -> ET.Element | None:
        return root.find("/".join(_q(p) for p in path.split("/")))

    def txt(path: str, default: str | None = None) -> str | None:
        e = find(path)
        return e.text.strip() if e is not None and e.text else default

    def flag(path: str, default: bool) -> bool:
        v = txt(path)
        return default if v is None else v.lower() == "true"

    times = []
    trig = find("Triggers")
    for ct in (trig if trig is not None else []):
        if ct.tag != _q("CalendarTrigger") or ct.find(_q("ScheduleByDay")) is None:
            continue
        en = ct.find(_q("Enabled"))
        if en is not None and (en.text or "").strip().lower() == "false":
            continue
        sb = ct.find(_q("StartBoundary"))
        m = _BOUNDARY_HM.search(sb.text or "") if sb is not None else None
        if m:
            times.append(f"{m.group(1)}:{m.group(2)}")
    return {"description": txt("RegistrationInfo/Description", "") or "", "times": sorted(times),
            "command": txt("Actions/Exec/Command", "") or "", "arguments": txt("Actions/Exec/Arguments", "") or "",
            "workdir": txt("Actions/Exec/WorkingDirectory", "") or "", "enabled": flag("Settings/Enabled", True),
            "run_if_missed": flag("Settings/StartWhenAvailable", False), "wake": flag("Settings/WakeToRun", False)}


def task_names(csv_text: str, folder: str = FOLDER) -> list[str]:
    """`schtasks /Query /FO CSV /NH` 的输出里 folder 下的任务名（只看第一列，不看本地化的别的列）。"""
    out = []
    for row in csv.reader(io.StringIO(csv_text)):
        if row and row[0].lower().startswith(folder.lower()) and row[0] not in out:
            out.append(row[0])
    return out


def task_name(root: Path | str, folder: str = FOLDER) -> str:
    return f"{folder}daily-{hashlib.sha1(fs.norm(root).encode('utf-8')).hexdigest()[:8]}"


class SchTasksBackend:
    name = "schtasks"
    TIMEOUT = 30

    def __init__(self, run: Runner | None = None, *, folder: str = FOLDER, root: Path | None = None):
        self.run = run or _default_run
        self.folder = folder
        self.root = Path(root) if root is not None else None

    @property
    def _root(self) -> Path:
        return self.root if self.root is not None else paths.ROOT

    @staticmethod
    def program() -> str:
        """schtasks.exe（System32 里的）；测试用 MAAPILOT_SCHTASKS 换掉。"""
        if os.environ.get(SCHTASKS_ENV):
            return os.environ[SCHTASKS_ENV]
        return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "schtasks.exe")

    def available(self) -> tuple[bool, str | None]:
        p = self.program()
        if os.path.isfile(p) or shutil.which(p):
            return True, None
        return False, f"找不到 Windows 任务计划程序（{p}）"

    def task(self) -> str:
        return task_name(self._root, self.folder)

    def description(self) -> str:
        return f"{DESC} {self._root}"

    def _mine(self, desc: str) -> bool:
        head, _, rest = desc.partition(" ")
        return head == DESC and bool(rest) and fs.same_path(rest, self._root)

    def _schtasks(self, *args: str, what: str) -> bytes:
        try:
            rc, out, err = self.run([self.program(), *args], None, self.TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            raise CliError("schtasks_failed", f"任务计划程序{what}失败：{e}", exit=ENVIRONMENT) from None
        if rc != 0:
            msg = (decode(err) or decode(out)).strip()
            raise CliError("schtasks_failed", f"任务计划程序{what}失败（退出码 {rc}）：{msg}", exit=ENVIRONMENT,
                           hint="打开「任务计划程序」看看 MaaPilot 文件夹；还不行就 maapilot doctor")
        return out

    def _read(self) -> list[tuple[str, dict]]:
        names = task_names(decode(self._schtasks("/Query", "/FO", "CSV", "/NH", what="列任务")), self.folder)
        return [(n, parse_xml(self._schtasks("/Query", "/TN", n, "/XML", what="读任务"))) for n in names]

    def _entry(self, n: str, info: dict) -> Entry:
        mine = self._mine(info["description"])
        return Entry(info["times"], f"{info['command']} {info['arguments']}".strip(), mine, n, old=not mine,
                     enabled=info["enabled"], settings={"run_if_missed": info["run_if_missed"], "wake": info["wake"]})

    def read(self) -> list[Entry]:
        return [self._entry(n, info) for n, info in self._read()]

    def remove(self, *, dry_run=False) -> dict:
        cur = self._read()
        mine = [n for n, i in cur if self._mine(i["description"])]
        old = [n for n, i in cur if not self._mine(i["description"])]
        if not dry_run:
            for n in mine:
                self._schtasks("/Delete", "/TN", n, "/F", what="删任务")
        return {"removed": mine, "old_entries": old, "preview": "\n".join(old)}


# --- 假后端（测试） ----------------------------------------------------------------------------------------------------

class FakeBackend:
    """条目存在 JSON 文件里：[{times, command, mine, ident, old, enabled, settings}]。本项目的条目 ident 是 `maapilot-daily <ROOT>`。"""
    name = "fake"

    def __init__(self, store: Path):
        self.store = Path(store)

    def available(self) -> tuple[bool, str | None]:
        return True, None

    def _load(self) -> list[Entry]:
        try:
            return [Entry(**e) for e in json.loads(self.store.read_text(encoding="utf-8"))]
        except FileNotFoundError:
            return []

    def _save(self, entries: list[Entry]) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps([asdict(e) for e in entries], ensure_ascii=False, indent=1), encoding="utf-8")

    def _ident(self) -> str:
        return f"{DESC} {paths.ROOT}"

    def read(self) -> list[Entry]:
        return [e for e in self._load() if e.ident == self._ident() or e.old]

    def remove(self, *, dry_run=False) -> dict:
        cur = self._load()
        mine = [e for e in cur if e.ident == self._ident()]
        keep = [e for e in cur if e.ident != self._ident()]
        if mine and not dry_run:
            self._save(keep)
        return {"removed": [e.ident for e in mine], "old_entries": [e.ident for e in cur if e.old],
                "preview": json.dumps([asdict(e) for e in keep], ensure_ascii=False)}


# --- 没有可用的调度（Windows 上找不到 schtasks.exe） -------------------------------------------------------------------

class Unavailable:
    """读出来什么都没有，也没什么可删的。"""
    name = "none"

    def __init__(self, hint: str):
        self.hint = hint

    def available(self) -> tuple[bool, str | None]:
        return False, self.hint

    def read(self) -> list[Entry]:
        return []

    def remove(self, *, dry_run=False) -> dict:
        return {"removed": [], "old_entries": [], "preview": ""}


def backend(run: Runner | None = None) -> Backend:
    """当前系统的调度后端。run：换掉跑子进程的方式（doctor 用它的 Probe）。"""
    spec = os.environ.get(SCHEDULER_ENV) or ""
    if spec.startswith("fake:"):
        return FakeBackend(Path(spec[5:]))
    if WINDOWS:
        b = SchTasksBackend(run)
        ok, hint = b.available()
        return b if ok else Unavailable(hint)
    return CronBackend(run)
