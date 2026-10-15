"""定时日常的调度后端：macOS / Linux 用 cron；Windows 的任务计划程序还没接上（P1），先给一个"不可用"的后端，
让 schedule / unschedule 报清楚的环境错误，reset / doctor / daily status / --brief 照常跑（当作"没设定时"）。

后端只管"本项目的定时条目"：装、读、删。什么时间、执行什么（`Command`）由 flows/daily/cron.py 决定。
- cron：crontab 里本项目的行带标记 `# maapilot-v2 <仓库路径>`，只增删这些行；旧版 MaaPilot 的行（`maapilot job tick`、
  `bin/daily.sh`）列为 old，带 replace_old 才删。测试用 MAAPILOT_CRONTAB 换掉 crontab 程序。
- fake：MAAPILOT_SCHEDULER=fake:<json 路径>，条目存在一个 JSON 文件里（测试用，各系统一样）。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from maapilot import paths
from maapilot.cliutil import ENVIRONMENT, CliError
from maapilot.oscompat import MACOS, WINDOWS

TAG = "# maapilot-v2"
OLD_MARKS = ("maapilot job tick", "bin/daily.sh")       # 旧版 MaaPilot 的定时任务
_CRON_HM = re.compile(r"^\s*(\d{1,2})\s+(\d{1,2}(?:,\d{1,2})*)\s+\*\s+\*\s+\*\s")
SCHEDULER_ENV = "MAAPILOT_SCHEDULER"
WINDOWS_TODO = "Windows 任务计划程序还没接上（下一步做）"

# runner(argv, input, timeout) -> (退出码, stdout, stderr)；doctor 换成走它的 Probe
Runner = Callable[[list, "str | None", float], "tuple[int, str, str]"]


@dataclass
class Command:
    """定时要执行的命令：argv、额外的环境变量（按顺序）、输出追加到哪个日志。"""
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    log: Path | None = None


@dataclass
class Entry:
    times: list[str]          # 排好序的 "HH:MM"
    command: str              # 执行什么（给人看）
    mine: bool                # 本项目（这个仓库）的
    ident: str                # cron 的整行 / 任务名
    old: bool = False         # 旧版 MaaPilot 的定时任务


class Backend(Protocol):
    name: str

    def available(self) -> tuple[bool, str | None]: ...
    def read(self) -> list[Entry]: ...
    def install(self, times: list[tuple[int, int]], command: Command, *, replace_old: bool = False,
                dry_run: bool = False) -> dict: ...
    def remove(self, *, dry_run: bool = False) -> dict: ...


def _hm(times: list[tuple[int, int]]) -> list[str]:
    return [f"{h:02d}:{m:02d}" for h, m in times]


def _default_run(argv: list, input: str | None, timeout: float) -> tuple[int, str, str]:
    try:
        from maapilot.oscompat import procs
        p = procs.run_text(argv, timeout, input=input)
    except ImportError:
        p = subprocess.run(argv, input=input, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout or "", p.stderr or ""


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
        def q(s) -> str:
            return shlex.quote(str(s)).replace("%", r"\%")    # crontab 里 % 是换行
        envs = " ".join(f"{k}={q(v)}" for k, v in command.env.items())
        cmd = " ".join(q(a) for a in command.argv)
        if command.log is not None:
            cmd += f" >> {q(command.log)} 2>&1"
        cmd = f"{envs} {cmd}" if envs else cmd
        return [f"{m} {h} * * * {cmd} {TAG} {paths.ROOT}" for h, m in times]

    # crontab 程序
    def read_text(self) -> str:
        try:
            rc, out, err = self.run([self.program(), "-l"], None, 30)
        except (OSError, subprocess.SubprocessError) as e:
            raise CliError("no_crontab", f"读不了 crontab：{e}", exit=ENVIRONMENT) from None
        if rc != 0:
            if "no crontab" in (err or "").lower():
                return ""
            raise CliError("no_crontab", f"读不了 crontab：{(err or '').strip()}", exit=ENVIRONMENT)
        return out

    def write_text(self, text: str) -> None:
        try:
            rc, _, err = self.run([self.program(), "-"], text, 60)
        except (OSError, subprocess.SubprocessError) as e:
            raise CliError("crontab_failed", f"写不了 crontab：{e}", exit=ENVIRONMENT) from None
        if rc != 0:
            raise CliError("crontab_failed", f"写不了 crontab：{(err or '').strip()}",
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


# --- 假后端（测试） ----------------------------------------------------------------------------------------------------

class FakeBackend:
    """条目存在 JSON 文件里：[{times, command, mine, ident, old}]。本项目的条目 ident 是 `maapilot-daily <ROOT>`。"""
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
        return f"maapilot-daily {paths.ROOT}"

    def read(self) -> list[Entry]:
        return [e for e in self._load() if e.ident == self._ident() or e.old]

    def install(self, times, command, *, replace_old=False, dry_run=False) -> dict:
        cur = self._load()
        mine = [e for e in cur if e.ident == self._ident()]
        old = [e for e in cur if e.old]
        keep = [e for e in cur if e.ident != self._ident() and not (replace_old and e.old)]
        new = Entry(_hm(times), " ".join(command.argv), True, self._ident())
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


# --- 没有可用的调度（Windows，任务计划程序接上之前） ---------------------------------------------------------------------

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
        raise CliError("no_scheduler", f"这台电脑上还设不了定时日常：{self.hint}", exit=ENVIRONMENT,
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
        return Unavailable(WINDOWS_TODO)
    return CronBackend(run)
