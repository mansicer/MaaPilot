"""用户偏好记录 private/memory/：启动检查带上目录行；第一次从 Claude Code 自动记忆搬过来（复制，不删原来的）。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from maapilot import cli, paths
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


def _prefs(days_old: float) -> None:
    d = memory.memory_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / memory.INDEX).write_text("- [a](a.md)\n", encoding="utf-8")
    (d / "a.md").write_text("x", encoding="utf-8")
    t = (datetime.now() - timedelta(days=days_old)).timestamp()
    os.utime(d / "a.md", (t, t))


def test_tidy_not_due_without_preferences():
    assert memory.tidy_due() is False


def test_first_tidy_waits_a_month_from_oldest_preference():
    _prefs(29)
    assert memory.tidy_due() is False
    _prefs(31)
    assert memory.tidy_due() is True


def test_tidied_quiets_for_a_month(capsys):
    _prefs(90)
    assert cli.main(["memory", "tidied"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["tidied_at"]
    now = datetime.now().astimezone()
    assert memory.tidy_due(now + timedelta(days=29)) is False
    assert memory.tidy_due(now + timedelta(days=30)) is True


def test_bad_tidy_record_falls_back_to_files():
    _prefs(31)
    (memory.memory_dir() / memory.TIDIED).write_text("?", encoding="utf-8")
    assert memory.tidy_due() is True


def test_report_line_only_when_due():
    from maapilot.flows import report
    assert any("maapilot-memory" in ln for ln in report.summary_lines({"memory_tidy": True}))
    assert not any("maapilot-memory" in ln for ln in report.summary_lines({}))


def test_no_claude_memory_no_dir():
    assert memory.import_claude() == [] and not memory.memory_dir().exists() and memory.index_lines() == []


def test_index_built_when_claude_had_none():
    _claude_memory({"a.md": "x"})
    assert memory.import_claude() == ["a.md"] and memory.index_lines() == ["- [a](a.md)"]


def test_imports_into_existing_dir_without_overwriting():
    """setup 之类先建了 private/memory/（还没有 MEMORY.md）也照样搬；已经有的同名文件不覆盖。"""
    _claude_memory({"MEMORY.md": "- [理智液](sanity.md) — 旧的\n", "sanity.md": "旧的", "shop.md": "先不换"})
    dst = memory.memory_dir()
    dst.mkdir(parents=True)
    (dst / "sanity.md").write_text("新的", encoding="utf-8")
    assert memory.import_claude() == ["MEMORY.md", "shop.md"]
    assert (dst / "sanity.md").read_text(encoding="utf-8") == "新的" and (dst / "shop.md").exists()
    assert (dst / memory.MARKER).read_text(encoding="utf-8").startswith(str(reset.memory_dir()))
    assert (reset.memory_dir() / "sanity.md").read_text(encoding="utf-8") == "旧的"          # 原来的不动


def test_index_lists_existing_files_too():
    _claude_memory({"a.md": "x"})
    dst = memory.memory_dir()
    dst.mkdir(parents=True)
    (dst / "b.md").write_text("y", encoding="utf-8")
    assert memory.import_claude() == ["a.md"] and memory.index_lines() == ["- [a](a.md)", "- [b](b.md)"]


def test_imports_only_once_even_if_index_deleted():
    _claude_memory({"a.md": "x"})
    assert memory.import_claude() == ["a.md"]
    (memory.memory_dir() / "MEMORY.md").unlink()                                 # 用户特意删掉目录
    (memory.memory_dir() / "a.md").unlink()
    assert memory.import_claude() == [] and not (memory.memory_dir() / "a.md").exists()


def test_existing_index_blocks_import():
    _claude_memory({"a.md": "x"})
    memory.memory_dir().mkdir(parents=True)
    (memory.memory_dir() / "MEMORY.md").write_text("- [b](b.md)\n", encoding="utf-8")
    assert memory.import_claude() == [] and not (memory.memory_dir() / "a.md").exists()
