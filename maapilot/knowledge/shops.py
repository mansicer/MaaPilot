"""商店兑换（见 docs/concepts/knowledge.md「商店兑换」，决策 D135）：养成目标为了补齐材料会自动兑换的商品，按服务器。

数据在 `maapilot.crew/knowledge/shops/<服务器>.yaml`（随代码维护）：游戏数据表里没有商店，价格与限购来自公开资料，文件里注明来源。
没有这个服务器的文件 = 不自动兑换（规划器照旧把这些物品当成拿不到）。

`offers(server)` → {物品 id: Offer}；`recipes(server, caps)` 把商品写成配方的形状（station "SHOP"，原料是货币），
给 `recipes.plan_crafts_ids` 与规划器用：兑换和合成一样是"用库存里的东西换出要的东西"，只是原料是凭证。只读，不碰模拟器。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml

SHOP = "SHOP"                             # 配方的 station：商店兑换
PERIODS = (None, "month")


class ShopDataError(ValueError):
    pass


@dataclass(frozen=True)
class Offer:
    item: str                             # 物品 id
    shop: str                             # 采购中心里的商店（凭证交易所）
    tab: str                              # 分区标签（采购凭证区）
    currency: str                         # 花的物品 id
    price: int                            # 单价
    limit: int | None = None              # 每期限购件数；None = 不限
    period: str | None = None             # 限购周期：month / None
    source: str = ""

    def to_dict(self) -> dict:
        return {"item": self.item, "shop": self.shop, "tab": self.tab, "currency": self.currency, "price": self.price,
                "limit": self.limit, "period": self.period}


def rules_path(server: str) -> Path:
    return Path(__file__).parent / "shops" / f"{server}.yaml"


def parse(raw: dict, where: str = "shops") -> dict[str, Offer]:
    """文件内容 → {物品 id: Offer}；写错抛 ShopDataError（列出全部错误）。"""
    errs: list[str] = []
    out: dict[str, Offer] = {}
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ShopDataError(f"{where}: 要有 version: 1")
    for i, o in enumerate(raw.get("offers") or []):
        at = f"{where}.offers[{i}]"
        if not isinstance(o, dict):
            errs.append(f"{at}: 要是映射")
            continue
        unknown = set(o) - {"item", "shop", "tab", "currency", "price", "limit", "period", "source"}
        if unknown:
            errs.append(f"{at}: 未知的键 {sorted(unknown)}")
        item, cur = str(o.get("item") or ""), str(o.get("currency") or "")
        price, limit, period = o.get("price"), o.get("limit"), o.get("period")
        if not item or not cur or not o.get("shop") or not o.get("tab"):
            errs.append(f"{at}: item / shop / tab / currency 都要写")
        if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
            errs.append(f"{at}: price 要是正整数")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
            errs.append(f"{at}: limit 要是正整数或 null")
        if period not in PERIODS:
            errs.append(f"{at}: period 只能是 month 或 null")
        if (limit is None) != (period is None):
            errs.append(f"{at}: limit 与 period 要同时写或同时省略")
        if item in out:
            errs.append(f"{at}: {item} 重复")
        if not errs:
            out[item] = Offer(item=item, shop=str(o["shop"]), tab=str(o["tab"]), currency=cur, price=price,
                              limit=limit, period=period, source=str(o.get("source") or ""))
    if errs:
        raise ShopDataError("；".join(errs))
    return out


@lru_cache(maxsize=None)
def offers(server: str | None) -> dict[str, Offer]:
    """这个服务器会自动兑换的商品；没有数据文件（或 server 为 None）→ {}。数据文件写错抛 ShopDataError。"""
    if not server:
        return {}
    p = rules_path(server)
    if not p.exists():
        return {}
    return parse(yaml.safe_load(p.read_text(encoding="utf-8")) or {}, where=p.name)


def currencies(server: str | None) -> set[str]:
    """兑换要花的货币（物品 id）。"""
    return {o.currency for o in offers(server).values()}


def as_recipe(o: Offer) -> dict:
    """商品写成配方的形状（同 recipes.recipe()）：station SHOP，一次兑换一件，原料是货币。"""
    from maapilot.knowledge.items import display_name
    return {"item_id": o.item, "name": display_name(o.item), "station": SHOP, "room_level": 0, "count": 1, "lmd": 0,
            "costs": {o.currency: o.price}, "extra_outcome_rate": 0.0, "extra_outcome_group": [],
            "formula_type": None, "tier": None, "offer": o.to_dict()}


def bought_in_period(events, item: str, period_start) -> int:
    """账本里本期已经兑换了多少（`shop.bought` 事件，`at` ≥ period_start）。events 是账本事件的字典序列。"""
    from maapilot.knowledge import calendar
    n = 0
    for e in events or ():
        if e.get("kind") != "shop.bought" or str(e.get("item")) != item:
            continue
        try:
            at = calendar.to_game_tz(datetime.fromisoformat(str(e.get("at"))))
        except (TypeError, ValueError):
            continue
        if at >= period_start:
            n += int(e.get("count") or 0)
    return n


def caps(server: str | None, events, now) -> dict[str, int]:
    """有限购的商品本期还能兑换多少：{物品 id: 件数}（不限购的不在里面）。"""
    from maapilot.knowledge import calendar
    out: dict[str, int] = {}
    for item, o in offers(server).items():
        if o.limit is None:
            continue
        start = calendar.period_start(o.period, now)
        out[item] = max(0, o.limit - bought_in_period(events, item, start))
    return out
