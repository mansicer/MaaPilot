"""现场练度：我们的命令在游戏里读到 / 做成的练度（private/state/live.json），补森空岛的滞后。

森空岛的练度会滞后（10-14：专精 04:58 练完，05:13 还是旧的，几小时后才更新；社区也有一天不更新的报告），
而我们刚做完培养时最要用练度（标完成、算还缺什么）。练度只会涨，所以 `overlay()` 按项取快照和现场读数里高的：
精英化与等级连起来比（精二 1 级 > 精一 80 级），技能等级、各技能专精各自取高。

只记可靠的读数：MAA 干员培养的 AlreadySatisfied / Completed、`op upgrade` 做完核对过的、`state op`。
每项是"至少到了这里"（下界）。快照追上来的条目在下次记录时清掉；记下一天后、更晚的快照还比它低的，
当成可能读错了：不再采用，`suspects()` 列出来（doctor --brief 告诉 agent）。
"""
from __future__ import annotations

from datetime import timedelta

from maapilot import paths
from maapilot.tools import common

SUSPECT_AFTER = timedelta(hours=24)


def _path():
    return paths.STATE / "live.json"


def load() -> dict:
    return dict((common.read_json(_path()) or {}).get("operators") or {})


def _snap_mastery(owned: dict, k: int) -> int | None:
    sk = next((x for x in owned.get("skills") or [] if x.get("index") == k), None)
    return None if sk is None or sk.get("mastery") is None else int(sk["mastery"])


def _behind(owned: dict | None, live: dict) -> list[str]:
    """快照比现场读数低的项（快照没有这名干员 → 全部）。"""
    owned = owned or {}
    out = []
    if live.get("elite") is not None:
        ce = owned.get("elite")
        if ce is None or (int(ce), int(owned.get("level") or 1)) < (live["elite"], live.get("level") or 1):
            out.append("elite")
    if live.get("skill_level") is not None and (owned.get("skill_level") is None
                                                or int(owned["skill_level"]) < live["skill_level"]):
        out.append("skill_level")
    for k, m in (live.get("mastery") or {}).items():
        cur = _snap_mastery(owned, int(k))
        if cur is None or cur < m:
            out.append(f"mastery{k}")
    return out


def _suspect(entry: dict, box_at: str | None) -> bool:
    at, snap_at = common.parse_iso(entry.get("at")), common.parse_iso(box_at)
    return bool(at and snap_at and snap_at - at > SUSPECT_AFTER)


def record(name: str, *, source: str, elite: int | None = None, level: int | None = None,
           skill_level: int | None = None, mastery: dict[int, int] | None = None) -> None:
    """记一条现场读数（下界）。和已有的按项取高；顺手清掉快照已经追上的条目。写不了不影响命令。"""
    try:
        from maapilot.tools.calc import raw_box_snapshot
        ops = load()
        e = dict(ops.get(name) or {})
        if elite is not None and (elite, level or 1) > (e.get("elite", -1), e.get("level") or 1):
            e["elite"], e["level"] = elite, level
        if skill_level is not None and skill_level > (e.get("skill_level") or 0):
            e["skill_level"] = skill_level
        m = {str(k): v for k, v in (e.get("mastery") or {}).items()}
        for k, v in (mastery or {}).items():
            if v > m.get(str(k), -1):
                m[str(k)] = v
        if m:
            e["mastery"] = m
        e.update(at=common.iso(), source=source)
        ops[name] = e
        box, box_at = raw_box_snapshot()
        snap_at = common.parse_iso(box_at)
        for n in list(ops):
            at = common.parse_iso(ops[n].get("at"))
            if snap_at and at and snap_at >= at and not _behind(box.get(n), ops[n]):
                del ops[n]
        common.write_json(_path(), {"operators": ops})
    except Exception:                                   # noqa: BLE001 - 记录是附带的
        pass


def overlay(box: dict, box_at: str | None) -> dict:
    """快照 operators + 现场读数（按项取高）。用到了现场读数的干员带 `live: {at, source, parts}`。"""
    out = dict(box)
    for name, live in load().items():
        if _suspect(live, box_at):
            continue
        parts = _behind(box.get(name), live)
        if not parts:
            continue
        o = dict(box.get(name) or {})
        if "elite" in parts:
            o["elite"], o["level"] = live["elite"], live.get("level") or 1
        if "skill_level" in parts:
            o["skill_level"] = live["skill_level"]
        skills = [dict(s) for s in o.get("skills") or []]
        for k, m in (live.get("mastery") or {}).items():
            if f"mastery{k}" not in parts:
                continue
            sk = next((s for s in skills if s.get("index") == int(k)), None)
            if sk is None:
                skills.append({"index": int(k), "mastery": m})
            else:
                sk["mastery"] = m
        if skills:
            o["skills"] = sorted(skills, key=lambda s: s.get("index") or 0)
        o["live"] = {"at": live.get("at"), "source": live.get("source"), "parts": parts}
        out[name] = o
    return out


def suspects() -> list[dict]:
    """记下一天后、更晚的快照还比它低的现场读数：[{name, at, source, parts}]。"""
    from maapilot.tools.calc import raw_box_snapshot
    box, box_at = raw_box_snapshot()
    out = []
    for name, live in load().items():
        parts = _behind(box.get(name), live)
        if parts and _suspect(live, box_at):
            out.append({"name": name, "at": live.get("at"), "source": live.get("source"), "parts": parts})
    return out


# --- 从命令结果里取 ------------------------------------------------------------------------

def from_progress(name: str, plan: dict, rows: list[dict]) -> None:
    """op progress 的结果 → 记录。MaaCore 回的专精数：AlreadySatisfied 是现场读到的当前等级；
    Completed / PrerequisiteTraining 是这次开练的等级（还没练完），只说明至少到了前一级。"""
    kw: dict = {"mastery": {}}
    for r in rows:
        if r.get("result") != "Success":
            continue
        step, detail = r.get("step") or "", r.get("detail")
        if step.startswith("精英化") and plan.get("elite"):
            kw["elite"] = plan["elite"]
            if detail == "Completed":
                kw["level"] = 1
        elif step.startswith("技能升到") and plan.get("skill_level"):
            kw["skill_level"] = plan["skill_level"]
        elif "专" in step and r.get("skill_mastery"):
            for i, v in enumerate(r["skill_mastery"]):
                if v:
                    kw["mastery"][i + 1] = v if detail == "AlreadySatisfied" else v - 1
    kw["mastery"] = {k: v for k, v in kw["mastery"].items() if v > 0} or None
    if any(v is not None for v in kw.values()):
        record(name, source="op progress", **kw)
