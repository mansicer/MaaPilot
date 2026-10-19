"""MaaPilot 的版本：maapilot.__version__，git 仓库里再带上当前提交（直接读 .git，不调 git 程序；
下载的压缩包里没有 .git 就只有版本号）。"""
from __future__ import annotations

from pathlib import Path

from maapilot import __version__, paths


def _gitdir(root: Path) -> Path | None:
    g = root / ".git"
    if g.is_dir():
        return g
    try:                                               # git worktree：.git 是一行 "gitdir: <路径>"
        line = g.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    d = Path(line[7:].strip())
    return d if d.is_absolute() else (root / d).resolve()


def _ref(gitdir: Path, ref: str) -> str | None:
    common = gitdir
    try:                                               # worktree 的分支存在主仓库里
        common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        pass
    for d in dict.fromkeys((gitdir, common)):
        try:
            return (d / ref).read_text(encoding="utf-8").strip()
        except OSError:
            pass
        try:
            for line in (d / "packed-refs").read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
        except OSError:
            pass
    return None


def commit(root: Path | None = None) -> str | None:
    """当前提交的前 7 位；不是 git 仓库、读不出来 → None。"""
    g = _gitdir(root or paths.ROOT)
    if g is None:
        return None
    try:
        head = (g / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    sha = _ref(g, head[4:].strip()) if head.startswith("ref:") else head
    return sha[:7] if sha and len(sha) >= 7 and all(c in "0123456789abcdef" for c in sha.lower()) else None


def info(root: Path | None = None) -> dict:
    """{version, commit, text}；text 给人看："0.0.1（bff8a50）" 或 "0.0.1"。"""
    c = commit(root)
    return {"version": __version__, "commit": c, "text": f"{__version__}（{c}）" if c else __version__}
