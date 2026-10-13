"""外部数据源的格式核对与健康记录（见 docs/design.md「外部数据源」）。

MaaPilot 读的外部数据（森空岛、MAA 的活动列表和任务表、maa-cli 的输出、官方公告、官网安装包地址、企鹅物流、一图流、
游戏数据表）格式随时可能变。规矩：在解析的地方核对用到的字段（有没有、类型对不对），对不上就抛 `UpstreamFormat`
（错误码 upstream_format，消息写明哪个来源、缺哪个字段），不悄悄当成"没有数据"。有正当退路的（森空岛的理智 → 首页识别）
照样退，但要 `bad()` 记下来，让用户的 agent 看得到。

健康记录在 private/state/sources.json：
`{来源: {last_ok, last_error: {at, code, message, effect}, recent: [最近几次出错]}}`。
last_error 比 last_ok 新（或从没成功过）就算"有问题"：`doctor --brief` 的 sources、日常汇报的"要注意"都从这里读。
网络连不上不算格式问题，不记在这里（各命令自己报）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from maapilot import paths

CODE = "upstream_format"
RECENT_MAX = 20                 # 每个来源留最近几次出错（汇报按时间段取）
OK_REFRESH = timedelta(hours=1)  # 健康时 last_ok 隔多久才重写一次（少写盘）

SOURCES = {
    "skland": "森空岛",
    "maa_activity": "MAA 的活动关列表",
    "maa_tasks": "MAA 的任务表",
    "maa_cli": "maa-cli 的输出",
    "bulletin": "官方公告",
    "game_download": "官网的安装包地址",
    "penguin": "企鹅物流",
    "yituliu": "一图流",
    "gamedata": "游戏数据表",
}


def name(source: str) -> str:
    return SOURCES.get(source, source)


class UpstreamFormat(ValueError):
    """外部数据的格式和程序认的不一样。field：点分路径（status.ap.current）；problem：缺 / 类型不对 / 自由文字。"""

    code = CODE

    def __init__(self, source: str, field: str, problem: str = "missing", *, effect: str | None = None):
        self.source, self.field, self.effect = source, field, effect
        what = {"missing": f"缺 {field}", "type": f"{field} 的类型不对"}.get(problem, f"{field}：{problem}")
        self.message = f"{name(source)}的数据格式变了（{what}）"
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {"source": self.source, "field": self.field, "effect": self.effect}


_TYPES = {dict: "dict", list: "list", str: "str", int: "int"}


def need(obj, path: str, typ=None, *, source: str, label: str | None = None):
    """取 obj 里点分路径 path 的值；路径上缺东西或最后的值不是 typ → UpstreamFormat（字段名写 label 或 path）。"""
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            raise UpstreamFormat(source, label or path)
        cur = cur[key]
    if typ is not None and (not isinstance(cur, typ) or (typ is int and isinstance(cur, bool))):
        raise UpstreamFormat(source, label or path, "type")
    return cur


def need_keys(row, keys: tuple[str, ...], *, source: str, where: str) -> dict:
    """row 是 dict 且有这些键；否则 UpstreamFormat（字段写成 where.键）。"""
    if not isinstance(row, dict):
        raise UpstreamFormat(source, where, "type")
    for k in keys:
        if k not in row:
            raise UpstreamFormat(source, f"{where}.{k}")
    return row


# --- 健康记录 ---------------------------------------------------------------------------------------------------------

def _path():
    return paths.STATE / "sources.json"


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds")


def _dt(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


def load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{id(d)}.part")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


def _broken(e: dict) -> bool:
    err = _dt((e.get("last_error") or {}).get("at"))
    if err is None:
        return False
    last = _dt(e.get("last_ok"))
    return last is None or err >= last


def ok(source: str, now: datetime | None = None) -> None:
    """这次按程序认的格式读成了。写盘失败不影响调用方。"""
    try:
        d = load()
        e = d.get(source) or {}
        t = now or _now()
        last = _dt(e.get("last_ok"))
        if not _broken(e) and last is not None and t - last < OK_REFRESH:
            return
        e["last_ok"] = _iso(t)
        d[source] = e
        _save(d)
    except OSError:
        pass


def bad(source: str, err, *, effect: str | None = None, code: str = CODE, now: datetime | None = None) -> dict:
    """记一次格式问题。err：UpstreamFormat 或一句话。effect：受影响的是什么、程序怎么退的（"理智改用首页识别"）。"""
    message = err.message if isinstance(err, UpstreamFormat) else str(err)
    effect = effect or getattr(err, "effect", None)
    rec = {"at": _iso(now or _now()), "code": code, "message": message, **({"effect": effect} if effect else {})}
    try:
        d = load()
        e = d.get(source) or {}
        e["last_error"] = rec
        e["recent"] = ([*(e.get("recent") or []), rec])[-RECENT_MAX:]
        d[source] = e
        _save(d)
    except OSError:
        pass
    return rec


def line(rec: dict) -> str:
    return rec.get("message", "") + (f"，{rec['effect']}" if rec.get("effect") else "")


def unhealthy(d: dict | None = None) -> list[dict]:
    """现在有问题的来源：[{source, name, at, code, message, effect, line}]。"""
    d = load() if d is None else d
    out = []
    for src, e in d.items():
        if isinstance(e, dict) and _broken(e):
            r = e["last_error"]
            if not r.get("message"):
                continue
            out.append({"source": src, "name": name(src), "at": r.get("at"), "code": r.get("code"),
                        "message": r.get("message"), "effect": r.get("effect"), "line": line(r)})
    return sorted(out, key=lambda x: x["at"] or "")


def errors_between(start: str | None, end: str | None = None, d: dict | None = None) -> list[str]:
    """[start, end] 之间记下的格式问题（去重后的中文句子），给日常汇报用。end 为 None 时到现在。"""
    t0, t1 = _dt(start), _dt(end) if end else _now()
    if t0 is None:
        return []
    d = load() if d is None else d
    out: list[str] = []
    for e in d.values():
        for r in (e.get("recent") or []) if isinstance(e, dict) else []:
            t = _dt(r.get("at"))
            if t is not None and t0 <= t <= (t1 or t) and line(r) not in out:
                out.append(line(r))
    return out


HINT = "外部数据的格式变了，要开发者改程序；告诉用户哪部分受影响（不要自己绕过去）"


def cli_error(e: UpstreamFormat, *, effect: str | None = None):
    """UpstreamFormat → 命令的错误（错误码 upstream_format，退出码 1），data 里带来源和字段。"""
    from maapilot.cliutil import FAILED, CliError
    eff = effect or e.effect
    return CliError(CODE, e.message + (f"，{eff}" if eff else ""), exit=FAILED, hint=HINT,
                    data={**e.to_dict(), "effect": eff})
