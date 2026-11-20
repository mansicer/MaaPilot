"""下载下来的东西和上游公布的哈希对一遍：maa-cli、adb（`toolchain`）、单独装的 MaaCore（`crew/maacore`）、游戏数据表（`knowledge/datasync`）。

哈希从哪来：
- maa-cli 实测版本、adb：写在 `toolchain` 里（发版前更新），装的时候不用联网查；
- maa-cli 别的版本、MaaCore：GitHub 发布信息里每个文件的 `digest`（`sha256:…`）和 `size`；
- 游戏数据表：GitHub 目录列表里的 git blob sha。

对不上的文件不留：调用方删掉，会被运行的（程序、动态库）停下报错，数据表保留本地旧的。
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path

RELEASE_API = "https://api.github.com/repos/{repo}/releases/tags/{tag}"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Mismatch(RuntimeError):
    """文件和公布的哈希 / 大小对不上。"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha(data: bytes) -> str:
    """git 给文件内容算的 sha（GitHub 目录列表、blob 接口里的 `sha`）。"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def parse_digest(digest: object) -> str | None:
    """GitHub 发布文件的 `digest`（"sha256:<64 位十六进制>"）→ 十六进制串；没有、不是 sha256、格式不对 → None。"""
    if not isinstance(digest, str) or not digest.lower().startswith("sha256:"):
        return None
    h = digest.split(":", 1)[1].strip().lower()
    return h if SHA256_RE.match(h) else None


def check_file(path: Path, sha256: str, size: int | None = None) -> None:
    """path 的大小（给了才查）和 sha256 对不上 → Mismatch。不删文件，调用方处理。"""
    got_size = path.stat().st_size
    if size is not None and got_size != size:
        raise Mismatch(f"{path.name} 大小不对：应为 {size} 字节，下到的是 {got_size} 字节（多半没下完）")
    got = sha256_file(path)
    if got != sha256.lower():
        raise Mismatch(f"{path.name} 的 sha256 和公布的对不上：应为 {sha256.lower()}，下到的是 {got}")


def _get_json(url: str, timeout: float = 30) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "maapilot", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def asset_checksum(asset: dict) -> tuple[str, int | None] | None:
    """发布信息里的一个文件 → (sha256, 大小)；没有 digest → None。"""
    h = parse_digest(asset.get("digest"))
    if h is None:
        return None
    size = asset.get("size")
    return h, size if isinstance(size, int) and size > 0 else None


def release_asset_checksum(repo: str, tag: str, name: str, *, get=_get_json) -> tuple[str, int | None] | None:
    """GitHub 上 repo 的 tag 这次发布里名叫 name 的文件 → (sha256, 大小)；发布里没有这个文件、没有 digest → None。
    网络不通、被限流抛 OSError（urllib 的错误都是）。get：测试换成假的。"""
    rel = get(RELEASE_API.format(repo=repo, tag=tag))
    assets = rel.get("assets") if isinstance(rel, dict) else None
    for a in assets or []:
        if isinstance(a, dict) and a.get("name") == name:
            return asset_checksum(a)
    return None
