"""定时日常的调度后端：macOS / Linux 用 cron，Windows 用任务计划程序（schtasks.exe）。

后端只管"本项目的定时条目"：装、读、删。什么时间、执行什么（`Command`）由 flows/daily/cron.py 决定；
执行的一律是启动器 `python -m maapilot.oscompat.launch …`（环境变量、日志都在它的参数里，见 launch.py）。
- cron：crontab 里本项目的行带标记 `# maapilot-v2 <仓库路径>`，只增删这些行；旧版 MaaPilot 的行（`maapilot job tick`、
  `bin/daily.sh`）列为 old，带 replace_old 才删。测试用 MAAPILOT_CRONTAB 换掉 crontab 程序。cron 不补跑（到点时电脑睡着就跳过这次）。
- schtasks：一个仓库一个任务 `\\MaaPilot\\daily-<仓库路径的 sha1 前 8 位>`，每个时间点一个每天的触发器；
  描述 `maapilot-daily <仓库路径>` 是"本项目的"标记。`\\MaaPilot\\` 下别的任务（别的目录的 MaaPilot）列为 old，带 replace_old 才删。
  只读 schtasks 的 CSV 里的任务名和 /XML 的内容，不解析本地化的表格文字。测试用 MAAPILOT_SCHTASKS 换掉 schtasks 程序。
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
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
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
class Command:
    """定时要执行的命令。argv：完整的启动器命令（项目 .venv 的 python … -m maapilot.oscompat.launch … -- daily run …）；
    log：启动器追加输出的日志（装之前先建好目录）；run_if_missed / wake：到点时电脑关着或睡着，开机后补跑 / 叫醒电脑来跑
    （只有任务计划程序做得到，cron 不管）。"""
    argv: list[str]
    log: Path | None = None
    run_if_missed: bool = True
    wake: bool = False


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
    def install(self, times: list[tuple[int, int]], command: Command, *, replace_old: bool = False,
                dry_run: bool = False) -> dict: ...
    def remove(self, *, dry_run: bool = False) -> dict: ...


def _hm(times: list[tuple[int, int]]) -> list[str]:
    return [f"{h:02d}:{m:02d}" for h, m in times]


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

    @staticmethod
    def render(times: list[tuple[int, int]], command: Command) -> list[str]:
        """每个时间点一行：`M H * * * <启动器命令> # maapilot-v2 <仓库>`。环境变量和日志都在启动器的参数里。"""
        def q(s) -> str:
            return shlex.quote(str(s)).replace("%", r"\%")    # crontab 里 % 是换行
        cmd = " ".join(q(a) for a in command.argv)
        return [f"{m} {h} * * * {cmd} {TAG} {paths.ROOT}" for h, m in times]

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

    def install(self, times, command, *, replace_old=False, dry_run=False) -> dict:
        cur = self.read_text()
        mine, old = self.split(cur)
        keep = [ln for ln in cur.splitlines() if not self.mine(ln) and not (replace_old and self.old(ln))]
        new_lines = self.render(times, command)
        text = "\n".join(keep + new_lines) + "\n"
        changed = text != (cur if cur.endswith("\n") or not cur else cur + "\n")
        if not dry_run and changed:
            if command.log is not None:
                Path(command.log).parent.mkdir(parents=True, exist_ok=True)
            self.write_text(text)
        return {"installed": new_lines, "replaced": mine, "old_entries": old,
                "removed_old": old if replace_old else [], "changed": changed, "preview": text}

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


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrib) -> ET.Element:
    e = ET.SubElement(parent, _q(tag), attrib)
    if text is not None:
        e.text = text
    return e


def _bool(b: bool) -> str:
    return "true" if b else "false"


def _boundaries(times: list[tuple[int, int]], now: datetime) -> list[str]:
    """每个时间点下一次到点的本地时间（不带时区：按电脑当时的时区算）。不用过去的日期：
    "错过了就补跑"开着时，起点在过去的新任务可能一装好就当成错过了、马上跑一次。"""
    out = []
    for h, m in times:
        t = now.replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=None)
        if t <= now.replace(tzinfo=None):
            t += timedelta(days=1)
        out.append(t.strftime("%Y-%m-%dT%H:%M:%S"))
    return out


def build_xml(times: list[tuple[int, int]], argv: list[str], opts: dict) -> str:
    """任务计划程序的任务定义（纯函数）。argv[0] 是执行的程序，其余是参数（按 Windows 的规则加引号）。
    opts：description、workdir、run_if_missed、wake、user（可选，DOMAIN\\用户名）、now（可选，算起点用）。
    只在用户登录时运行（InteractiveToken，不存密码、不要管理员）：模拟器要开在用户的桌面里；锁屏也照常运行。"""
    ET.register_namespace("", NS)
    task = ET.Element(_q("Task"), {"version": "1.2"})
    reg = _sub(task, "RegistrationInfo")
    _sub(reg, "Description", opts["description"])
    trig = _sub(task, "Triggers")
    for boundary in _boundaries(times, opts.get("now") or datetime.now()):
        ct = _sub(trig, "CalendarTrigger")
        _sub(ct, "StartBoundary", boundary)
        _sub(ct, "Enabled", "true")
        _sub(_sub(ct, "ScheduleByDay"), "DaysInterval", "1")
    pr = _sub(_sub(task, "Principals"), "Principal", id="Author")
    if opts.get("user"):
        _sub(pr, "UserId", opts["user"])
    _sub(pr, "LogonType", "InteractiveToken")
    _sub(pr, "RunLevel", "LeastPrivilege")
    st = _sub(task, "Settings")
    _sub(st, "MultipleInstancesPolicy", "IgnoreNew")
    _sub(st, "DisallowStartIfOnBatteries", "false")
    _sub(st, "StopIfGoingOnBatteries", "false")
    _sub(st, "AllowHardTerminate", "true")
    _sub(st, "StartWhenAvailable", _bool(opts.get("run_if_missed", True)))
    _sub(st, "RunOnlyIfNetworkAvailable", "false")
    idle = _sub(st, "IdleSettings")
    _sub(idle, "StopOnIdleEnd", "false")
    _sub(idle, "RestartOnIdle", "false")
    _sub(st, "AllowStartOnDemand", "true")
    _sub(st, "Enabled", "true")
    _sub(st, "Hidden", "false")
    _sub(st, "RunOnlyIfIdle", "false")
    _sub(st, "WakeToRun", _bool(opts.get("wake", False)))
    _sub(st, "ExecutionTimeLimit", "PT6H")
    _sub(st, "Priority", "7")
    ex = _sub(_sub(task, "Actions", Context="Author"), "Exec")
    _sub(ex, "Command", str(argv[0]))
    if len(argv) > 1:
        _sub(ex, "Arguments", subprocess.list2cmdline([str(a) for a in argv[1:]]))
    if opts.get("workdir"):
        _sub(ex, "WorkingDirectory", str(opts["workdir"]))
    ET.indent(task)
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(task, encoding="unicode") + "\n"


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

    @staticmethod
    def gui_python(exe: str) -> str:
        """python.exe 旁边有 pythonw.exe 就用它（不开控制台窗口）。"""
        p = Path(exe)
        if p.name.lower() == "python.exe":
            w = p.with_name("pythonw.exe")
            if w.is_file():
                return str(w)
        return exe

    def xml(self, times, command: Command) -> str:
        user = os.environ.get("USERNAME")
        dom = os.environ.get("USERDOMAIN")
        return build_xml(times, [self.gui_python(command.argv[0]), *command.argv[1:]],
                         {"description": self.description(), "workdir": str(self._root),
                          "run_if_missed": command.run_if_missed, "wake": command.wake,
                          "user": f"{dom}\\{user}" if user and dom else user})

    _COMPARE = ("description", "times", "command", "arguments", "workdir", "enabled", "run_if_missed", "wake")

    def install(self, times, command, *, replace_old=False, dry_run=False) -> dict:
        cur = self._read()
        name = self.task()
        mine = [(n, i) for n, i in cur if self._mine(i["description"])]
        old = [n for n, i in cur if not self._mine(i["description"])]
        xml = self.xml(times, command)
        want = parse_xml(xml)
        same = (len(mine) == 1 and mine[0][0].lower() == name.lower()
                and all(mine[0][1][k] == want[k] for k in self._COMPARE))
        changed = not same or bool(replace_old and old)
        if not dry_run and changed:
            if command.log is not None:
                Path(command.log).parent.mkdir(parents=True, exist_ok=True)
            for n, _ in mine:
                if n.lower() != name.lower():                        # 名字的算法变过：旧名字的删掉
                    self._schtasks("/Delete", "/TN", n, "/F", what="删任务")
            if not same:
                self._create(name, xml)
            if replace_old:
                for n in old:
                    self._schtasks("/Delete", "/TN", n, "/F", what="删任务")
        return {"installed": [name], "replaced": [n for n, _ in mine], "old_entries": old,
                "removed_old": old if replace_old else [], "changed": changed, "preview": xml}

    def _create(self, name: str, xml: str) -> None:
        """任务定义写成 UTF-16（带 BOM）的临时文件交给 schtasks /Create /XML（/TR 最长 261 个字、设不了工作目录），用完删掉。"""
        fd, tmp = tempfile.mkstemp(prefix="maapilot-task-", suffix=".xml")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(codecs.BOM_UTF16_LE + xml.encode("utf-16-le"))
            self._schtasks("/Create", "/TN", name, "/XML", tmp, "/F", what="建任务")
        finally:
            fs.safe_unlink(tmp)

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

    def install(self, times, command, *, replace_old=False, dry_run=False) -> dict:
        cur = self._load()
        mine = [e for e in cur if e.ident == self._ident()]
        old = [e for e in cur if e.old]
        keep = [e for e in cur if e.ident != self._ident() and not (replace_old and e.old)]
        new = Entry(_hm(times), " ".join(command.argv), True, self._ident(), enabled=True,
                    settings={"run_if_missed": command.run_if_missed, "wake": command.wake})
        changed = keep + [new] != cur
        if not dry_run and changed:
            self._save(keep + [new])
        return {"installed": [new.ident], "replaced": [e.ident for e in mine], "old_entries": [e.ident for e in old],
                "removed_old": [e.ident for e in old] if replace_old else [], "changed": changed,
                "preview": json.dumps([asdict(e) for e in keep + [new]], ensure_ascii=False)}

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
    """读出来什么都没有；要装 / 删时报环境错误。"""
    name = "none"

    def __init__(self, hint: str):
        self.hint = hint

    def available(self) -> tuple[bool, str | None]:
        return False, self.hint

    def read(self) -> list[Entry]:
        return []

    def _refuse(self):
        raise CliError("no_scheduler", f"这台电脑上设不了定时日常：{self.hint}", exit=ENVIRONMENT,
                       hint="先在对话里手动跑日常（maapilot daily run）")

    def install(self, times, command, *, replace_old=False, dry_run=False) -> dict:
        self._refuse()

    def remove(self, *, dry_run=False) -> dict:
        self._refuse()


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
