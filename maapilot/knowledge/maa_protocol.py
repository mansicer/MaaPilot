"""MaaCore 任务参数白名单（见 docs/concepts/knowledge.md，决策 D24）。maa-cli 不校验 TOML 里的参数名，写错会静默失效，
所以任务文件在写入/加载前用 validate_params() 核对。

来源：docs.maa.plus/zh-cn/protocol/integration.html。类型用 Python 类型元组表示；`int` 也接受 bool 之外的
整数，`float` 接受 int。注意 Fight 的过期理智药参数：现行名是 `medicine_expire_days`（MaaCore v6.8.0 起）；
`expiring_medicine` 是已弃用的别名，本表仍接受（不报错）；新写的任务文件请用现行名。
"""
from __future__ import annotations

from typing import Any

_INT = (int,)
_NUM = (int, float)
_STR = (str,)
_BOOL = (bool,)
_LIST = (list,)
_DICT = (dict,)

_COMMON = {"enable": _BOOL}

TASK_PARAMS: dict[str, dict[str, tuple[type, ...]]] = {
    "StartUp": {**_COMMON, "client_type": _STR, "start_game_enabled": _BOOL, "account_name": _STR},
    "CloseDown": {**_COMMON, "client_type": _STR},
    "Fight": {
        **_COMMON, "stage": _STR, "medicine": _INT, "medicine_expire_days": _INT, "expiring_medicine": _INT, "stone": _INT,
        "times": _INT, "series": _INT, "drops": _DICT, "report_to_penguin": _BOOL, "penguin_id": _STR,
        "report_to_yituliu": _BOOL, "yituliu_id": _STR, "server": _STR, "client_type": _STR, "DrGrandet": _BOOL,
    },
    "Recruit": {
        **_COMMON, "refresh": _BOOL, "select": _LIST, "confirm": _LIST, "level3_recruitment_permit_reserve": _INT,
        "first_tags": _LIST, "extra_tags_mode": _INT, "times": _INT, "set_time": _BOOL, "expedite": _BOOL,
        "expedite_times": _INT, "skip_robot": _BOOL, "preserve_tags": _LIST, "recruitment_time": _DICT,
        "report_to_penguin": _BOOL, "penguin_id": _STR, "report_to_yituliu": _BOOL, "yituliu_id": _STR, "server": _STR,
    },
    "Infrast": {
        **_COMMON, "mode": _INT, "facility": _LIST, "drones": _STR, "threshold": _NUM, "replenish": _BOOL,
        "dorm_notstationed_enabled": _BOOL, "dorm_trust_enabled": _BOOL, "fiammetta_targets": _LIST,
        "fiammetta_recovery_enabled": _BOOL, "use_pinus_sylvestris": _BOOL, "use_perception_information": _BOOL,
        "use_worldly_plight": _BOOL, "use_abyssal_hunter": _BOOL, "reception_message_board": _BOOL,
        "reception_clue_exchange": _BOOL, "reception_send_clue": _BOOL, "filename": _STR, "plan_index": _INT,
        "continue_training": _BOOL,
    },
    "Mall": {
        **_COMMON, "visit_friends": _BOOL, "shopping": _BOOL, "buy_first": _LIST, "blacklist": _LIST,
        "force_shopping_if_credit_full": _BOOL, "only_buy_discount": _BOOL, "reserve_max_credit": _BOOL,
        "credit_fight": _BOOL, "formation_index": _INT,
    },
    "Award": {**_COMMON, "award": _BOOL, "mail": _BOOL, "recruit": _BOOL, "orundum": _BOOL, "mining": _BOOL,
              "specialaccess": _BOOL, "signinevent": _BOOL},
    "Roguelike": {
        **_COMMON, "theme": _STR, "mode": _INT, "squad": _STR, "roles": _STR, "core_char": _STR, "core_char_list": _LIST,
        "use_support": _BOOL, "use_nonfriend_support": _BOOL, "starts_count": _INT, "difficulty": _INT,
        "stop_at_final_boss": _BOOL, "stop_at_max_level": _BOOL, "investment_enabled": _BOOL, "investments_count": _INT,
        "stop_when_investment_full": _BOOL, "investment_with_more_score": _BOOL, "start_with_elite_two": _BOOL,
        "only_start_with_elite_two": _BOOL, "refresh_trader_with_dice": _BOOL, "first_floor_foldartal": _STR,
        "start_foldartal_list": _LIST, "collectible_mode_start_list": _DICT, "use_foldartal": _BOOL,
        "check_collapsal_paradigms": _BOOL, "double_check_collapsal_paradigms": _BOOL,
        "expected_collapsal_paradigms": _LIST, "monthly_squad_auto_iterate": _BOOL, "monthly_squad_check_comms": _BOOL,
        "deep_exploration_auto_iterate": _BOOL, "collectible_mode_shopping": _BOOL, "collectible_mode_squad": _STR,
        "start_with_seed": _STR, "blackflow_strategy": _STR, "blackflow_cultivation_target": _STR,
        "find_playTime_target": _INT,
    },
    "Copilot": {
        **_COMMON, "filename": _STR, "copilot_list": _LIST, "loop_times": _INT, "use_sanity_potion": _BOOL,
        "formation": _BOOL, "formation_index": _INT, "user_additional": _LIST, "add_trust": _BOOL,
        "ignore_requirements": _BOOL, "support_unit_usage": _INT, "support_unit_name": _STR,
    },
    "SSSCopilot": {**_COMMON, "filename": _STR, "loop_times": _INT},
    "ParadoxCopilot": {**_COMMON, "filename": _STR, "list": _LIST},
    "Depot": {**_COMMON},
    "OperBox": {**_COMMON},
    "Reclamation": {**_COMMON, "theme": _STR, "mode": _INT, "tools_to_craft": _LIST, "clear_store": _BOOL,
                    "increment_mode": _INT, "num_craft_batches": _INT},
    "Custom": {**_COMMON, "task_names": _LIST, "params": _DICT},
    "SingleStep": {**_COMMON, "type": _STR, "subtype": _STR, "details": _DICT},
    "VideoRecognition": {**_COMMON, "filename": _STR},
    "SwitchTheme": {**_COMMON, "themes": _LIST},
}


def _type_ok(value: Any, types: tuple[type, ...]) -> bool:
    if types == _BOOL:
        return isinstance(value, bool)
    if isinstance(value, bool):                      # bool is an int subclass; never accept it as a number
        return False
    return isinstance(value, types)


def _type_name(types: tuple[type, ...]) -> str:
    return "/".join(t.__name__ for t in types)


def validate_params(task: str, params: dict[str, Any]) -> list[str]:
    """核对一个任务的参数：未知任务、未知参数名、类型不符各报一条；空列表表示通过。"""
    spec = TASK_PARAMS.get(task)
    if spec is None:
        return [f"未知任务类型 {task!r}（已知：{', '.join(sorted(TASK_PARAMS))}）"]
    errs: list[str] = []
    for key, value in (params or {}).items():
        types = spec.get(key)
        if types is None:
            errs.append(f"{task}: 未知参数 {key!r}（MaaCore 会静默忽略）")
        elif not _type_ok(value, types):
            errs.append(f"{task}: 参数 {key!r} 应为 {_type_name(types)}，实际 {type(value).__name__}")
    return errs
