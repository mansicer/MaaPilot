"""把一张真机截图做成识别回归测试的样本：缩到 1280×720，只留下要识别的几块，其余涂黑（去掉昵称、ID 等个人信息，文件也小）。

    .venv/bin/python tests/fixtures/screens/make.py private/shots/20261011-165659.png home_130 --keep 640 15 640 265

--keep 可以给多次（1280×720 参考坐标，要覆盖被测函数读的所有区域）。写到 tests/fixtures/screens/<名>.png。
做完用读图工具看一眼：留下的区域里不能有昵称、ID、好友名。用法见 CLAUDE_DEV.md「识别回归测试」。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent


def make(src: Path, name: str, keep: list[tuple[int, int, int, int]]) -> Path:
    im = Image.open(src).convert("RGB")
    if im.size != (1280, 720):
        im = im.resize((1280, 720))
    out = Image.new("RGB", im.size)
    for x, y, w, h in keep:
        out.paste(im.crop((x, y, x + w, y + h)), (x, y))
    dst = HERE / f"{name}.png"
    out.save(dst, "PNG", optimize=True)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("name")
    ap.add_argument("--keep", nargs=4, type=int, action="append", required=True, metavar=("X", "Y", "W", "H"))
    a = ap.parse_args()
    print(make(a.src, a.name, [tuple(k) for k in a.keep]))


if __name__ == "__main__":
    main()
