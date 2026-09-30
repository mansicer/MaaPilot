"""统计数据层（见 docs/concepts/knowledge.md#统计数据层，决策 D121）：企鹅物流的关卡与掉率、一图流的物品价值，缓存在 data/live/。

缓存存接口原样的 JSON（`penguin-<服务器>-stages.json`、`penguin-<服务器>-matrix.json`、`yituliu-values.json`），
`data/live/manifest.json` 记每个文件的 `{fetched_at, source_url, ok, error, attempted_at}`；`load()` 读缓存时才规范化。
联网只发生在 `fetch_*()`；`sync()` 永不抛异常，失败时旧缓存原样保留。不碰模拟器、不读 private/。

企鹅物流数据以 CC BY-NC 4.0 发布（https://penguin-stats.io）：只在用户本机缓存，不随仓库分发。
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from maapilot import paths

PENGUIN_HOSTS = ("https://penguin-stats.io", "https://penguin-stats.cn")
PENGUIN_STAGES_PATH = "/PenguinStats/api/v2/stages?server={server}"
PENGUIN_MATRIX_PATH = "/PenguinStats/api/v2/result/matrix?server={server}"
YITULIU_VALUE_URL = "https://backend.yituliu.cn/item/v7/value"
MATRIX_STALE_H = 24
MATRIX_MAX_AGE_D = 30
VALUES_MAX_AGE_D = 7
MIN_SAMPLES = 100
VALUES_SERVERS = frozenset({"CN"})                 # 一图流只有国服口径
STAGE_SUFFIXES = ("_perm", "_rep")                 # 企鹅物流给常驻化 / 复刻关加的后缀
EXCLUDED_ITEM_TYPES = frozenset({"VOUCHER_MGACHA"})  # 随机素材包：在 item_table 里，但不是能直接用的物品

# 企鹅物流不统计固定奖励：CE 关的龙门币按关卡固定产出（游戏规则，手工维护）；结算龙门币不计
FIXED_YIELDS: dict[str, dict[str, float]] = {
    "wk_melee_1": {"4001": 1700}, "wk_melee_2": {"4001": 2800}, "wk_melee_3": {"4001": 4100},
    "wk_melee_4": {"4001": 5700}, "wk_melee_5": {"4001": 7500}, "wk_melee_6": {"4001": 10000},
}

MANIFEST_NAME = "manifest.json"
VALUES_FILE = "yituliu-values.json"
USER_AGENT = "maapilot-live"


def live_dir() -> Path:
    return paths.DATA / "live"


def stages_file(server: str) -> str:
    return f"penguin-{server}-stages.json"


def matrix_file(server: str) -> str:
    return f"penguin-{server}-matrix.json"


@dataclass(frozen=True)
class Drop:
    stage_id: str
    item_id: str
    rate: float
    samples: int


@dataclass
class Live:
    server: str
    rates: dict[str, dict[str, float]]             # stageId → {物品 id: 每次期望掉落}（已规范化，含 FIXED_YIELDS）
    ap: dict[str, int]                             # stageId → 理智
    open_at: dict[str, datetime | None]            # 活动关的开放时间（常驻关 None）
    values: dict[str, float] | None                # 物品 id → 理智价值；不可用为 None
    matrix_at: datetime | None
    values_at: datetime | None
    samples: dict[str, dict[str, int]] = field(default_factory=dict)   # stageId → {物品 id: 样本数}

    def usable(self, now: datetime) -> bool:
        """掉率不超过 30 天（超过或没有 → 规划器退化到主掉落关）。"""
        return self.matrix_at is not None and _aware(now) - self.matrix_at <= timedelta(days=MATRIX_MAX_AGE_D)

    def stale(self, now: datetime) -> bool:
        """掉率超过 24 小时（仍然用，警告 live.stale）。"""
        return self.matrix_at is None or _aware(now) - self.matrix_at > timedelta(hours=MATRIX_STALE_H)

    def efficiency(self, stage_id: str) -> float | None:
        """Σ 掉率 × 物品价值 ÷ 理智；没有价值表、不认识这关或理智为 0 时 None。没有估值的物品按 0 算。"""
        ap = self.ap.get(stage_id)
        if self.values is None or not ap or stage_id not in self.rates:
            return None
        return sum(r * self.values.get(i, 0.0) for i, r in self.rates[stage_id].items()) / ap

    def drops(self, stage_id: str) -> list[Drop]:
        return [Drop(stage_id, i, r, self.samples.get(stage_id, {}).get(i, 0))
                for i, r in sorted(self.rates.get(stage_id, {}).items())]


# --- 纯逻辑 ---------------------------------------------------------------------

def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.astimezone()


def _now(now: datetime | None) -> datetime:
    return _aware(now) if now else datetime.now(timezone.utc).astimezone()


def _from_ms(ms) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if isinstance(ms, (int, float)) else None


def normalize_stage_id(pid: str, known: set[str]) -> str | None:
    """企鹅物流 stageId → 官方 stageId：`_perm` / `_rep` 去后缀；官方表里没有的（随机素材包、公招……）None。"""
    if pid in known:
        return pid
    for suf in STAGE_SUFFIXES:
        if pid.endswith(suf) and pid[: -len(suf)] in known:
            return pid[: -len(suf)]
    return None


def parse_matrix(raw: dict, stages_raw: list, *, known_stages: set[str], known_items: set[str],
                 server: str = "CN") -> dict:
    """企鹅物流 matrix + stages → {"rates", "samples", "ap", "open_at"}（键是官方 stageId）。
    只取当前区间（end 为 null）且样本 ≥ MIN_SAMPLES 的条目，掉率 = quantity / times；物品不在 known_items 的丢弃。
    同一官方关有多个变体（原版 / 复刻 / 常驻）时，每种物品取样本最多的一条；开放时间取最晚的一次。
    rates 不含 FIXED_YIELDS（load() 合并）。"""
    ap: dict[str, int] = {}
    open_at: dict[str, datetime | None] = {}
    for s in stages_raw:
        sid = normalize_stage_id(s.get("stageId", ""), known_stages)
        exist = ((s.get("existence") or {}).get(server) or {})
        if sid is None or not exist.get("exist"):
            continue
        if isinstance(s.get("apCost"), int):
            ap[sid] = s["apCost"]
        t = _from_ms(exist.get("openTime"))
        prev = open_at.get(sid)
        open_at[sid] = t if prev is None or (t is not None and t > prev) else prev

    best: dict[tuple[str, str], tuple[int, float]] = {}
    for m in raw.get("matrix", []):
        if m.get("end") is not None or m.get("times", 0) < MIN_SAMPLES or m.get("itemId") not in known_items:
            continue
        sid = normalize_stage_id(m.get("stageId", ""), known_stages)
        if sid is None or sid not in ap:
            continue
        key = (sid, m["itemId"])
        if key not in best or m["times"] > best[key][0]:
            best[key] = (m["times"], m["quantity"] / m["times"])

    rates: dict[str, dict[str, float]] = {}
    samples: dict[str, dict[str, int]] = {}
    for (sid, item), (times, rate) in best.items():
        rates.setdefault(sid, {})[item] = rate
        samples.setdefault(sid, {})[item] = times
    return {"rates": rates, "samples": samples, "ap": ap, "open_at": open_at}


def parse_values(raw: dict, *, known_items: set[str]) -> dict[str, float]:
    """一图流 /item/v7/value → {物品 id: itemValueAp}，只留 known_items 里的物品。"""
    return {d["itemId"]: float(d["itemValueAp"]) for d in raw.get("data", [])
            if d.get("itemId") in known_items and isinstance(d.get("itemValueAp"), (int, float))}


def check_penguin(matrix: object, stages: object) -> None:
    """写缓存前的结构校验：不像企鹅物流接口的响应抛 ValueError。"""
    if not isinstance(stages, list) or not stages or not all(isinstance(s, dict) and "stageId" in s for s in stages):
        raise ValueError("企鹅物流 stages 响应结构不对")
    rows = matrix.get("matrix") if isinstance(matrix, dict) else None
    if not isinstance(rows, list) or not rows or not all(
            isinstance(m, dict) and {"stageId", "itemId", "times", "quantity"} <= m.keys() for m in rows):
        raise ValueError("企鹅物流 matrix 响应结构不对")


def check_values(raw: object) -> None:
    rows = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or raw.get("code") != 200 or not isinstance(rows, list) or not rows or not all(
            isinstance(d, dict) and "itemId" in d and "itemValueAp" in d for d in rows):
        raise ValueError("一图流物品价值响应结构不对")


def read_manifest() -> dict:
    try:
        return json.loads((live_dir() / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _fetched_at(manifest: dict, file: str) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(manifest[file]["fetched_at"]))
    except (KeyError, TypeError, ValueError):
        return None


def ages(server: str = "CN", *, now: datetime | None = None) -> dict[str, float | None]:
    """每个缓存文件距上次成功拉取的秒数；没拉到过为 None（`data sync --check` 报年龄用）。"""
    manifest, t = read_manifest(), _now(now)
    files = [stages_file(server), matrix_file(server)] + ([VALUES_FILE] if server in VALUES_SERVERS else [])
    return {f: (t - at).total_seconds() if (at := _fetched_at(manifest, f)) else None for f in files}


def _known_stages() -> set[str]:
    from maapilot.knowledge.stages import _stage_table
    return set(_stage_table())


def _known_items() -> set[str]:
    from maapilot.knowledge.gamedata import _items
    return {i for i, it in _items().items() if it.get("itemType") not in EXCLUDED_ITEM_TYPES}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load(server: str = "CN", *, now: datetime | None = None) -> Live | None:
    """读 data/live/ 的缓存并规范化；企鹅物流缓存没有（或读不出）时 None。一图流超过 7 天、没有或不是国服时 values 为 None。
    需要官方表 stage_table / item_table。"""
    d, manifest, t = live_dir(), read_manifest(), _now(now)
    matrix_raw, stages_raw = _read_json(d / matrix_file(server)), _read_json(d / stages_file(server))
    try:
        check_penguin(matrix_raw, stages_raw)
    except ValueError:
        return None
    known_items = _known_items()
    parsed = parse_matrix(matrix_raw, stages_raw, known_stages=_known_stages(), known_items=known_items, server=server)
    rates = parsed["rates"]
    matrix_at = _fetched_at(manifest, matrix_file(server))
    for sid, opened in parsed["open_at"].items():   # 缓存早于（复刻 / 常驻）开放：那时的掉率不代表现在，这关不给掉率
        if opened is not None and matrix_at is not None and matrix_at < opened:
            rates.pop(sid, None)
    for sid, fixed in FIXED_YIELDS.items():
        if sid in parsed["ap"]:
            rates.setdefault(sid, {}).update(fixed)

    values, values_at = None, None
    if server in VALUES_SERVERS:
        values_at = _fetched_at(manifest, VALUES_FILE)
        raw = _read_json(d / VALUES_FILE)
        if values_at is not None and t - values_at <= timedelta(days=VALUES_MAX_AGE_D):
            try:
                check_values(raw)
                values = parse_values(raw, known_items=known_items)
            except ValueError:
                values = None
    return Live(server=server, rates=rates, ap=parsed["ap"], open_at=parsed["open_at"], values=values,
                matrix_at=matrix_at, values_at=values_at, samples=parsed["samples"])


# --- 联网 ---------------------------------------------------------------------------

def _request(url: str, *, timeout: float, body: bytes | None = None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_penguin(server: str, *, timeout: float = 20) -> tuple[dict, list]:
    """企鹅物流 (matrix, stages)，先主站后镜像；都失败抛最后一个异常。响应结构不对也换下一个主机。"""
    err: Exception | None = None
    for host in PENGUIN_HOSTS:
        try:
            stages = _request(host + PENGUIN_STAGES_PATH.format(server=server), timeout=timeout)
            matrix = _request(host + PENGUIN_MATRIX_PATH.format(server=server), timeout=timeout)
            check_penguin(matrix, stages)
            return matrix, stages
        except Exception as e:                    # 网络、HTTP、JSON、结构——都换下一个主机
            err = e
    raise err                                     # type: ignore[misc]


def fetch_values(*, timeout: float = 20) -> dict:
    """一图流物品价值（请求体 {} 取默认参数）。"""
    raw = _request(YITULIU_VALUE_URL, timeout=timeout, body=b"{}")
    check_values(raw)
    return raw


def _write_atomic(files: dict[str, object], *, indent: int | None = None) -> None:
    """先把所有文件写成 .part，再逐个 replace（同一数据源的几个文件一起换）。"""
    d = live_dir()
    d.mkdir(parents=True, exist_ok=True)
    parts = []
    for name, obj in files.items():
        tmp = d / (name + ".part")
        seps = None if indent else (",", ":")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent, separators=seps) + "\n", encoding="utf-8")
        parts.append((tmp, d / name))
    for tmp, dest in parts:
        tmp.replace(dest)


def _describe(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"[:300]


def sync(server: str = "CN", *, now: datetime | None = None) -> dict:
    """拉取并替换缓存，更新 manifest。返回 {"penguin": "ok"|错误文字, "values": "ok"|"skipped"|错误文字, "fetched_at"}。
    永不抛异常：失败只记进结果与 manifest（fetched_at 保留上次成功的时间），旧缓存原样保留。
    一图流只有国服口径，别的服务器 values 为 "skipped"。"""
    t = _now(now)
    stamp = t.isoformat(timespec="seconds")
    result = {"penguin": "ok", "values": "ok", "fetched_at": stamp}
    try:
        manifest = read_manifest()
        names = {stages_file(server): PENGUIN_STAGES_PATH, matrix_file(server): PENGUIN_MATRIX_PATH}
        try:
            matrix, stages = fetch_penguin(server)
            check_penguin(matrix, stages)
            _write_atomic({stages_file(server): stages, matrix_file(server): matrix})
            for name, path in names.items():
                manifest[name] = {"fetched_at": stamp, "source_url": PENGUIN_HOSTS[0] + path.format(server=server),
                                  "ok": True, "error": None, "attempted_at": stamp}
        except Exception as e:
            result["penguin"] = _describe(e)
            for name, path in names.items():
                old = manifest.get(name) or {"fetched_at": None, "source_url": PENGUIN_HOSTS[0] + path.format(server=server)}
                manifest[name] = {**old, "ok": False, "error": result["penguin"], "attempted_at": stamp}
        if server in VALUES_SERVERS:
            try:
                raw = fetch_values()
                check_values(raw)
                _write_atomic({VALUES_FILE: raw})
                manifest[VALUES_FILE] = {"fetched_at": stamp, "source_url": YITULIU_VALUE_URL, "ok": True,
                                         "error": None, "attempted_at": stamp}
            except Exception as e:
                result["values"] = _describe(e)
                manifest[VALUES_FILE] = {**manifest.get(VALUES_FILE, {"fetched_at": None, "source_url": YITULIU_VALUE_URL}),
                                         "ok": False, "error": result["values"], "attempted_at": stamp}
        else:
            result["values"] = "skipped"
        _write_atomic({MANIFEST_NAME: manifest}, indent=1)
    except Exception as e:                        # 写盘失败之类：同样只报告
        msg = _describe(e)
        result = {**result, **{k: msg for k in ("penguin", "values") if result[k] == "ok"}}
    return result
