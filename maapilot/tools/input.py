"""`maapilot input tap / swipe / drag / press / seq / back / text / home`：操作模拟器（拿锁）。契约见 docs/cli.md「input」。

坐标是 1280×720 参考坐标系，MaaFramework 按设备真实分辨率换算。这里没有页面拦截：合成玉、源石的防线是 guard 对账。
tap / swipe / back / text 之后不自动截图；home（只导航回首页）最后截一张首页给 agent 看。
seq 在一次连接里按顺序做几步：每条命令要重连（1–2 秒），放下干员后马上划朝向这类接不上的操作用它。
"""
from __future__ import annotations

import time

from maapilot.cliutil import BAD_REQUEST, FAILED, CliError
from maapilot.tools import common

SEQ_MAX = 20
# 动词 → (坐标个数, 后面可选的毫秒参数：(名字, 默认, 最小, 最大)…)；wait 的毫秒必填
STEPS = {
    "tap": (2, ()),
    "swipe": (4, (("ms", 500, 50, 10000),)),
    "drag": (4, (("hold", 200, 0, 5000), ("ms", 300, 50, 5000))),
    "press": (2, (("ms", 1000, 100, 10000),)),
    "back": (0, ()),
    "wait": (0, (("ms", None, 0, 10000),)),
}


def _point(x: int, y: int) -> None:
    from maapilot.crew.screen import check_point
    try:
        check_point(x, y)
    except ValueError as e:
        raise CliError("bad_point", str(e), exit=BAD_REQUEST) from None


def _step(words: list[str]) -> tuple[str, list[int]]:
    """一步 → (动词, 数)；写错了 ValueError（话是给 agent 看的），坐标出界 CliError（bad_point）。"""
    if not words or words[0] not in STEPS:
        raise ValueError(f"不认识的动作 {words[:1]}（可以用 {' / '.join(STEPS)}）")
    verb, rest = words[0], words[1:]
    npts, opts = STEPS[verb]
    if not npts <= len(rest) <= npts + len(opts):
        raise ValueError(f"{verb} 要 {npts} 个坐标数" + (f"，后面可选 {'、'.join(o[0] for o in opts)}（毫秒）" if opts else ""))
    try:
        nums = [int(w) for w in rest]
    except ValueError:
        raise ValueError(f"{verb} 后面要写整数：{' '.join(rest)}") from None
    for i in range(0, npts, 2):
        _point(nums[i], nums[i + 1])
    for i, (name, default, lo, hi) in enumerate(opts):
        if npts + i >= len(nums):
            if default is None:
                raise ValueError(f"{verb} 要写{name}（毫秒）")
            nums.append(default)
        elif not lo <= nums[npts + i] <= hi:
            raise ValueError(f"{verb} 的 {name} 要在 {lo}–{hi} 毫秒之间")
    return verb, nums


def _run(game, verb: str, n: list[int]) -> None:
    if verb == "tap":
        game.tap(*n, delay=0)
    elif verb == "swipe":
        game.swipe(*n[:4], duration_ms=n[4], delay=0)
    elif verb == "drag":
        game.drag(*n[:4], hold_ms=n[4], duration_ms=n[5], delay=0)
    elif verb == "press":
        game.long_press(*n, delay=0)
    elif verb == "back":
        game.key_back()
    else:
        time.sleep(n[0] / 1000)


def _checked(words: list[str]) -> tuple[str, list[int]]:
    try:
        return _step(words)
    except ValueError as e:
        raise CliError("bad_arguments", str(e), exit=BAD_REQUEST) from None


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
def cmd_drag(args):
    step = _checked(["drag", *map(str, (args.x1, args.y1, args.x2, args.y2, args.hold, args.ms))])
    return _do("input drag", lambda g: _run(g, *step))


@common.logged
def cmd_press(args):
    step = _checked(["press", str(args.x), str(args.y), str(args.ms)])
    return _do("input press", lambda g: _run(g, *step))


@common.logged
def cmd_seq(args):
    if not 1 <= len(args.steps) <= SEQ_MAX:
        raise CliError("bad_arguments", f"一次 1–{SEQ_MAX} 步", exit=BAD_REQUEST)
    steps = []
    for i, raw in enumerate(args.steps, 1):
        try:
            steps.append(_step(raw.split()))
        except ValueError as e:
            raise CliError("bad_arguments", f"第 {i} 步「{raw}」：{e}", exit=BAD_REQUEST) from None
        except CliError as e:                            # 坐标出界（bad_point）
            raise CliError(e.code, f"第 {i} 步「{raw}」：{e.message}", exit=e.exit) from None

    def action(game):
        for i, step in enumerate(steps):
            try:
                _run(game, *step)
            except Exception as e:
                raise CliError("input_failed", f"第 {i + 1} 步「{args.steps[i]}」没做成：{type(e).__name__}: {e}",
                               hint="maapilot screen shot 看看做到哪了", data={"steps": len(steps), "done": i}) from None
    _do("input seq", action)
    return {"done": True, "steps": len(steps)}


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
    if out["seen"]:
        out["hint"] = ("路上关掉了" + "、".join(overlays.NOTABLE[n] for n in out["seen"])
                       + "（程序只关不领）：看首页截图上的活动入口，按用户偏好进去领签到奖励")
    try:
        out["shot"] = screen.capture(game, "home")[1]["path"]
    except Exception as e:                               # noqa: BLE001 - 截图是这条命令要交的东西：截不了就算没做成
        raise CliError("input_failed", f"回首页后截不了图：{type(e).__name__}: {e}", exit=FAILED, data=out,
                       hint="maapilot screen shot 再截一次，看首页的活动入口") from None
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
    g = sub.add_parser("input", help="点击、滑动、拖动、长按、连续几步、返回、输入、回首页")
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
    p = gs.add_parser("drag", help="按住、拖到终点再松开（部署干员、拖道具）")
    for a in ("x1", "y1", "x2", "y2"):
        p.add_argument(a, type=int)
    p.add_argument("--hold", type=int, default=200, help="起点按住多久再开始拖（毫秒，默认 200）")
    p.add_argument("--ms", type=int, default=300, help="拖过去用多久（毫秒，默认 300）")
    p.set_defaults(func=cmd_drag, command="input drag")
    p = gs.add_parser("press", help="长按")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--ms", type=int, default=1000, help="按多久（毫秒，默认 1000）")
    p.set_defaults(func=cmd_press, command="input press")
    p = gs.add_parser("seq", help="一次连接里按顺序做几步，步与步之间不停（要停写 wait）")
    p.add_argument("steps", nargs="+", metavar="步",
                   help='每步一个引号："tap X Y" "swipe X1 Y1 X2 Y2 [ms]" "drag X1 Y1 X2 Y2 [hold] [ms]" '
                        '"press X Y [ms]" "back" "wait ms"')
    p.set_defaults(func=cmd_seq, command="input seq")
    p = gs.add_parser("back", help="安卓返回键")
    p.set_defaults(func=cmd_back, command="input back")
    p = gs.add_parser("home", help="回首页（只导航；截一张首页，写明路上关掉了哪些弹窗）")
    p.set_defaults(func=cmd_home, command="input home")
    p = gs.add_parser("text", help="往当前输入框输入")
    p.add_argument("text")
    p.set_defaults(func=cmd_text, command="input text")
