"""`maapilot screen shot / ocr`：截图、识别文字（碰模拟器，只读，拿锁）。契约见 docs/cli.md「screen」。

截图按 1280×720 参考坐标系存成 PNG（MaaFramework 已把截图缩到短边 720；不是正好 1280×720 时再缩一次），
放在 private/shots/<YYYYmmdd-HHMMSS>[-<名>].png，输出绝对路径给 agent 用读图工具看。
"""
from __future__ import annotations

import re
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, CliError
from maapilot.tools import common


def shot_path(name: str | None) -> Path:
    stamp = common.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w.-]+", "_", name).strip("_")[:40] if name else ""
    p = paths.SHOTS / (f"{stamp}-{safe}.png" if safe else f"{stamp}.png")
    n = 2
    while p.exists():
        p = p.with_name(f"{p.stem.rsplit('~', 1)[0]}~{n}.png")
        n += 1
    return p


def save_png(img, path: Path) -> tuple[int, int]:
    """BGR 数组存成 1280×720 的 PNG，返回（宽, 高）。"""
    from PIL import Image
    from maapilot.crew.screen import REF_H, REF_W
    im = Image.fromarray(img[:, :, ::-1])
    if im.size != (REF_W, REF_H):
        im = im.resize((REF_W, REF_H))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG")
    return im.size


def capture(game, name: str | None):
    """截一张、存下，返回 (img, {path, width, height, at})。"""
    img = game.screencap()
    p = shot_path(name)
    w, h = save_png(img, p)
    return img, {"path": common.rel(p), "width": w, "height": h, "at": common.iso()}


def _roi(values) -> tuple[int, int, int, int]:
    from maapilot.crew.screen import FULL_ROI, check_roi
    if not values:
        return FULL_ROI
    roi = tuple(int(v) for v in values)
    try:
        check_roi(roi)
    except ValueError as e:
        raise CliError("bad_roi", str(e), exit=BAD_REQUEST) from None
    return roi


def _check_regex(pattern: str):
    try:
        return re.compile(pattern)
    except re.error as e:
        raise CliError("bad_regex", f"--find 的正则写错了：{e}", exit=BAD_REQUEST) from None


def ocr_hits(game, img, roi, pattern: str | None) -> list[dict]:
    rx = _check_regex(pattern) if pattern else None
    out = []
    for h in game.ocr(roi, img):
        if rx and not rx.search(h.text):
            continue
        x, y, w, hh = (int(v) for v in h.box)
        out.append({"text": h.text, "box": [x, y, w, hh], "center": [x + w // 2, y + hh // 2],
                    "score": round(float(h.score), 3)})
    return out


@common.logged
def cmd_shot(args):
    with common.device("screen shot"):
        game = common.connect(ocr=False)
        return capture(game, args.name)[1]


@common.logged
def cmd_ocr(args):
    roi = _roi(args.roi)
    if args.find:
        _check_regex(args.find)
    with common.device("screen ocr"):
        game = common.connect(ocr=True)
        img, meta = capture(game, args.name or "ocr")
        return {"path": meta["path"], "roi": list(roi), "hits": ocr_hits(game, img, roi, args.find)}


def register(sub) -> None:
    g = sub.add_parser("screen", help="截图、识别文字（只读）")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("shot", help="截一张图存到 private/shots/")
    p.add_argument("--name", help="文件名后缀，如 home")
    p.set_defaults(func=cmd_shot, command="screen shot")
    p = gs.add_parser("ocr", help="截图并识别文字")
    p.add_argument("--roi", nargs=4, type=int, metavar=("X", "Y", "W", "H"), help="只识别这一块（1280×720 坐标）")
    p.add_argument("--find", metavar="正则", help="只返回匹配的文字")
    p.add_argument("--name", help="截图文件名后缀")
    p.set_defaults(func=cmd_ocr, command="screen ocr")
