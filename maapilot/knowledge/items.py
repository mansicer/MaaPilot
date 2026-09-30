"""物品主键（见 docs/concepts/knowledge.md）：内部一律用 item_table 的 id（"4001" 龙门币、"30115" 聚合剂）；中文名只出现在
用户输入解析（resolve_item）和显示（display_name）。数据表复用 gamedata 的懒加载。不碰模拟器。
"""
from __future__ import annotations

from maapilot.knowledge.gamedata import UnknownNameError, _items, _items_by_name

# MAA Depot 能识别的物品类型（Depot.known 用）
DEPOT_ITEM_TYPES = frozenset({"MATERIAL", "CARD_EXP", "GOLD"})
# 货币与凭证（`state show depot` 单列，D144）：按 itemType；采购凭证在数据表里是 MATERIAL，按 id 单列
CURRENCY_ITEM_TYPES = frozenset({"GOLD", "DIAMOND", "DIAMOND_SHD", "HGG_SHD", "LGG_SHD", "SOCIAL_PT", "EPGS_COIN",
                                 "REP_COIN", "RETRO_COIN", "LMTGS_COIN", "TKT_RECRUIT", "TKT_INST_FIN", "TKT_GACHA",
                                 "TKT_GACHA_10", "EXTERMINATION_AGENT"})
CURRENCY_ITEM_IDS = frozenset({"4006"})


def resolve_item(name_or_id: str) -> str:
    """中文名或 id -> id。既不是 id 也不是已知中文名时抛 UnknownNameError（消息含原文）。"""
    key = str(name_or_id).strip()
    if key in _items():
        return key
    it = _items_by_name().get(key)
    if it is None:
        raise UnknownNameError(key)
    return it["itemId"]


def display_name(item_id: str) -> str:
    """id -> 中文名；不认识的 id 原样返回（显示层永远不该炸）。"""
    it = _items().get(item_id)
    return it["name"] if it else item_id


def item_type(item_id: str) -> str | None:
    it = _items().get(item_id)
    return it["itemType"] if it else None


def is_currency(item_id: str) -> bool:
    """货币、凭证、许可（龙门币、合成玉、资质 / 高级 / 采购凭证、信用、寻访凭证……），而不是养成材料。"""
    return item_id in CURRENCY_ITEM_IDS or item_type(item_id) in CURRENCY_ITEM_TYPES


def depot_recognizable(item_id: str) -> bool:
    """该物品是否属于 MAA Depot 识别得到的类型（材料 / 作战记录 / 龙门币）。"""
    return item_type(item_id) in DEPOT_ITEM_TYPES
