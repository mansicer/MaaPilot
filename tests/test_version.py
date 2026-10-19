"""版本号：一处定义（maapilot.__version__，pyproject.toml 从这里读），git 仓库里带上当前提交。"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from maapilot import __version__, cli, version

ROOT = Path(__file__).resolve().parent.parent
SHA = "0123456789abcdef0123456789abcdef01234567"


def test_version_is_single_sourced():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in proj["project"] and "version" in proj["project"]["dynamic"]
    assert proj["tool"]["hatch"]["version"]["path"] == "maapilot/__init__.py"


def _repo(tmp_path, head="ref: refs/heads/main\n"):
    g = tmp_path / ".git"
    (g / "refs" / "heads").mkdir(parents=True)
    (g / "HEAD").write_text(head, encoding="utf-8")
    return g


def test_commit_from_branch_ref(tmp_path):
    g = _repo(tmp_path)
    (g / "refs" / "heads" / "main").write_text(SHA + "\n", encoding="utf-8")
    assert version.commit(tmp_path) == "0123456"
    assert version.info(tmp_path) == {"version": __version__, "commit": "0123456", "text": f"{__version__}（0123456）"}


def test_commit_from_packed_refs_and_detached(tmp_path):
    g = _repo(tmp_path)
    (g / "packed-refs").write_text(f"# pack-refs with: peeled\n{SHA} refs/heads/main\n", encoding="utf-8")
    assert version.commit(tmp_path) == "0123456"
    (g / "HEAD").write_text(SHA[::-1] + "\n", encoding="utf-8")
    assert version.commit(tmp_path) == SHA[::-1][:7]


def test_commit_in_worktree(tmp_path):
    main = tmp_path / "main"
    g = _repo(main)
    (g / "refs" / "heads" / "feat").write_text(SHA, encoding="utf-8")
    wt = g / "worktrees" / "w"
    wt.mkdir(parents=True)
    (wt / "HEAD").write_text("ref: refs/heads/feat\n", encoding="utf-8")
    (wt / "commondir").write_text("../..\n", encoding="utf-8")
    work = tmp_path / "w"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {wt}\n", encoding="utf-8")
    assert version.commit(work) == "0123456"


def test_no_git_is_version_only(tmp_path):
    assert version.commit(tmp_path) is None
    assert version.info(tmp_path)["text"] == __version__
    _repo(tmp_path)                                    # 分支还没有提交
    assert version.commit(tmp_path) is None


def test_cli_version_flag(capsys):
    assert cli.main(["--version"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["command"] == "version" and out["data"]["version"] == __version__
