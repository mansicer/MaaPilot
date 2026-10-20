"""`maapilot input tap / swipe / back / text / home`：操作模拟器（拿锁）。契约见 docs/cli.md「input」。

坐标是 1280×720 参考坐标系，MaaFramework 按设备真实分辨率换算。这里没有页面拦截：合成玉、源石的防线是 guard 对账。
tap / swipe / back / text 之后不自动截图；home（只导航回首页）最后截一张首页给 agent 看。
"""
from __future__ import annotations

from maapilot.cliutil import BAD_REQUEST, FAILED, CliError
from maapilot.tools import common


def _point(x: int, y: int) -> None:
    from maapilot.crew.screen import check_point
    try:
        check_point(x, y)
    except ValueError as e:
        raise CliError("bad_point", str(e), exit=BAD_REQUEST) from None


def _do(command: str, action) -> dict:
    with common.device(command):
        game = common.connect(ocr=False)
        try:
            action(game)
        except CliError:
            raise
        except Exception as e:
            raise CliError("input_failed", f"操作没做成：{type(e).__name__}: {e}", hint="maapilot doctor") from None
    return {"done": True}


@common.logged
def cmd_tap(args):
    _point(args.x, args.y)
    return _do("input tap", lambda g: g.tap(args.x, args.y, delay=0))


@common.logged
def cmd_swipe(args):
    _point(args.x1, args.y1)
    _point(args.x2, args.y2)
    if not 50 <= args.ms <= 10000:
        raise CliError("bad_arguments", "--ms 要在 50–10000 之间", exit=BAD_REQUEST)
    return _do("input swipe", lambda g: g.swipe(args.x1, args.y1, args.x2, args.y2, duration_ms=args.ms, delay=0))


@common.logged
def cmd_back(args):
    return _do("input back", lambda g: g.key_back())


@common.logged
def cmd_text(args):
    if not args.text:
        raise CliError("bad_arguments", "要输入的文字是空的", exit=BAD_REQUEST)
    return _do("input text", lambda g: g.input_text(args.text))


def go_home(game) -> dict:
    """回首页（nav.ensure_home：关认得的遮挡、左上菜单"首页"、返回键；只导航，不点会花东西的按钮），再截一张。
    → {home, shot, overlays（路上关掉的遮挡，按顺序）, seen（其中要交给 agent 的：活动签到页 / 月签到）[, hint]}。"""
    from maapilot.crew import nav, overlays
    from maapilot.tools import screen
    pos = overlays.mark()
    home = bool(nav.ensure_home(game))
    names = overlays.closed_since(pos)
    out: dict = {"home": home, "shot": None, "overlays": list(dict.fromkeys(names)),
                 "seen": overlays.notable(names)}
    try:
        out["shot"] = screen.capture(game, "home")[1]["path"]
    except Exception:                                    # noqa: BLE001 - 截不了图也照样交代路上关了什么
        pass
    if out["seen"]:
        out["hint"] = ("路上关掉了" + "、".join(overlays.NOTABLE[n] for n in out["seen"])
                       + "（程序只关不领）：看首页截图上的活动入口，按用户偏好进去领签到奖励")
    return out


@common.logged
def cmd_home(args):
    with common.device("input home"):
        game = common.connect(ocr=True)
        try:
            data = go_home(game)
        except CliError:
            raise
        except Exception as e:
            raise CliError("input_failed", f"回首页没做成：{type(e).__name__}: {e}", hint="maapilot screen shot 看看停在哪") from None
    if not data["home"]:
        raise CliError("not_home", "没回到首页", exit=FAILED, data=data,
                       hint="看 shot 截图停在哪页，按 maapilot-recovery 处理")
    return data


def register(sub) -> None:
    g = sub.add_parser("input", help="点击、滑动、返回、输入、回首页")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("tap", help="点一下")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.set_defaults(func=cmd_tap, command="input tap")
    p = gs.add_parser("swipe", help="滑动")
    for a in ("x1", "y1", "x2", "y2"):
        p.add_argument(a, type=int)
    p.add_argument("--ms", type=int, default=500, help="时长（毫秒，默认 500）")
    p.set_defaults(func=cmd_swipe, command="input swipe")
    p = gs.add_parser("back", help="安卓返回键")
    p.set_defaults(func=cmd_back, command="input back")
    p = gs.add_parser("home", help="回首页（只导航；截一张首页，写明路上关掉了哪些弹窗）")
    p.set_defaults(func=cmd_home, command="input home")
    p = gs.add_parser("text", help="往当前输入框输入")
    p.add_argument("text")
    p.set_defaults(func=cmd_text, command="input text")
