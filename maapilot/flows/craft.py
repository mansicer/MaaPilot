"""`maapilot craft <物品> [--count N]`：合成快捷操作。契约见 docs/cli.md。

- 精英材料（加工站配方）：maapilot.crew.craft，只合这一样，中间材料要先各自 craft（`calc craft` 给顺序）；
- 双芯片：制造站（maapilot.crew.manufacture），造完改回原产物；
- 商店兑换的（芯片助剂，花采购凭证）：采购中心（maapilot.crew.shop），确认框上核对总价，对不上不买。
做不成退出码 1（shortcut_failed），agent 按 maapilot-base 在界面上自己做。
"""
from __future__ import annotations

import math

from maapilot.cliutil import BAD_REQUEST, FAILED, CliError
from maapilot.flows._common import game_data_error, rel, run_shortcut


def _offer(item_id: str):
    from maapilot import config
    from maapilot.knowledge import shops
    server = str((config.load() if config.exists() else config.DEFAULTS).get("server") or "Official")
    for s in (server, server.lower()):
        o = shops.offers(s).get(item_id)
        if o:
            return o
    return None


def craft(item: str, count: int) -> dict:
    from maapilot.knowledge import recipes
    from maapilot.knowledge.gamedata import UnknownNameError
    from maapilot.knowledge.items import display_name, resolve_item
    if count < 1:
        raise CliError("bad_arguments", "--count 至少是 1", exit=BAD_REQUEST)
    try:
        iid = resolve_item(item)
        name = display_name(iid)
    except UnknownNameError:
        raise CliError("unknown_name", f"认不出物品「{item}」", hint="用游戏里的中文名", exit=BAD_REQUEST) from None
    except (OSError, ValueError) as e:
        raise game_data_error(e) from None
    args = {"item": name, "count": count}

    if "双芯片" in name:
        def body(g, pr):
            from maapilot.crew import manufacture
            pr.stage = "制造站"
            res = manufacture.make(name, count)
            data = {"item": name, "count": count, "station": "MANUFACTURE", "made": res.get("made"),
                    "original": res.get("original"), "restored": res.get("restored"), "shot": rel(res.get("shot"))}
            if not res.get("restored"):
                raise CliError("not_restored", f"造好了 {res.get('made')} 个{name}，但制造站没改回原产物"
                               f"（{res.get('original') or '读不出原产物'}）", hint="在界面上把制造站改回原产物",
                               exit=FAILED, data=data)
            return data
        return run_shortcut("craft", args, "craft_error", body, needs_game=False)

    offer = _offer(iid)
    if offer is not None:
        def body(g, pr):
            from maapilot.crew import shop
            pr.stage = f"{offer.shop} {offer.tab}"
            try:
                res = shop.buy(name, count, shop=offer.shop, tab=offer.tab, unit_price=offer.price)
            except shop.ShopAborted as e:
                raise CliError("not_bought", f"没买：{e}", hint="什么都没花；看看余额够不够、商品还在不在",
                               exit=FAILED, data={"item": name, "count": count, "balance": e.balance}) from None
            return {"item": name, "count": count, "station": "SHOP", "currency": display_name(offer.currency),
                    "unit_price": offer.price, "balance_before": res.get("before"), "balance_after": res.get("after")}
        return run_shortcut("craft", args, "craft_error", body, needs_game=False)

    try:
        formula = recipes.workshop_by_name().get(name)
    except (OSError, ValueError) as e:
        raise game_data_error(e) from None
    if not formula:
        raise CliError("not_craftable", f"{name} 不能在加工站合成（快捷操作只做精英材料）", exit=BAD_REQUEST,
                       hint="data recipe 看它怎么来")
    times = math.ceil(count / max(1, int(formula.get("count") or 1)))

    def body(g, pr):
        from maapilot.crew import craft as cr
        pr.stage = "去加工站"
        cr.goto_workshop(g)
        pr.stage = f"合成 {name} ×{times}"
        res = cr.craft_once(g, name, times) or {}
        pr.done.append({"item": name, "times": times})
        shot = g.save(f"craft_{name}")
        return {"item": name, "count": count, "times": times, "station": "WORKSHOP", "shot": rel(shot),
                "verified": res.get("verified")}

    return run_shortcut("craft", args, f"craft_error_{name}", body)


def register(sub) -> None:
    p = sub.add_parser("craft", help="合成：加工站精英材料、制造站双芯片、采购中心芯片助剂")
    p.add_argument("item")
    p.add_argument("--count", type=int, default=1)
    p.set_defaults(func=lambda a: craft(a.item, a.count), command="craft")
