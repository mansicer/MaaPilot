"""用户偏好记录：private/memory/（MEMORY.md 目录 + 一事一文件），不依赖哪个 agent 的记忆功能，换宿主也跟着走。

agent 自己读写这些文件（格式见 AGENTS.md「用户偏好」）；程序只做两件事：
- 启动检查（doctor --brief）带上 MEMORY.md 的目录行，会话一开始 agent 就看到；
- 一次性搬家：还没有 private/memory/MEMORY.md、也没搬过时，把 Claude Code 给这个项目记的自动记忆（以前的位置）复制过来。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from maapilot import paths
from maapilot.oscompat import fs

INDEX = "MEMORY.md"
MARKER = ".imported-from-claude"          # 搬过一次的记号：来源目录、时间
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
