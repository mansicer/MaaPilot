"""命令记录（private/log/<日期>.jsonl）里的 `result`：关键结果的一小段摘要，事后查"当时读到了什么"不用翻对话。

`summarize(command, data)` 按命令挑几项（不放整个 data，一行要小）；没有摘要的命令返回 None。
这里也管"什么不能记"：森空岛 token、账号的 uid、昵称一律不进记录（`scrub_args`、`safe_argv`、`safe_text`）。
"""
from __future__ import annotations

import re

# 参数里这些名字的值不记（森空岛 token、角色 uid 等账号信息）
SECRET_KEYS = {"token", "uid", "nickname", "nick_name", "password", "phone", "cred", "did"}
# 这几组命令的命令行参数、出错原话一律不记（token 可能被误写在命令行上，出错原话里可能带账号信息）
SECRET_COMMANDS = ("skland", "state skland")
HIDDEN = "<略>"
# 长得像 token 的一段：20 个以上的 base64 字符，大小写字母和数字都有（路径、关卡名、物品名不会这样）
_TOKENISH = re.compile(r"(?<![\w/.+=-])[A-Za-z0-9+/=_-]{20,}(?![\w/.+=-])")


def _tokenish(s: str) -> bool:
    return bool(re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) and re.search(r"\d", s)) and not s.startswith("/")


def scrub_text(s: str) -> str:
    return _TOKENISH.sub(lambda m: HIDDEN if _tokenish(m.group(0)) else m.group(0), s)


def secret_command(command: str | None) -> bool:
    c = command or ""
    return any(c == p or c.startswith(p + " ") for p in SECRET_COMMANDS)


def scrub_args(args: dict | None) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if k in SECRET_KEYS:
            continue
        out[k] = scrub_text(v) if isinstance(v, str) else v
    return out


def safe_argv(argv: list[str]) -> list[str]:
    """命令行参数去掉不能记的：skland 组只留命令名；`--uid X`、`--token=X` 的值换成 <略>；像 token 的整段换掉。"""
    if argv and argv[0] == "skland" or argv[:2] == ["state", "skland"]:
        head = argv[:2] if len(argv) > 1 and re.fullmatch(r"[a-z]+", argv[1]) else argv[:1]   # 只留子命令名
        return head + ([HIDDEN] if len(argv) > len(head) else [])
    out, hide = [], False
    for a in argv:
        if hide:
            out.append(HIDDEN)
            hide = False
            continue
        key = a.lstrip("-").split("=", 1)[0].replace("-", "_")
        if a.startswith("--") and key in SECRET_KEYS:
            if "=" in a:
                out.append(a.split("=", 1)[0] + "=" + HIDDEN)
            else:
                out.append(a)
                hide = True
            continue
        out.append(scrub_text(a))
    return out


def safe_text(command: str | None, text: str | None, argv: list[str] | None = None) -> str | None:
    """出错原话：森空岛的命令不记原话；别的把命令行里被藏起来的那几段、像 token 的字样换掉。"""
    if text is None:
        return None
    if secret_command(command) or (argv and argv[0] == "skland"):
        return None
    if argv:
        for a, b in zip(argv, safe_argv(argv)):
            if a != b and len(a) >= 4:
                text = text.replace(a, HIDDEN)
    return scrub_text(text)


# --- 各命令的摘要 ------------------------------------------------------------------------------

def _pick(d: dict, *keys) -> dict:
    return {k: d[k] for k in keys if d.get(k) is not None and d.get(k) is not False}


def _sanity(d):
    out = _pick(d, "current", "max", "source", "suspect", "estimate")
    if d.get("skland_error"):
        out["skland_error"] = True
    return out


def _depot(d):
    counts = {str(i.get("id")): i.get("count") for i in d.get("items") or [] if isinstance(i, dict)}
    out = {"items": len(counts), "lmd": counts.get("4001"), "orundum": counts.get("4003"),
           "originite": counts.get("4002")}
    if d.get("changed"):
        out["changed"] = len(d["changed"])
    return {k: v for k, v in out.items() if v is not None}


def _box(d):
    ops = d.get("operators")
    return {"operators": len(ops) if isinstance(ops, list) else ops}


def _skland(d):
    return _pick(d, "operators", "with_modules", "mastered", "source")


def _guard(d):
    if "before" not in d and "after" not in d:          # 重记起点：快照本身
        return _pick(d, "originite", "orundum")
    out = _pick(d, "before", "after", "baseline", "checked_instead", "resumed")
    if d.get("unexplained"):
        out["unexplained"] = d["unexplained"]
    if d.get("used"):
        out["used"] = [{k: u.get(k) for k in ("item", "amount")} for u in d["used"]]
    return out


def _maa(d):
    out = _pick(d, "stage", "times_done", "sanity_used", "medicine_used", "proxy_failed", "orundum_gained",
                "weekly_capped", "offline")
    if isinstance(d.get("sanity"), dict):
        out["sanity_left"] = d["sanity"].get("current")
    if isinstance(d.get("drops"), list):
        out["drops"] = len(d["drops"])
    bad = [t.get("name") for t in d.get("tasks") or [] if isinstance(t, dict)
           and str(t.get("status", "")).lower() not in ("", "success", "succeeded", "completed", "done")]
    if bad:
        out["tasks_not_done"] = bad
    return out


def _op_upgrade(d):
    return _pick(d, "operator", "target", "before", "after", "stage")


def _op_mastery(d):
    return _pick(d, "operator", "skill", "status", "rank", "hours", "short", "crafted", "stage")


def _state_op(d):
    return _pick(d, "name", "elite", "level", "skill_level")


def _op_progress(d):
    out = {"steps": [f"{r.get('step')}：{r.get('detail')}" for r in d.get("steps") or []] or None,
           "training_started": d.get("training_started") or None, "home": d.get("home")}
    fb = d.get("fallback")
    if fb:
        out["fallback"] = f"{fb.get('command')}：{fb.get('status')}"
    return {k: v for k, v in out.items() if v is not None}


def _craft(d):
    return _pick(d, "item", "count", "times", "station", "made", "restored", "balance_before", "balance_after",
                 "balance", "stage", "done")


def _goal(d):
    g = d.get("goal") or {}
    out = _pick(g, "title", "status")
    if d.get("changed"):
        out["changed"] = d["changed"]
    if "verified" in d:
        out["verified"] = d["verified"]
    return out


def _goals_move(d):
    return {"moved": d.get("moved")}


def _goals_expire(d):
    return {"expired": [g.get("title") for g in d.get("expired") or []]}


def _items_text(items, limit=8) -> list[str]:
    return [f"{i.get('name')}×{i.get('count')}" for i in (items or [])[:limit] if isinstance(i, dict)]


def _calc_need(d):
    short = d.get("short") or {}
    out = {"short_lmd": short.get("lmd") or None, "short_exp": short.get("exp") or None,
           "short": _items_text(short.get("items")) or None}
    after = d.get("short_after_goals") or {}
    if after.get("items"):
        out["short_after_goals"] = _items_text(after["items"])
    if d.get("assumed"):
        out["assumed"] = len(d["assumed"])
    return {k: v for k, v in out.items() if v is not None}


SUMMARIES = {
    "state sanity": _sanity, "state depot": _depot, "state box": _box, "state skland": _skland, "state op": _state_op,
    "guard snapshot": _guard, "guard check": _guard,
    "op upgrade": _op_upgrade, "op mastery": _op_mastery, "op progress": _op_progress,
    "craft": _craft,
    "goals add": _goal, "goals update": _goal, "goals remove": _goal, "goals move": _goals_move,
    "goals expire": _goals_expire,
    "calc need": _calc_need,
}


def summarize(command: str | None, data) -> dict | None:
    """命令的关键结果（小）；没有摘要或 data 不是 dict → None。摘要出错不影响命令。"""
    if not isinstance(data, dict) or not command:
        return None
    fn = SUMMARIES.get(command) or (_maa if command.startswith("maa ") and command != "maa why" else None)
    if fn is None:
        return None
    try:
        return fn(data) or None
    except Exception:                                    # noqa: BLE001 - 记录是附带的
        return None
