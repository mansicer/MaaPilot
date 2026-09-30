"""Read-only Arknights game knowledge (character/item/stage/recipe data + upgrade-cost math + game calendar
+ MaaCore parameter whitelist + data-table sync). Never touches the emulator; all data is loaded lazily from
maapilot.paths.DATA on first use. Item costs are keyed by item id; use items.display_name to show them.
"""
from __future__ import annotations

from maapilot.knowledge.gamedata import (
    ModuleDataUnavailable,
    UnknownNameError,
    character,
    elite_cost,
    exp_value,
    item,
    item_name,
    level_cost,
    mastery_cost,
    mastery_cost_range,
    module_cost,
    modules_of,
    skill_level_cost,
    upgrade_cost,
)
from maapilot.knowledge.items import depot_recognizable, display_name, resolve_item
from maapilot.knowledge.recipes import is_craftable, obtain, recipe, tree
from maapilot.knowledge.stages import (
    ANNIHILATION_MAA_VALUES,
    AmbiguousStageError,
    StageRef,
    UnknownStageError,
    display,
    is_open_on,
    open_weekdays,
    owning_zone,
    resolve_stage,
    sanity_cost,
    stage,
)

__all__ = [
    "ModuleDataUnavailable", "UnknownNameError", "character", "elite_cost", "exp_value", "item",
    "item_name", "level_cost", "mastery_cost", "mastery_cost_range", "module_cost", "modules_of", "skill_level_cost",
    "upgrade_cost", "depot_recognizable", "display_name", "resolve_item", "is_craftable", "obtain", "recipe", "tree",
    "ANNIHILATION_MAA_VALUES", "AmbiguousStageError", "StageRef", "UnknownStageError", "display", "is_open_on",
    "open_weekdays", "owning_zone", "resolve_stage", "sanity_cost", "stage",
]
