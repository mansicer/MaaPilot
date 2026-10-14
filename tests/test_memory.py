"""用户偏好记录 private/memory/：启动检查带上目录行；第一次从 Claude Code 自动记忆搬过来（复制，不删原来的）。"""
from __future__ import annotations

from maapilot import paths
from maapilot.flows import reset
from maapilot.tools import doctor, memory


def _claude_memory(files: dict) -> None:
    d = reset.memory_dir()
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")


def test_first_brief_imports_claude_memory_once():
    _claude_memory({"MEMORY.md": "- [理智液](sanity.md) — 快到期的可以用\n",
                    "sanity.md": "---\nname: sanity\n---\n快到期的可以用（10-05）\n"})
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    m = doctor.memory_brief()
    assert m["imported"] == ["MEMORY.md", "sanity.md"] and m["index"] == ["- [理智液](sanity.md) — 快到期的可以用"]
    assert (reset.memory_dir() / "sanity.md").exists()                         # 原来的不删
    (memory.memory_dir() / "MEMORY.md").write_text("- [理智液](sanity.md) — 都不用\n", encoding="utf-8")
    m = doctor.memory_brief()                                                  # 搬过一次就不再搬
    assert m["imported"] is None and m["index"] == ["- [理智液](sanity.md) — 都不用"]


def test_no_claude_memory_no_dir():
    assert memory.import_claude() == [] and not memory.memory_dir().exists() and memory.index_lines() == []


def test_index_built_when_claude_had_none():
    _claude_memory({"a.md": "x"})
    assert memory.import_claude() == ["a.md"] and memory.index_lines() == ["- [a](a.md)"]
