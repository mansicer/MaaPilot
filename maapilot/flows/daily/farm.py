"""刷理智：刷之前读理智、按理智算时限。"""
from __future__ import annotations

from maapilot import paths
from maapilot.flows.daily.base import TASK_TIMEOUT_MIN
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

# --- 刷理智的时限：按理智算 -------------------------------------------------------------------------------------------

FARM_MIN_PER_FIGHT = 3          # 打一次大约几分钟（config: daily.farm_min_per_fight）
FARM_MARGIN_MIN = 15            # 再多给几分钟（config: daily.farm_timeout_margin_min）
SANITY_FRESH_MIN = 60           # 理智快照多久以内直接用（按 6 分钟回 1 点往上推）
DEFAULT_STAGE_COST = 6          # 认不出关卡时一次按几点理智算（1-7 这类）


def _stage_cost(stage: str) -> tuple[int, bool]:
    try:
        from maapilot.knowledge import stages
        cost = stages.resolve_stage(stage).ap_cost
        if cost:
            return int(cost), True
    except Exception:                                    # noqa: BLE001 - 没有数据表、认不出：按默认
        pass
    return DEFAULT_STAGE_COST, False


def _sanity_estimate(snap: dict | None) -> int | None:
    from maapilot.tools.state import estimate_sanity
    return estimate_sanity(snap)


def _age_min(at: str | None) -> int:
    from maapilot.tools.common import age_min
    return age_min(at) or 0


def read_sanity_for_farm(env: dict) -> tuple[int | None, str]:
    """刷理智前的理智：新的快照（state/sanity.json，1 小时内）直接用；否则 `state sanity`（回首页读右上角）；
    再不行用旧快照推一个。→ (理智或 None, 来源)。"""
    from maapilot.tools import common
    snap = common.read_json(paths.STATE / "sanity.json")
    if snap and _age_min(snap.get("at")) <= SANITY_FRESH_MIN:
        return _sanity_estimate(snap), "snapshot"
    code, env_ = daily.maapilot_call(["state", "sanity"], env, timeout_s=180)
    d = env_.get("data") if isinstance(env_.get("data"), dict) else None
    if code == 0 and d and d.get("current") is not None:
        return int(d["current"]), "state sanity"
    est = _sanity_estimate(snap)
    return est, ("old snapshot" if est is not None else "none")


def farm_timeout(cfg: dict, stage: str, sanity: int | None, source: str) -> tuple[float, dict]:
    """刷理智的时限（秒）：能打几次 = ceil(理智 / 一次的理智)，时限 = 次数 × 每次几分钟 + 余量。
    理智读不到 → 用 task_timeout_min。→ (秒, 说明)。"""
    dcfg = cfg.get("daily") or {}
    fallback = float(dcfg.get("task_timeout_min") or TASK_TIMEOUT_MIN)
    if sanity is None:
        return fallback * 60, {"min": fallback, "basis": "task_timeout_min", "sanity_source": source}
    cost, known = daily._stage_cost(stage)
    fights = max(1, -(-int(sanity) // cost))
    per = float(dcfg.get("farm_min_per_fight") or FARM_MIN_PER_FIGHT)
    margin = float(dcfg.get("farm_timeout_margin_min") if dcfg.get("farm_timeout_margin_min") is not None
                   else FARM_MARGIN_MIN)
    minutes = fights * per + margin
    return minutes * 60, {"min": round(minutes, 1), "basis": "sanity", "sanity": int(sanity), "sanity_source": source,
                          "stage_cost": cost, "stage_cost_known": known, "fights": fights, "min_per_fight": per,
                          "margin_min": margin}
