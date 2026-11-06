"""用户偏好记录：private/memory/（MEMORY.md 目录 + 一事一文件），不依赖哪个 agent 的记忆功能，换宿主也跟着走。

agent 自己读写这些文件（格式见 AGENTS.md「用户偏好」）；程序只做三件事：
- 启动检查（doctor --brief）带上 MEMORY.md 的目录行，会话一开始 agent 就看到；
- 一次性搬家：还没有 private/memory/MEMORY.md、也没搬过时，把 Claude Code 给这个项目记的自动记忆（以前的位置）复制过来；
- 记上次整理的时间：满一个月后日常结果带上 memory_tidy，agent 按 skill maapilot-memory 整理（`memory tidied` 记下）。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from maapilot import paths
from maapilot.oscompat import fs

INDEX = "MEMORY.md"
MARKER = ".imported-from-claude"          # 搬过一次的记号：来源目录、时间
TIDIED = ".last-tidy"                     # 上次整理的时间
TIDY_DAYS = 30
MAX_LINES = 120


def memory_dir() -> Path:
    return paths.PRIVATE / "memory"


def index_lines() -> list[str]:
    """MEMORY.md 里的条目行（"- …"）；没有 → []。"""
    try:
        text = fs.read_text_utf8(memory_dir() / INDEX)
    except (OSError, ValueError):
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")][:MAX_LINES]


def _files() -> list[Path]:
    return [p for p in memory_dir().glob("*.md") if p.name != INDEX]


def _last_tidy() -> datetime | None:
    """上次整理的时间；没整理过 → 最早的偏好文件的时间（第一次也等满一个月）；没有偏好 → None。"""
    try:
        return datetime.fromisoformat(fs.read_text_utf8(memory_dir() / TIDIED).strip())
    except (OSError, ValueError):
        files = _files()
        return datetime.fromtimestamp(min(p.stat().st_mtime for p in files)).astimezone() if files else None


def tidy_due(now: datetime | None = None) -> bool:
    """离上次整理满一个月了，而且有偏好可整理。"""
    last = _last_tidy()
    return bool(last and _files()) and ((now or datetime.now().astimezone()) - last).days >= TIDY_DAYS


def tidied() -> str:
    at = datetime.now().astimezone().isoformat(timespec="seconds")
    memory_dir().mkdir(parents=True, exist_ok=True)
    fs.atomic_write_text(memory_dir() / TIDIED, at + "\n")
    return at


def import_claude(src: Path | None = None) -> list[str]:
    """搬一次家：private/memory/MEMORY.md 还没有、也没搬过（没有 MARKER）→ 把 Claude Code 自动记忆里的 .md 复制过来。

    目录已经在（setup 先建了、或 agent 先写了别的文件）也照样搬；同名文件不覆盖。原来的不删。
    搬完写 MARKER（来源和时间）：用户以后特意删掉 MEMORY.md 也不会再搬回来。返回复制的文件名。"""
    dst = memory_dir()
    if (dst / INDEX).exists() or (dst / MARKER).exists():
        return []
    if src is None:
        from maapilot.flows.reset import memory_dir as claude_memory_dir
        src = claude_memory_dir()
    files = sorted(p for p in src.glob("*.md") if p.is_file()) if src.is_dir() else []
    if not files:
        return []
    dst.mkdir(parents=True, exist_ok=True)
    copied = []
    for p in files:
        if not (dst / p.name).exists():
            shutil.copy2(p, dst / p.name)
            copied.append(p.name)
    if not (dst / INDEX).exists():
        entries = sorted(p for p in dst.glob("*.md") if p.is_file() and p.name != INDEX)
        (dst / INDEX).write_text("".join(f"- [{p.stem}]({p.name})\n" for p in entries), encoding="utf-8")
    (dst / MARKER).write_text(f"{src}\n{datetime.now().astimezone().isoformat(timespec='seconds')}\n", encoding="utf-8")
    return copied


def cmd_tidied(args) -> dict:
    return {"tidied_at": tidied()}


def register(sub) -> None:
    g = sub.add_parser("memory", help="用户偏好记录")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("tidied", help="记下这次整理偏好的时间（一个月后日常再提醒）")
    p.set_defaults(func=cmd_tidied, command="memory tidied")
