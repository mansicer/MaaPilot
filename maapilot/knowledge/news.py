"""官方游戏内公告（国服：官服、B 服同一套日程）：活动预告、停机维护、版本更新。契约见 docs/cli.md「news」。

接口就是游戏里"公告"那一栏（不用登录）：列表 `bulletinList`，全文 `bulletin/<cid>`（HTML）。
程序只做可靠的事：拉下来、去掉 HTML、按固定写法抽出时间段（"10月09日 12:00 - 10月30日 03:59"、"维护时间：2026年10月09日06:00 ~ 12:00"），
分成 活动 / 维护 / 其他 三类。正文原样给 agent 读，抽不出的时间由 agent 自己看。
缓存在 private/state/news.json：`{at, items: [...], told: {<事件键>: 时间}}`。told：告诉过用户了（`news told`）。
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timedelta

from maapilot import paths
from maapilot.knowledge import sources
from maapilot.knowledge.calendar import GAME_TZ
from maapilot.knowledge.sources import UpstreamFormat, need, need_keys

LIST_URL = "https://ak-webview.hypergryph.com/api/game/bulletinList?target=Android"
ITEM_URL = "https://ak-webview.hypergryph.com/api/game/bulletin/{cid}"
SRC = "bulletin"
EFFECT = "维护和活动时间按上次拉到的公告看"
STALE_H = 6                     # 缓存超过几小时，日常开头再拉一次
START_DUE_H = 72                # 活动开始后几小时内还算"没说过要说"
MAINT_DUE_H = 72                # 维护结束后几小时内还算"没说过要说"（之后游戏多半已经在日常里更新了）
RECENT_DAYS = 30                # 只看最近多少天发的公告（常驻的说明类公告很旧，不看）
CN_SERVERS = ("Official", "Bilibili")   # 这些服用的是国服公告里的维护、活动日程；其他服（YoStar、txwy）日程不一样，不看


def applies(cfg: dict | None = None) -> bool:
    """这台的服务器跟不跟国服的公告日程走（维护跳过日常、日程提醒都只对这些服）。"""
    from maapilot import config
    return config.server(cfg) in CN_SERVERS


def _path():
    return paths.STATE / "news.json"


# --- 解析（纯函数，测试用） ---------------------------------------------------------------------------------------------

def plain(content: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", content or "", flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t　]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


_D = r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*[:：]\s*(\d{2})"
_T = r"(\d{1,2})\s*[:：]\s*(\d{2})"
# 起 - 止：止可以只写时间（同一天）
_RANGE = re.compile(_D + r"\s*(?:-|~|～|—|至|到)\s*(?:" + _D + r"|" + _T + r")")
_LABEL_CUT = re.compile(r"[◆。；;\n】]|开启时间|活动时间|开放时间|维护时间|时间[:：]")


def _mk(year: int, mon: int, day: int, h: int, m: int) -> datetime | None:
    try:
        return datetime(year, mon, day, h, m, tzinfo=GAME_TZ)
    except ValueError:
        return None


def windows(text: str, published: datetime) -> list[dict]:
    """正文里的时间段 [{label, start, end}]（ISO，游戏时区）。年份没写时取发布那年；比发布早半年以上的算下一年。"""
    out = []
    for mt in _RANGE.finditer(text):
        g = mt.groups()
        y1 = int(g[0]) if g[0] else published.year
        start = _mk(y1, int(g[1]), int(g[2]), int(g[3]), int(g[4]))
        if start is None:
            continue
        if not g[0] and start < published - timedelta(days=180):
            start = start.replace(year=start.year + 1)
        if g[6]:
            y2 = int(g[5]) if g[5] else start.year
            end = _mk(y2, int(g[6]), int(g[7]), int(g[8]), int(g[9]))
            if end is not None and end < start and not g[5]:
                end = end.replace(year=end.year + 1)
        else:
            end = _mk(start.year, start.month, start.day, int(g[10]), int(g[11]))
            if end is not None and end < start:
                end += timedelta(days=1)
        if end is None:
            continue
        before = text[max(0, mt.start() - 40):mt.start()]
        parts = [p.strip(" ：:「」【】") for p in _LABEL_CUT.split(before)]
        parts = [p for p in parts if p]
        label = re.sub(r"^[一二三四五六七八九十\d]*[、.．]\s*", "", parts[-1] if parts else "")
        out.append({"label": label[-24:], "start": start.isoformat(), "end": end.isoformat()})
    return out


def kind_of(title: str, text: str) -> str:
    """活动 / 维护 / 其他。维护：标题或正文写了停机维护、闪断更新；活动：活动预告、SideStory、限时活动开启。"""
    t = title.replace("\n", "")
    if re.search(r"维护|闪断|版本更新", t) or re.search(r"停机维护|闪断更新", text[:300]):
        return "维护"
    if re.search(r"活动即将开启|活动开启|SideStory|限时活动", t):     # 只看标题：寻访、通讯的正文里也常有"活动"
        return "活动"
    return "其他"


def item_from(raw: dict, *, published: datetime | None = None) -> dict:
    """bulletin/<cid> 的 data → 一条公告。"""
    title = (raw.get("title") or "").replace("\\n", " ").replace("\n", " ").strip()
    text = plain(raw.get("content") or "")
    if published is None:
        ts = raw.get("updatedAt")
        published = datetime.fromtimestamp(ts, GAME_TZ) if isinstance(ts, (int, float)) else datetime.now(GAME_TZ)
    kind = kind_of(title, text)
    wins = windows(text, published)
    item = {"cid": str(raw.get("cid")), "title": title, "kind": kind, "published": raw.get("displayTime"),
            "windows": wins, "text": text}
    if kind == "维护":
        mw = [w for w in wins if "维护" in w["label"]] or wins[:1]
        item["maintenance"] = mw[0] if mw else None
        item["forced_update"] = bool(re.search(r"强制更新|重新下载和安装", text))
    return item


# --- 格式核对 ----------------------------------------------------------------------------------------------------------

def check_list(data) -> list:
    """bulletinList 的 data：list[]，每条有 cid、title、displayTime（"2026-10-13"）、updatedAt、category。对不上抛 UpstreamFormat。"""
    rows = need(data, "list", list, source=SRC)
    for b in rows:
        need_keys(b, ("cid", "title", "displayTime", "updatedAt", "category"), source=SRC, where="list[]")
        try:
            datetime.strptime(b["displayTime"] or "", "%Y-%m-%d")
        except (TypeError, ValueError):
            raise UpstreamFormat(SRC, "list[].displayTime", f"日期写法认不出（{b['displayTime']}）") from None
    return rows


def check_item(raw) -> dict:
    """bulletin/<cid> 的 data：cid、title、content（HTML 字符串）、displayTime、updatedAt。"""
    need_keys(raw, ("cid", "title", "content", "displayTime", "updatedAt"), source=SRC, where="bulletin")
    if not isinstance(raw["content"], str):
        raise UpstreamFormat(SRC, "bulletin.content", "type")
    return raw


# --- 联网 --------------------------------------------------------------------------------------------------------------

def _get(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "maapilot-news"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    if not isinstance(d, dict):
        raise UpstreamFormat(SRC, "最外层", "type")
    if d.get("code") not in (0, None):
        raise RuntimeError(f"公告接口返回 {d.get('code')}：{d.get('msg')}")
    return need(d, "data", dict, source=SRC)


def fetch(now: datetime | None = None, get=_get) -> list[dict]:
    """最近 RECENT_DAYS 天的活动、维护公告（"其他"只留标题）。网络错误直接抛；格式变了记进 sources.json、抛 UpstreamFormat。"""
    try:
        out = _fetch(now, get)
    except UpstreamFormat as e:
        sources.bad(SRC, e, effect=EFFECT)
        raise
    sources.ok(SRC)
    return out


def _fetch(now: datetime | None, get) -> list[dict]:
    now = now or datetime.now(GAME_TZ)
    old = load()
    cache = {i["cid"]: i for i in old.get("items") or []}
    out = []
    for b in check_list(get(LIST_URL)):
        shown = datetime.strptime(b["displayTime"], "%Y-%m-%d").replace(tzinfo=GAME_TZ)
        if shown < now - timedelta(days=RECENT_DAYS):
            continue
        cid = str(b.get("cid"))
        title = (b.get("title") or "").replace("\\n", " ").replace("\n", " ").strip()
        prev = cache.get(cid)
        if prev and prev.get("updated_at") == b.get("updatedAt"):
            out.append(prev)
            continue
        if kind_of(title, "") == "其他" and b.get("category") not in (1, 2):
            out.append({"cid": cid, "title": title, "kind": "其他", "published": b.get("displayTime"), "windows": []})
            continue
        item = item_from(check_item(get(ITEM_URL.format(cid=cid))))
        item["updated_at"] = b.get("updatedAt")
        out.append(item)
    return out


# --- 缓存 --------------------------------------------------------------------------------------------------------------

def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


_MARKS = ("told",)


def _save_marks(d: dict) -> None:
    """存之前再读一次盘上的 told 合进来：拉公告要几秒，期间别的进程（`news told`）记下的不能丢。"""
    cur = load()
    for k in _MARKS:
        if cur.get(k) or d.get(k):
            d[k] = {**(cur.get(k) or {}), **(d.get(k) or {})}
    save(d)


def refresh(now: datetime | None = None, get=_get) -> dict:
    now = now or datetime.now(GAME_TZ)
    items = fetch(now, get)
    d = load()
    d.update({"at": now.isoformat(timespec="seconds"), "items": items})
    _save_marks(d)
    return d


def stale(d: dict | None = None, now: datetime | None = None) -> bool:
    d = load() if d is None else d
    try:
        at = datetime.fromisoformat(d["at"])
    except (KeyError, TypeError, ValueError):
        return True
    return (now or datetime.now(GAME_TZ)) - at > timedelta(hours=STALE_H)


# --- 从缓存里看日程（不联网） ------------------------------------------------------------------------------------------

def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def maintenance_now(d: dict | None = None, now: datetime | None = None) -> dict | None:
    """现在是不是在某条维护公告写的维护时段里 → {cid, title, start, end, forced_update} / None。"""
    d = load() if d is None else d
    now = now or datetime.now(GAME_TZ)
    for i in d.get("items") or []:
        w = i.get("maintenance")
        if w and _dt(w["start"]) <= now < _dt(w["end"]):
            return {"cid": i["cid"], "title": i["title"], "start": w["start"], "end": w["end"],
                    "forced_update": bool(i.get("forced_update"))}
    return None


def events(d: dict | None = None) -> list[dict]:
    """日程上的"时间点"：维护开始 / 结束、活动（各段）开始 / 结束。每个有唯一的 key（告诉过用户就记在 told 里）。"""
    d = load() if d is None else d
    out = []
    for i in d.get("items") or []:
        if i.get("kind") == "维护" and i.get("maintenance"):
            w = i["maintenance"]
            out.append({"key": f"{i['cid']}:维护开始", "at": w["start"], "what": "维护开始", "cid": i["cid"],
                        "title": i["title"], "forced_update": bool(i.get("forced_update")), "end": w["end"]})
            out.append({"key": f"{i['cid']}:维护结束", "at": w["end"], "what": "维护结束", "cid": i["cid"],
                        "title": i["title"], "forced_update": bool(i.get("forced_update"))})
        elif i.get("kind") == "活动":
            starts: dict[str, dict] = {}                   # 同一时刻开始的几段（关卡、商店、寻访）算一个时间点，取最长的那段
            for w in i.get("windows") or []:
                e = starts.get(w["start"])
                if e is None:
                    starts[w["start"]] = {"key": f"{i['cid']}:{w['start']}", "at": w["start"], "what": "开始",
                                          "cid": i["cid"], "title": i["title"], "label": w["label"], "end": w["end"]}
                elif w["end"] > e["end"]:
                    e["end"] = w["end"]
            out.extend(starts.values())
        else:
            continue
        # 公告本身：活动 / 维护一发公告就是一个时间点（还没开始时先跟用户说一声）
        spans = [i["maintenance"]] if i.get("kind") == "维护" and i.get("maintenance") else i.get("windows") or []
        if spans:
            first = min(w["start"] for w in spans)
            pub = f"{i['published']}T00:00:00+08:00" if i.get("published") else first
            out.append({"key": f"{i['cid']}:公告", "at": min(pub, first), "what": "公告", "cid": i["cid"],
                        "title": i["title"], "kind": i["kind"], "start": first, "end": max(w["end"] for w in spans),
                        "forced_update": bool(i.get("forced_update"))})
    out.sort(key=lambda e: e["at"])
    return out


def upcoming(d: dict | None = None, now: datetime | None = None, days: float = 3) -> list[dict]:
    """接下来 `days` 天内的时间点，加上正在进行的维护。"""
    now = now or datetime.now(GAME_TZ)
    return [e for e in events(d) if now <= _dt(e["at"]) <= now + timedelta(days=days)]


def due(d: dict | None = None, now: datetime | None = None) -> list[dict]:
    """还没告诉过用户的官方日程消息：会话里的 agent 该去说了（maapilot-events）。
    - `公告`：活动 / 维护的新公告，还没开始（先说一声；同一条公告别的时间点已经说过的不算）；
    - 活动 `开始`：开始后 START_DUE_H 小时内、还没结束（用户这几天没开会话也能补上）；
    - `维护结束`：结束后 MAINT_DUE_H 小时内（要更新游戏）。"""
    d = load() if d is None else d
    now = now or datetime.now(GAME_TZ)
    told = d.get("told") or {}
    told_cids = {k.split(":", 1)[0] for k in told}
    out = []
    for e in events(d):
        if e["key"] in told:
            continue
        if e["what"] == "公告":
            ok = e["cid"] not in told_cids and now < _dt(e["start"])
        elif e["what"] == "开始":
            ok = _dt(e["at"]) <= now < min(_dt(e["end"]), _dt(e["at"]) + timedelta(hours=START_DUE_H))
        elif e["what"] == "维护结束":
            ok = _dt(e["at"]) <= now <= _dt(e["at"]) + timedelta(hours=MAINT_DUE_H)
        else:
            ok = False
        if ok:
            out.append(e)
    return out


def _mark(field: str, keys: list[str], now: datetime | None) -> dict:
    d = load()
    marks = d.setdefault(field, {})
    at = (now or datetime.now(GAME_TZ)).isoformat(timespec="seconds")
    for k in keys:
        marks[k] = at
    _save_marks(d)
    return d[field]


def mark_told(keys: list[str], now: datetime | None = None) -> dict:
    return _mark("told", keys, now)


def lines(d: dict | None = None, now: datetime | None = None, days: float = 3) -> list[str]:
    """给启动检查的几句话：正在维护、3 天内的维护和活动开始、正在进行的活动。"""
    d = load() if d is None else d
    now = now or datetime.now(GAME_TZ)
    out = []
    m = maintenance_now(d, now)
    if m:
        out.append(f"正在维护（到 {_short(m['end'])}）" + ("，维护后要强制更新游戏" if m["forced_update"] else ""))
    for e in upcoming(d, now, days):
        if e["what"] == "维护开始":
            out.append(f"{_short(e['at'])}–{_short(e['end'])} 停机维护" + ("（强制更新）" if e["forced_update"] else ""))
        elif e["what"] == "开始":
            out.append(f"{_short(e['at'])} 开始：{_name(e)}")
    for e in events(d):
        if e["what"] == "开始" and _dt(e["at"]) <= now < _dt(e["end"]):
            out.append(f"进行中：{_name(e)}（到 {_short(e['end'])}）")
    return out


def _name(e: dict) -> str:
    t = re.sub(r"\s*活动即将开启\s*", "", e["title"]).strip(" 【】")
    lab = e.get("label") or ""
    return f"{t}（{lab}）" if lab and t not in lab and len(lab) <= 12 else t


def _short(iso: str) -> str:
    t = _dt(iso)
    return f"{t.month}月{t.day}日 {t:%H:%M}"
