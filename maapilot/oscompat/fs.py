"""文件：一律 UTF-8；原子写在 Windows 上遇到文件被占着（杀毒、索引、别的进程开着）时重试；路径比较认大小写和分隔符的差别。"""
from __future__ import annotations

import os
import time
from pathlib import Path

from maapilot.oscompat import WINDOWS

RETRIES = 10
RETRY_S = 0.1
_path = os.path                   # 路径比较按本机规则；测试换成 ntpath 测 Windows 的写法


def _retry(fn):
    for i in range(RETRIES):
        try:
            return fn()
        except PermissionError:
            if not WINDOWS or i == RETRIES - 1:
                raise
            time.sleep(RETRY_S)


class BadEncoding(ValueError):
    """文件不是 UTF-8。是 ValueError（读 JSON 时照旧当"读不到"）；cli 遇到会照常报 internal_error，所以带上能看懂的话。"""

    def __init__(self, p, err: UnicodeDecodeError):
        super().__init__(f"{p} 不是 UTF-8 编码（{err.reason}，第 {err.start} 字节）：用编辑器另存为 UTF-8 再试")
        self.path = p


def read_text_utf8(p: Path | str) -> str:
    """读 UTF-8 文本，文件开头的 BOM（Windows 记事本、PowerShell 存的）去掉；换行统一成 \n。
    不是 UTF-8 → BadEncoding；不存在 → FileNotFoundError。"""
    try:
        return Path(p).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        raise BadEncoding(p, e) from None


def newline_of(p: Path | str, default: str = "\n") -> str:
    """文件现在用的换行（看第一个换行）："\r\n" 或 "\n"；文件不在或没有换行 → default。改写时照原样写回。"""
    try:
        with open(p, "rb") as f:
            head = f.read(65536)
    except OSError:
        return default
    i = head.find(b"\n")
    if i < 0:
        return default
    return "\r\n" if i > 0 and head[i - 1:i] == b"\r" else "\n"


def atomic_write_text(p: Path | str, text: str, *, newline: str | None = "\n") -> None:
    """同目录临时文件 + 替换，不留半截文件。text 里用 \n；newline 是写到磁盘上的换行，默认 "\n"（各系统写出一样的文件），
    要保留原文件的换行就传 newline_of(p)。临时文件以点开头，不会被按扩展名找文件的地方（*.json、*.md）看到。"""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
        _retry(lambda: os.replace(tmp, p))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def atomic_replace(src: Path | str, dst: Path | str) -> None:
    """os.replace，Windows 上目标被占着时重试。"""
    _retry(lambda: os.replace(src, dst))


def safe_unlink(p: Path | str) -> None:
    """删文件；不存在不算错，Windows 上被占着时重试。"""
    def go():
        try:
            Path(p).unlink()
        except FileNotFoundError:
            pass
    _retry(go)


def norm(p) -> str:
    """比较用的路径：去掉 \\\\?\\ 前缀、规范化、Windows 上不分大小写和分隔符。"""
    s = str(p)
    if s.startswith("\\\\?\\"):
        s = s[4:]
    return _path.normcase(_path.normpath(s))


def same_path(a, b) -> bool:
    return norm(a) == norm(b)


def make_dir_link(link: Path, target: Path) -> str:
    """让 link 指向目录 target：macOS / Linux 用符号链接；Windows 用目录联接（不要管理员权限），不行就复制一份。
    → "symlink" / "junction" / "copy"。link 已存在（文件或空链接）时先删掉。"""
    import shutil
    link, target = Path(link), Path(target)
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    if not WINDOWS:
        link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
        return "symlink"
    try:
        import _winapi
        _winapi.CreateJunction(str(target.resolve()), str(link))
        return "junction"
    except (ImportError, OSError):
        shutil.copytree(target, link)
        return "copy"


def imread(p: Path | str):
    """cv2.imread 在 Windows 上读不了带中文的路径：先读字节再解码。读不到 → None。"""
    import cv2
    import numpy as np
    try:
        data = np.fromfile(str(p), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite(p: Path | str, img, ext: str | None = None) -> bool:
    """cv2.imwrite 在 Windows 上写不了带中文的路径：先编码再写字节。格式按扩展名（或 ext，如 ".png"）。"""
    import cv2
    p = Path(p)
    ok, buf = cv2.imencode(ext or p.suffix or ".png", img)
    if not ok:
        return False
    buf.tofile(str(p))
    return True
