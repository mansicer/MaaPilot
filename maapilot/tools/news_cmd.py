"""`maapilot news list / show / told`：官方游戏内公告里的维护和活动日程（国服：官服、B 服；不碰模拟器）。契约见 docs/cli.md「news」。"""
from __future__ import annotations

from maapilot.cliutil import BAD_REQUEST, FAILED, CliError
from maapilot.tools import common


def _official() -> None:
    from maapilot import config
    from maapilot.knowledge import news
    if not news.applies():
        raise CliError("unsupported_server", f"公告只支持国服（官服、B 服；现在是 {config.server()}）", exit=BAD_REQUEST)


def _brief(i: dict) -> dict:
    out = {k: i.get(k) for k in ("cid", "title", "kind", "published", "windows")}
    if i.get("kind") == "维护":
        out.update({"maintenance": i.get("maintenance"), "forced_update": bool(i.get("forced_update"))})
    return out


def listing(refresh: bool = False, days: float = 3) -> dict:
    from maapilot.knowledge import news
    _official()
    d = news.load()
    if refresh or news.stale(d):
        try:
            d = news.refresh()
        except news.UpstreamFormat as e:             # 公告接口的格式变了：已记进 sources.json
            if not d.get("items"):
                from maapilot.knowledge import sources
                raise sources.cli_error(e) from None
            d = {**d, "refresh_error": e.message}
        except Exception as e:                       # noqa: BLE001 - 拉不到就用缓存，并说清楚
            if not d.get("items"):
                raise CliError("network", f"拉不到官方公告：{type(e).__name__}: {e}", exit=FAILED,
                               hint="稍后再试；也可以请用户看游戏里的公告") from None
            d = {**d, "refresh_error": f"{type(e).__name__}: {e}"}
    return {"at": d.get("at"), "refresh_error": d.get("refresh_error"),
            "maintenance_now": news.maintenance_now(d), "upcoming": news.upcoming(d, days=days),
            "due": news.due(d), "lines": news.lines(d, days=days),
            "items": [_brief(i) for i in d.get("items") or [] if i.get("kind") != "其他"],
            "others": [i.get("title") for i in d.get("items") or [] if i.get("kind") == "其他"]}


def show(cid: str) -> dict:
    from maapilot.knowledge import news
    for i in news.load().get("items") or []:
        if i.get("cid") == cid:
            if i.get("text") is None:                 # "其他"类只存了标题：现拉全文
                try:
                    i = news.item_from(news._get(news.ITEM_URL.format(cid=cid)))
                except Exception as e:               # noqa: BLE001
                    raise CliError("network", f"拉不到公告全文：{type(e).__name__}: {e}", exit=FAILED) from None
            return {k: i.get(k) for k in ("cid", "title", "kind", "published", "windows", "maintenance",
                                          "forced_update", "text") if k in i}
    raise CliError("not_found", f"缓存里没有公告 {cid}", exit=BAD_REQUEST, hint="先 maapilot news list --refresh")


def told(keys: list[str]) -> dict:
    from maapilot.knowledge import news
    from maapilot.tools import confirm
    if confirm._unattended():                        # 用户不在场：标成已告知，会话里就不会再提了
        raise CliError("not_allowed", "定时日常叫起的 agent 不能把日程标成已告知（用户还没看到）", exit=BAD_REQUEST,
                       hint="写进你的总结")
    known = {e["key"] for e in news.events()}
    bad = [k for k in keys if k not in known]
    if bad:
        raise CliError("unknown_event", f"不认识的时间点：{'、'.join(bad)}", exit=BAD_REQUEST,
                       hint="用 news list 的 due / upcoming 里的 key")
    return {"told": sorted(keys)}


@common.logged
def cmd_list(args):
    return listing(refresh=args.refresh, days=args.days)


def cmd_show(args):
    return show(args.cid)


def cmd_told(args):
    from maapilot.knowledge import news
    out = told(args.keys)
    news.mark_told(args.keys)
    out["due"] = news.due()
    return out


def register(sub) -> None:
    g = sub.add_parser("news", help="官方公告里的维护、活动日程（国服：官服、B 服）")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("list", help="正在维护吗、接下来几天的维护和活动、到点还没告诉用户的（缓存超过 6 小时会重新拉）")
    p.add_argument("--refresh", action="store_true", help="不管缓存，现在就拉一次")
    p.add_argument("--days", type=float, default=3, help="upcoming 看几天（默认 3）")
    p.set_defaults(func=cmd_list, command="news list")
    p = gs.add_parser("show", help="一条公告的全文")
    p.add_argument("cid", help="公告编号（list 的 items[].cid）")
    p.set_defaults(func=cmd_show, command="news show")
    p = gs.add_parser("told", help="这些时间点已经告诉过用户了（daily watch 不再因为它们结束）")
    p.add_argument("keys", nargs="+", metavar="key", help="list 的 due[].key")
    p.set_defaults(func=cmd_told, command="news told")
