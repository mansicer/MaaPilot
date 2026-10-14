"""用户偏好记录：private/memory/（MEMORY.md 目录 + 一事一文件），不依赖哪个 agent 的记忆功能，换宿主也跟着走。

agent 自己读写这些文件（格式见 AGENTS.md「用户偏好」）；程序只做两件事：
- 启动检查（doctor --brief）带上 MEMORY.md 的目录行，会话一开始 agent 就看到；
- 一次性搬家：还没有 private/memory/ 时，把 Claude Code 给这个项目记的自动记忆（以前的位置）复制过来。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from maapilot import paths

INDEX = "MEMORY.md"
MAX_LINES = 120


def memory_dir() -> Path:
    return paths.PRIVATE / "memory"


def index_lines() -> list[str]:
    """MEMORY.md 里的条目行（"- …"）；没有 → []。"""
    try:
        text = (memory_dir() / INDEX).read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")][:MAX_LINES]


def import_claude(src: Path | None = None) -> list[str]:
    """private/memory/ 还不存在、Claude Code 的自动记忆里有东西 → 整个复制过来（不删原来的）。返回复制的文件名。"""
    dst = memory_dir()
    if dst.exists():
        return []
    if src is None:
        from maapilot.flows.reset import memory_dir as claude_memory_dir
        src = claude_memory_dir()
    files = sorted(p for p in src.glob("*.md") if p.is_file()) if src.is_dir() else []
    if not files:
        return []
    dst.mkdir(parents=True)
    for p in files:
        shutil.copy2(p, dst / p.name)
    if not (dst / INDEX).exists():
        (dst / INDEX).write_text("".join(f"- [{p.stem}]({p.name})\n" for p in files), encoding="utf-8")
    return [p.name for p in files]
