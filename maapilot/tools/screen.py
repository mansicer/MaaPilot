"""`maapilot screen shot / ocr`：截图、识别文字（只读：不拿锁，用不开输入服务的只读连接）。契约见 docs/cli.md「screen」。

不拿锁：MAA 或别的命令正在操作模拟器时也能截图看看。连接用 Game(readonly=True)：MaaFramework 不在设备上起
maatouch / minitouch，也不发任何点击，所以不会和正在跑的任务抢输入。

截图按 1280×720 参考坐标系存成 PNG（MaaFramework 已把截图缩到短边 720；不是正好 1280×720 时再缩一次），
放在 private/shots/<YYYYmmdd-HHMMSS>[-<名>].png，输出绝对路径给 agent 用读图工具看。
`screen ocr --image <图>` 不连模拟器，在已有的截图上跑同一套识别（core.ImageGame），排查识别错误、做回归样本用。
"""
from __future__ import annotations

import re
from pathlib import Path

from maapilot import cliutil, paths
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
    with cliutil.device_readonly():
        game = common.connect(ocr=False, readonly=True)
        return capture(game, args.name)[1]


def image_game(path: str):
    """`--image`：不连模拟器，在这张图上识别（和真机同一套 OCR）。"""
    from maapilot.cliutil import ENVIRONMENT
    p = Path(path).expanduser()
    if not p.is_file():
        raise CliError("bad_image", f"没有这个图片文件：{path}", exit=BAD_REQUEST)
    try:
        from maapilot.crew.core import ImageGame, load_image
        img = load_image(p)
    except Exception as e:                           # 不是图片、不是 16:9
        raise CliError("bad_image", f"读不了这张图：{type(e).__name__}: {e}", exit=BAD_REQUEST) from None
    try:
        return ImageGame(img), img, p
    except Exception as e:                           # OCR 模型或资源缺失
        raise CliError("ocr_unavailable", f"OCR 资源加载不了：{e}", hint="maapilot doctor", exit=ENVIRONMENT) from None


@common.logged
def cmd_ocr(args):
    roi = _roi(args.roi)
    if args.find:
        _check_regex(args.find)
    if args.image:
        game, img, p = image_game(args.image)
        return {"path": common.rel(p), "roi": list(roi), "hits": ocr_hits(game, img, roi, args.find)}
    with cliutil.device_readonly():
        game = common.connect(ocr=True, readonly=True)
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
    p.add_argument("--image", metavar="图片", help="不截图、不连模拟器：识别这张已有的截图（排查识别错误用）")
    p.set_defaults(func=cmd_ocr, command="screen ocr")
