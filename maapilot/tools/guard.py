"""`maapilot guard snapshot / check / allow / status`：源石、合成玉对账——代码里唯一的硬防线。契约见 docs/cli.md「guard」。

数量来源：MAA 仓库识别（Depot）。实测它的结果里有源石（4002）、合成玉（4003）；和 `state depot` 是同一次识别，
顺手更新 depot 快照。Depot 按物品 id 给数，不依赖页面布局，但上万的数会取整（真机：16566 → 16000），
所以再回首页 OCR 顶栏：和仓库识别对得上时用顶栏的精确值（`merge_topbar`），对不上或读不到就用仓库识别的数。
识别结果里没有这两项、但认出了别的物品时按 0 算（真是 0 时游戏不显示）；这样误认只会往"减少了"的方向错，宁可误停。
一件都没认出 → 读不到（退出码 1），不做判断。

文件（private/guard/，只由程序写）：
- snapshot.json  `{at, originite, orundum}`：上次通过时的数；
- allowances.jsonl  每行一笔用户同意的花费 `{at, item, amount, reason, used, used_amount}`；
- stop.json  `{at, before, after, unexplained, run_id?}`：在就是停机中（`doctor --brief`、`daily run` 会看）；
- dirty.json  `{since, commands}`：上次对账（或快照）之后跑过可能花东西的命令（点击、MAA 任务、快捷操作；
  `cliutil.device()` 拿锁时调 `mark_dirty`）。

check：上千的数减少不到 1000 当作读数误差（ROUNDING）；其余减少量先扣掉快照之后记下的、还没用完的放行（按时间先后）；还有没放行的减少 → 写 stop.json、退出码 3；
通过 → 更新快照、删 stop.json 和 dirty.json。增加不算问题。读不到时什么都不改（dirty 留着，下次照样要对）。

snapshot：只在"快照之后没人碰过会花钱的操作"时重记起点（这样用户自己在游戏里花的不会被当成异常）；
dirty 在、又有快照时改做一次 check——agent 不能靠重记快照把自己的花费抹掉。停机中不重记。
"""
from __future__ import annotations

import json
import os

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, FAILED, NEEDS_USER, CliError
from maapilot.tools import common

ITEMS = {"originite": "4002", "orundum": "4003"}
NAMES = {"originite": "源石", "orundum": "合成玉"}
ALIASES = {"源石": "originite", "至纯源石": "originite", "originite": "originite",
           "合成玉": "orundum", "orundum": "orundum"}


def _f(name: str):
    return paths.GUARD / name


def snapshot_file():
    return _f("snapshot.json")


def stop_file():
    return _f("stop.json")


def allow_file():
    return _f("allowances.jsonl")


def dirty_file():
    return _f("dirty.json")


# 拿锁时会记 dirty 的命令（前缀）；启动、关游戏、截图、读状态不花东西，不记
_SPENDY = ("input ", "maa ", "op ", "craft")
_HARMLESS = ("maa startup", "maa closedown")


def mark_dirty(owner: str) -> None:
    """可能花东西的命令拿到锁时调用：记下"上次对账之后有人动过"。写不了不影响命令本身。"""
    if not owner.startswith(_SPENDY) or owner.startswith(_HARMLESS):
        return
    try:
        d = common.read_json(dirty_file()) or {"since": common.iso(), "commands": []}
        d["commands"] = (d.get("commands") or [])[-19:] + [owner]
        common.write_json(dirty_file(), d)
    except OSError:
        pass


def dirty() -> dict | None:
    return common.read_json(dirty_file())


def _clear_dirty() -> None:
    try:
        dirty_file().unlink()
    except FileNotFoundError:
        pass


def stopped() -> dict | None:
    """停机中 → stop.json 的内容；否则 None。不碰模拟器。"""
    return common.read_json(stop_file())


def currencies(depot_items: dict) -> dict:
    """{originite, orundum}；一件都没认出 → CliError(unreadable)。"""
    if not depot_items:
        raise CliError("unreadable", "仓库识别什么都没认出，读不到源石、合成玉", exit=FAILED,
                       hint="回首页后再试（maapilot guard check）")
    return {k: int(depot_items.get(iid, 0)) for k, iid in ITEMS.items()}


def read_now() -> dict:
    """识别仓库（同时更新 depot 快照），再回首页读顶栏，返回 {originite, orundum}。不拿锁。"""
    from maapilot.tools.state import read_depot, read_home
    depot = currencies(read_depot()["items"])
    try:
        top = read_home()[0]
    except Exception:                                  # 顶栏读不到就只用仓库识别的数
        top = None
    return merge_topbar(depot, top)


def merge_topbar(depot: dict, top: dict | None) -> dict:
    """仓库识别把上万的数取整（真机：合成玉 16566 认成 16000），单笔几百的花费可能看不出来。
    首页顶栏是精确值，但 OCR 可能读错：和仓库识别对得上（不小于它、差不到取整的一格）才用顶栏的数。"""
    if not top:
        return dict(depot)
    for k in ITEMS:                                    # 有一样对不上就说明顶栏读歪了，都不用
        d, t = int(depot.get(k) or 0), top.get(k)
        step = 1000 if d >= 10000 else 1
        if t is None or not d <= t < d + step:
            return dict(depot)
    return {k: int(top[k]) for k in ITEMS}


def load_allowances() -> list[dict]:
    out = []
    try:
        for ln in allow_file().read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _save_allowances(rows: list[dict]) -> None:
    p = allow_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, p)


def open_allowances(since: str | None, rows: list[dict] | None = None) -> list[dict]:
    """快照之后记下的、还没用完的放行（rows 省略时读文件；给了就返回其中的同一批对象）。"""
    t0 = common.parse_iso(since) if since else None
    out = []
    for r in (rows if rows is not None else load_allowances()):
        if r.get("used"):
            continue
        at = common.parse_iso(r.get("at"))
        if t0 is not None and (at is None or at < t0):
            continue
        out.append(r)
    return out


def take_snapshot(values: dict | None = None) -> dict:
    """记下现在的数（values 省略时读一次仓库）。不拿锁、不做判断（给 check 用；命令走 snapshot()）。"""
    values = values if values is not None else read_now()
    snap = {"at": common.iso(), **values}
    common.write_json(snapshot_file(), snap)
    _clear_dirty()
    return snap


def snapshot() -> dict:
    """`guard snapshot`：没人动过 → 重记起点；动过（dirty）且有快照 → 改做 check；停机中 → 退出码 3。"""
    stop = stopped()
    if stop:
        raise CliError("guard_stop", "对账停机中，不能重记起点", exit=NEEDS_USER, data={"stop": stop},
                       hint="先把停机的原因告诉用户（guard status）；用户说明后 guard allow 补记，再 guard check")
    d = dirty()
    if d and common.read_json(snapshot_file()):
        out = check()
        out["checked_instead"] = True
        out["detail"] = "上次对账之后跑过可能花东西的命令（" + "、".join(dict.fromkeys(d.get("commands") or [])) + \
                        "），所以先对账，不重记起点"
        return out
    return take_snapshot()


ROUNDING = 1000      # 上千的数读不准（仓库识别把上万的取整，顶栏也可能读不到）：减少不到这么多不算花费


def evaluate(before: dict, after: dict, allowances: list[dict]) -> tuple[dict, dict, dict]:
    """纯函数：(allowed 每种放行合计, used {allowance 下标: 用掉多少}, unexplained 每种没放行的减少（负数）)。
    前后有一个数上千时，减少不到 ROUNDING 当作读数误差，不算花费（用户 2026-10-05 定：不考虑只花了几百合成玉的情况）。"""
    allowed = {k: 0 for k in ITEMS}
    used: dict[int, int] = {}
    unexplained = {}
    for k in ITEMS:
        dec = int(before.get(k) or 0) - int(after.get(k) or 0)
        rows = [(i, r) for i, r in enumerate(allowances) if r.get("item") == k]
        allowed[k] = sum(int(r.get("amount", 0)) - int(r.get("used_amount", 0)) for _, r in rows)
        if dec <= 0:
            continue
        if dec < ROUNDING and max(int(before.get(k) or 0), int(after.get(k) or 0)) >= ROUNDING:
            continue
        left = dec
        for i, r in rows:
            avail = int(r.get("amount", 0)) - int(r.get("used_amount", 0))
            take = min(avail, left)
            if take > 0:
                used[i] = take
                left -= take
            if left <= 0:
                break
        if left > 0:
            unexplained[k] = -left
    return allowed, used, unexplained


def check(values: dict | None = None, *, run_id: str | None = None) -> dict:
    """对账。通过返回 data；没放行的减少抛 CliError(currency_decreased, 退出码 3)。不拿锁。"""
    snap = common.read_json(snapshot_file())
    after = values if values is not None else read_now()
    if not snap:
        take_snapshot(after)
        return {"baseline": True, "after": after, "detail": "还没有快照：这次的数记为起点"}
    before = {k: snap.get(k) for k in ITEMS}
    all_rows = load_allowances()
    open_rows = open_allowances(snap.get("at"), all_rows)
    allowed, used, unexplained = evaluate(before, after, open_rows)
    data = {"before": before, "after": after, "allowed": allowed, "unexplained": unexplained}
    if unexplained:
        stop = {"at": common.iso(), "before": before, "after": after, "unexplained": unexplained}
        if run_id or os.environ.get(common.RUN_ID_ENV):
            stop["run_id"] = run_id or os.environ[common.RUN_ID_ENV]
        common.write_json(stop_file(), stop)
        what = "、".join(f"{NAMES[k]}少了 {-v}" for k, v in unexplained.items())
        raise CliError("currency_decreased", f"没经用户同意：{what}", exit=NEEDS_USER, data=data,
                       hint="立刻告诉用户，问清楚；用户同意这笔后 guard allow 补记，再 guard check")
    # 通过：记下用掉的放行、更新快照、解除停机
    for i, amount in used.items():
        r = open_rows[i]
        r["used_amount"] = int(r.get("used_amount", 0)) + amount
        r["used"] = r["used_amount"] >= int(r.get("amount", 0))
    if used:
        _save_allowances(all_rows)                   # open_rows 是 all_rows 里的同一批对象
    take_snapshot(after)
    was_stopped = stopped() is not None
    try:
        stop_file().unlink()
    except FileNotFoundError:
        pass
    data["used"] = [{"item": open_rows[i]["item"], "amount": a, "reason": open_rows[i].get("reason")}
                    for i, a in used.items()]
    data["resumed"] = was_stopped
    return data


def allow(item: str, amount: int, reason: str) -> dict:
    key = ALIASES.get(item.strip())
    if key is None:
        raise CliError("bad_arguments", f"只能放行 源石 或 合成玉，不认识「{item}」", exit=BAD_REQUEST)
    if amount <= 0:
        raise CliError("bad_arguments", "数量要是正整数", exit=BAD_REQUEST)
    if not reason.strip():
        raise CliError("bad_arguments", "--reason 要写用户同意了什么", exit=BAD_REQUEST)
    row = {"at": common.iso(), "item": key, "amount": int(amount), "reason": reason.strip(), "used": False,
           "used_amount": 0}
    rows = load_allowances() + [row]
    _save_allowances(rows)
    out = {"recorded": row}
    if stopped():
        out["hint"] = "现在停机中：maapilot guard check 对一下，对上了就解除"
    return out


def status() -> dict:
    snap = common.read_json(snapshot_file())
    return {"snapshot": snap, "allowances": open_allowances((snap or {}).get("at")), "stop": stopped(),
            "dirty": dirty()}


# --- 命令 ----------------------------------------------------------------------------

@common.logged
def cmd_snapshot(args):
    with common.device("guard snapshot") as lk:
        with lk.keepalive():
            return snapshot()


@common.logged
def cmd_check(args):
    with common.device("guard check") as lk:
        with lk.keepalive():
            return check()


def cmd_allow(args):
    from maapilot.tools import confirm
    confirm.require("这笔花费")                                       # 定时日常里不记；会话里 agent 已在对话里问过用户
    return allow(args.item, args.amount, args.reason)


def cmd_status(args):
    return status()


def register(sub) -> None:
    g = sub.add_parser("guard", help="源石、合成玉对账")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("snapshot", help="记下现在的源石、合成玉（上次对账后动过会花钱的操作时改做对账）")
    p.set_defaults(func=cmd_snapshot, command="guard snapshot")
    p = gs.add_parser("check", help="再读一次，与快照对账")
    p.set_defaults(func=cmd_check, command="guard check")
    p = gs.add_parser("allow", help="记下用户在对话里同意的一笔花费（定时日常里一律拒绝）")
    p.add_argument("item", help="源石 或 合成玉")
    p.add_argument("amount", type=int)
    p.add_argument("--reason", required=True, help="用户同意了什么")
    p.set_defaults(func=cmd_allow, command="guard allow")
    p = gs.add_parser("status", help="快照、未用的放行、是否停机中（不碰模拟器）")
    p.set_defaults(func=cmd_status, command="guard status")
