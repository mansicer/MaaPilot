"""`maapilot goals`：目标清单 private/goals.md（完成或放弃的挪进 goals-done.md）。契约与格式见 docs/cli.md「goals.md」。

半结构化 Markdown：每个目标一个 `## 标题`，下面固定 5 行字段（状态、对象、原话、同意、花合成玉/源石），
可选的第 6 行"期限"（限时活动用），再往下是 agent 自由写的"分解与进展"。程序只读写固定字段；其余文字（文件开头的说明、分解与进展）原样保留。
目标的先后就是优先级（第一个最高，`goals move` 调整）。
agent 也会直接用文件工具改这个文件，所以解析要宽松：键的顺序不限、半角冒号也认、缺字段只报 problems 不报错。

期限：`- 期限：2026-10-30 03:59`（游戏时区 UTC+8，到这一刻就算到期），或只写日期 `- 期限：2026-10-30`
（算到那个游戏日结束，即次日 04:00）。日期后面可以跟括号说明，如「2026-10-30 03:59（昨日海结束）」。
过了期限的目标"到期"：不算进行中（daily run 不再为它叫 agent），`goals expire` / `expire_due()` 把它挪进 goals-done.md。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, NEEDS_USER, CliError
from maapilot.knowledge.calendar import DAY_START, GAME_TZ
from maapilot.oscompat import fs

# 固定字段：键 → 输出里的名字
FIELDS = {"状态": "status", "对象": "object", "原话": "said", "同意": "approved", "花合成玉/源石": "premium"}
DONE_KEY = "结束"                                   # goals-done.md 里每段多的一行
DEADLINE_KEY = "期限"                               # 可选字段：没写不算问题（活动目标除外）
ACTIVE, PAUSED, DONE, DROPPED, EXPIRED = "进行中", "暂停", "完成", "放弃", "到期"
NOTES_HEAD = "分解与进展（agent 自由写）："
OBJECT_KINDS = ("干员", "材料", "关卡", "活动", "其他")
KINDS_TEXT = " / ".join(OBJECT_KINDS)

_HEADING = re.compile(r"^##(?!#)[ \t]*(.*?)\s*$")
_FIELD = re.compile(r"^[ \t]*[-*][ \t]*([^：:\n]{1,12}?)[ \t]*[：:][ \t]*(.*?)[ \t]*$")
_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")
# 期限：日期，可选时刻（时:分，可带秒）；文件里后面可以跟说明，命令行参数只能是这一段
_DEADLINE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:(?:[ \t]+|T)(\d{1,2})[:：](\d{2})(?:[:：]\d{2})?)?")
_NO_DEADLINE = ("", "none", "无", "没有", "不限")


def _norm_key(k: str) -> str | None:
    """字段键归一：去空白、全角斜杠；"花合成玉/源石"的各种写法（花合成玉、源石 / 合成玉/源石）都认。"""
    k = re.sub(r"\s+", "", k).replace("／", "/").replace("、", "/")
    if k in FIELDS or k in (DONE_KEY, DEADLINE_KEY):
        return k
    if "合成玉" in k or "源石" in k:
        return "花合成玉/源石"
    return None


def _deadline_from(m: re.Match) -> tuple[datetime, str]:
    """(到期时刻, 规范写法)。只有日期：那个游戏日结束（次日 04:00）。日期、时刻不存在抛 ValueError。"""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    day = date(y, mo, d)
    if m.group(4) is None:
        at = datetime.combine(day + timedelta(days=1), DAY_START, tzinfo=GAME_TZ)
        return at, f"{day:%Y-%m-%d}"
    at = datetime(y, mo, d, int(m.group(4)), int(m.group(5)), tzinfo=GAME_TZ)
    return at, f"{at:%Y-%m-%d %H:%M}"


def parse_deadline(text: str | None) -> datetime | None:
    """goals.md 里"期限"的值 → 带时区的到期时刻（GAME_TZ）。没写返回 None；写法不对抛 ValueError。"""
    t = (text or "").strip()
    if not t:
        return None
    m = _DEADLINE.match(t)
    if not m:
        raise ValueError(t)
    return _deadline_from(m)[0]


def _is_continuation(raw: str) -> bool:
    """字段值折行：缩进、非列表项的续行。"""
    return raw[:1] in (" ", "\t") and bool(raw.strip()) and not raw.lstrip().startswith(("-", "*", "#"))


@dataclass
class Section:
    title: str
    lines: list[str]                                 # 整段原文（含标题行），每行以换行结尾
    fields: dict = field(default_factory=dict)       # 键 → (起始行, 结束行(不含), 值)
    field_end: int = 1                               # 固定字段块之后的第一行（分解与进展从这里起）

    def value(self, key: str) -> str | None:
        f = self.fields.get(key)
        return f[2] if f else None

    @property
    def text(self) -> str:
        return "".join(self.lines)


@dataclass
class GoalsFile:
    path: Path
    preamble: list[str]
    sections: list[Section]
    trailing_newline: bool = True

    def text(self) -> str:
        return "".join(self.preamble) + "".join(s.text for s in self.sections)


def _parse_section(lines: list[str]) -> Section:
    """标题下的固定字段块：空行与 `- 键：值` 行（含折行的续行），遇到别的内容就是分解与进展的开始。"""
    m = _HEADING.match(lines[0].rstrip("\n"))
    sec = Section(title=m.group(1) if m else "", lines=lines)
    i, cur, last = 1, None, 0
    while i < len(lines):
        raw = lines[i].rstrip("\n")
        if not raw.strip():
            cur = None
            i += 1
            continue
        fm = _FIELD.match(raw)
        key = _norm_key(fm.group(1)) if fm else None
        if key:
            cur = None
            if key not in sec.fields:                # 同一个键写了两遍：认第一行
                sec.fields[key] = (i, i + 1, fm.group(2))
                cur = key
            last = i
            i += 1
            continue
        if cur and _is_continuation(raw):
            a, _, v = sec.fields[cur]
            sec.fields[cur] = (a, i + 1, f"{v} {raw.strip()}".strip())
            last = i
            i += 1
            continue
        break
    sec.field_end = last + 1
    return sec


def parse(text: str, path: Path) -> GoalsFile:
    lines = text.splitlines(keepends=True)
    trailing = True
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
        trailing = False
    fenced, inside = set(), False                    # 代码块里的 "## " 不算标题
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            inside = not inside
        elif inside:
            fenced.add(i)
    starts = [i for i, ln in enumerate(lines) if i not in fenced and _HEADING.match(ln.rstrip("\n"))]
    pre = lines[:starts[0]] if starts else lines
    secs = [_parse_section(lines[a:b]) for a, b in zip(starts, starts[1:] + [len(lines)])]
    return GoalsFile(path=path, preamble=pre, sections=secs, trailing_newline=trailing)


def load(path: Path | None = None) -> GoalsFile:
    p = path or paths.GOALS
    try:
        text = fs.read_text_utf8(p)
    except FileNotFoundError:
        text = ""
    return parse(text, p)


def save(gf: GoalsFile) -> None:
    """临时文件 + 替换；没改的段落字节不变（原来文件末尾没有换行的，照旧不加；原来是 CRLF 换行的照旧写 CRLF）。"""
    text = gf.text()
    if not gf.trailing_newline and text.endswith("\n"):
        text = text[:-1]
    fs.atomic_write_text(gf.path, text, newline=fs.newline_of(gf.path))


# --- 读 --------------------------------------------------------------------------------------------------------------

def deadline_of(sec: Section) -> datetime | None:
    """这个目标的到期时刻；没写或写法不对（problems 会报）都当没有期限。"""
    try:
        return parse_deadline(sec.value(DEADLINE_KEY))
    except ValueError:
        return None


def is_expired(sec: Section, now: datetime | None = None) -> bool:
    dl = deadline_of(sec)
    return dl is not None and (now or _now()) >= dl


def to_dict(sec: Section, now: datetime | None = None) -> dict:
    d = {"title": sec.title}
    for key, name in FIELDS.items():
        v = sec.value(key)
        if name == "premium":
            d[name] = None if v is None else (True if v.startswith("是") else False if v.startswith("否") else None)
        else:
            d[name] = v
    dl = deadline_of(sec)
    now = now or _now()
    d["deadline"] = dl.isoformat(timespec="minutes") if dl else None
    d["expired"] = dl is not None and now >= dl
    if dl:
        d["days_left"] = round((dl - now).total_seconds() / 86400, 1)
    if sec.value(DONE_KEY) is not None:
        d["ended"] = sec.value(DONE_KEY)
    d["notes"] = "".join(sec.lines[sec.field_end:]).strip("\n")
    return d


def problems(sec: Section, done: bool = False) -> list[str]:
    out = []
    if not sec.title:
        out.append("标题是空的")
    missing = [k for k in FIELDS if not (sec.value(k) or "").strip()]
    if missing:
        out.append("缺少字段：" + "、".join(missing))
    st = sec.value("状态")
    ok_status = (DONE, DROPPED, EXPIRED) if done else (ACTIVE, PAUSED)
    if st and not st.startswith(ok_status):
        out.append(f"状态写成了「{st}」，应是{'或'.join(ok_status)}")
    pv = sec.value("花合成玉/源石")
    if pv and not pv.startswith(("是", "否")):
        out.append(f"花合成玉/源石写成了「{pv}」，应是 是 或 否")
    obj = sec.value("对象")
    if obj and not obj.startswith(OBJECT_KINDS):
        out.append(f"对象「{obj}」不是 {KINDS_TEXT} 开头的写法")
    dv = (sec.value(DEADLINE_KEY) or "").strip()
    if dv:
        try:
            parse_deadline(dv)
        except ValueError:
            out.append(f"期限写成了「{dv}」，应是 2026-10-30 03:59 或 2026-10-30 这样的写法")
    elif obj and obj.startswith("活动") and not done:
        out.append("活动目标没写期限：活动总会结束，补一行「- 期限：<结束时间>」")
    return out


def _in_order(sec: Section) -> bool:
    """排优先级的目标：进行中、暂停（goals.md 里的先后就是优先级，第一个最高）。"""
    return (sec.value("状态") or "").strip().startswith((ACTIVE, PAUSED))


def priorities(gf: GoalsFile) -> dict[int, int]:
    """id(段) → 优先级（1 起，按文件顺序，只数进行中、暂停的）。"""
    out = {}
    for s in gf.sections:
        if _in_order(s):
            out[id(s)] = len(out) + 1
    return out


def is_active(sec: Section, now: datetime | None = None) -> bool:
    """进行中、且没过期限。"""
    return (sec.value("状态") or "").strip().startswith(ACTIVE) and not is_expired(sec, now)


def active_goals() -> list[dict]:
    """进行中的目标（daily run、doctor --brief、calc need 用），按优先级（文件顺序）；过了期限的不算。"""
    now = _now()
    gf = load()
    pr = priorities(gf)
    return [{**to_dict(s, now), "priority": pr.get(id(s))} for s in gf.sections if is_active(s, now)]


def active_count() -> int:
    return sum(1 for s in load().sections if is_active(s))


# --- 改 --------------------------------------------------------------------------------------------------------------

def find(gf: GoalsFile, title: str) -> Section:
    """精确匹配标题，否则唯一的前缀。"""
    t = title.strip()
    exact = [s for s in gf.sections if s.title == t]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise CliError("ambiguous_goal", f"有 {len(exact)} 个目标都叫「{t}」", hint="先把 goals.md 里重复的标题改开",
                       exit=BAD_REQUEST)
    pre = [s for s in gf.sections if s.title.startswith(t)] if t else []
    if len(pre) == 1:
        return pre[0]
    if pre:
        raise CliError("ambiguous_goal", f"「{t}」对得上好几个目标", hint="写完整的标题", exit=BAD_REQUEST,
                       data={"matches": [s.title for s in pre]})
    raise CliError("unknown_goal", f"没有叫「{t}」的目标", hint="goals list 看现有的标题", exit=BAD_REQUEST,
                   data={"titles": [s.title for s in gf.sections]})


def _reparse(sec: Section) -> None:
    new = _parse_section(sec.lines)
    sec.title, sec.fields, sec.field_end = new.title, new.fields, new.field_end


def _del_field(sec: Section, key: str) -> None:
    if key in sec.fields:
        a, b, _ = sec.fields[key]
        del sec.lines[a:b]
        _reparse(sec)


def _set_field(sec: Section, key: str, value: str) -> None:
    line = f"- {key}：{value}\n"
    if key in sec.fields:
        a, b, _ = sec.fields[key]
        sec.lines[a:b] = [line]                      # 连同折行的续行一起换掉
    else:
        at = sec.field_end                           # 插在最后一个字段后面
        sec.lines.insert(at, line)
    _reparse(sec)


def _append_note(sec: Section, text: str, when: datetime) -> None:
    note = f"- {when:%m-%d}：{text}\n"
    end = len(sec.lines)
    while end > sec.field_end and not sec.lines[end - 1].strip():
        end -= 1
    if "".join(sec.lines[sec.field_end:end]).strip():
        sec.lines.insert(end, note)
    else:
        sec.lines[sec.field_end:end] = ["\n", NOTES_HEAD + "\n", note]


def _gap_before_append(gf: GoalsFile) -> None:
    """往文件末尾追加一段前，保证前面的内容以空行结束（段与段之间空一行）。"""
    tail = gf.sections[-1].lines if gf.sections else gf.preamble
    if tail and tail[-1].strip():
        tail.append("\n")


def _strip_eof_blank(gf: GoalsFile) -> None:
    tail = gf.sections[-1].lines if gf.sections else gf.preamble
    while len(tail) > 1 and not tail[-1].strip():
        tail.pop()


def _one_line(name: str, v: str | None) -> str:
    v = (v or "").strip()
    if not v:
        raise CliError("bad_arguments", f"{name} 不能是空的", exit=BAD_REQUEST)
    if "\n" in v:
        raise CliError("bad_arguments", f"{name} 要写成一行", exit=BAD_REQUEST)
    return v


def _object(v: str) -> str:
    v = _one_line("--object", v)
    if not v.startswith(OBJECT_KINDS):
        raise CliError("invalid_object", f"对象「{v}」的写法不对", exit=BAD_REQUEST,
                       hint=f"以 {KINDS_TEXT} 开头，如「干员 遥 → 精2 1级」「材料 聚酸酯组 × 10」「活动 昨日海」"
                            "（docs/cli.md「对象的写法」）")
    return v


def _deadline(v: str) -> str | None:
    """--deadline → 规范写法（"2026-10-30 03:59" 或 "2026-10-30"）；none / 空 → None（去掉期限）。
    写法不对、日期不存在、已经过了都是 bad_deadline。"""
    t = (v or "").strip()
    if t.lower() in _NO_DEADLINE:
        return None
    hint = "写成 2026-10-30 03:59（游戏时间 UTC+8），或只写日期 2026-10-30（算到次日 04:00）；去掉期限写 none"
    m = _DEADLINE.fullmatch(t)
    try:
        if not m:
            raise ValueError(t)
        at, text = _deadline_from(m)
    except ValueError:
        raise CliError("bad_deadline", f"期限「{t}」的写法不对", hint=hint, exit=BAD_REQUEST) from None
    if at <= _now():
        raise CliError("bad_deadline", f"期限 {text} 已经过了", hint=hint, exit=BAD_REQUEST,
                       data={"deadline": at.isoformat(timespec="minutes")})
    return text


def _need_deadline(object_: str | None, deadline: str | None) -> None:
    if (object_ or "").startswith("活动") and not deadline:
        raise CliError("bad_deadline", "活动目标要写期限（活动结束的时间）", exit=BAD_REQUEST,
                       hint="加 --deadline \"2026-10-30 03:59\"（游戏里活动页写的结束时间）")


def _premium(v: str | None) -> str:
    v = (v or "否").strip()
    if v in ("是", "yes", "true"):
        return "是"
    if v in ("否", "no", "false"):
        return "否"
    raise CliError("bad_arguments", f"--premium 只能是 是 或 否（写的是 {v}）", exit=BAD_REQUEST)


def _now() -> datetime:
    return datetime.now().astimezone()


def _approved(v: str) -> str:
    """同意的记录以时间开头；没写时间就补上现在。"""
    v = _one_line("--approved", v)
    return v if _DATE.match(v) else f"{_now():%Y-%m-%d %H:%M}，{v}"


def add(title, object_, said, approved, premium="否", note=None, deadline=None) -> dict:
    title = _one_line("--title", title)
    if title.startswith("#"):
        raise CliError("bad_arguments", "标题不要带 #", exit=BAD_REQUEST)
    object_ = _object(object_)
    said = _one_line("--said", said)
    if not _DATE.search(said):
        said = f"{said}（{_now():%Y-%m-%d}）"
    approved = _approved(approved)
    premium = _premium(premium)
    deadline = _deadline(deadline) if deadline is not None else None
    _need_deadline(object_, deadline)
    gf = load()
    if any(s.title == title for s in gf.sections):
        raise CliError("duplicate_goal", f"已经有叫「{title}」的目标", hint="换个标题，或用 goals update 改它",
                       exit=BAD_REQUEST)
    if not gf.sections and not "".join(gf.preamble).strip():
        gf.preamble = ["# 目标清单\n"]
    _gap_before_append(gf)
    lines = [f"## {title}\n", f"- 状态：{ACTIVE}\n", f"- 对象：{object_}\n", f"- 原话：{said}\n",
             f"- 同意：{approved}\n", f"- 花合成玉/源石：{premium}\n"]
    if deadline:
        lines.append(f"- {DEADLINE_KEY}：{deadline}\n")
    lines += ["\n", NOTES_HEAD + "\n"]
    if note and note.strip():
        lines.append(f"- {_now():%m-%d}：{_one_line('--note', note)}\n")
    sec = _parse_section(lines)
    gf.sections.append(sec)
    save(gf)
    return {"goal": to_dict(sec), "active": sum(1 for s in gf.sections if is_active(s))}


def update(title, status=None, object_=None, approved=None, premium=None, note=None, rename=None,
           deadline=None) -> dict:
    gf = load()
    sec = find(gf, title)
    changed = []
    if status is not None:
        if status not in (ACTIVE, PAUSED):
            raise CliError("bad_arguments", f"--status 只能是 {ACTIVE} 或 {PAUSED}；完成或放弃用 goals remove",
                           exit=BAD_REQUEST)
        _set_field(sec, "状态", status)
        changed.append("状态")
    if object_ is not None:
        _set_field(sec, "对象", _object(object_))
        changed.append("对象")
    if approved is not None:
        _set_field(sec, "同意", _approved(approved))
        changed.append("同意")
    if premium is not None:
        _set_field(sec, "花合成玉/源石", _premium(premium))
        changed.append("花合成玉/源石")
    if deadline is not None:
        dl = _deadline(deadline)
        if dl:
            _set_field(sec, DEADLINE_KEY, dl)
        else:
            _del_field(sec, DEADLINE_KEY)
        changed.append("期限")
    if object_ is not None or deadline is not None:
        _need_deadline(sec.value("对象"), sec.value(DEADLINE_KEY))
    if rename is not None:
        new = _one_line("--rename", rename)
        if any(s.title == new for s in gf.sections if s is not sec):
            raise CliError("duplicate_goal", f"已经有叫「{new}」的目标", exit=BAD_REQUEST)
        sec.lines[0] = f"## {new}\n"
        _reparse(sec)
        changed.append("标题")
    if note is not None:
        _append_note(sec, _one_line("--note", note), _now())
        _reparse(sec)
        changed.append("分解与进展")
    if not changed:
        raise CliError("bad_arguments", "没有要改的内容",
                       hint="加 --status / --object / --approved / --premium / --deadline / --note / --rename", exit=BAD_REQUEST)
    save(gf)
    return {"goal": to_dict(sec), "changed": changed}


def _ended_copy(sec: Section, status: str, ended: str, note: str | None = None) -> Section:
    lines = list(sec.lines)
    while len(lines) > 1 and not lines[-1].strip():  # 段尾空行不带走
        lines.pop()
    moved = _parse_section(lines)
    _set_field(moved, "状态", status)
    _set_field(moved, DONE_KEY, ended)
    if note:
        _append_note(moved, note, _now())
        _reparse(moved)
    return moved


def _move_to_done(gf: GoalsFile, moves: list[tuple[Section, Section]]) -> None:
    """moves：(goals.md 里的原段, 要写进 goals-done.md 的段)。
    先写 goals-done.md 再删原段：中途出错顶多重复一段，不会丢。"""
    done = load(paths.GOALS_DONE)
    if not done.sections and not "".join(done.preamble).strip():
        done.preamble = ["# 完成或放弃的目标\n"]
    for _, moved in moves:
        _gap_before_append(done)
        done.sections.append(moved)
    save(done)
    for sec, _ in moves:
        idx = gf.sections.index(sec)
        gf.sections.pop(idx)
        if idx == len(gf.sections):                  # 删的是最后一段：文件末尾不留多余空行
            _strip_eof_blank(gf)
    save(gf)


# --- 完成前核对 --------------------------------------------------------------------------------------------------------

def _op_text(e: int | None, lv: int | None) -> str:
    return f"精{e} {lv}级" if e is not None else "不知道"


def verify(object_text: str | None) -> dict:
    """对照仓库、干员识别的快照，看目标是不是真的做完了（只读快照，不碰模拟器）。
    返回 {verified: True|False|None, short: [...], current: {...}, why: [没能核对的原因], depot_at, box_at}。
    干员：快照里的精英化 / 等级 / 技能等级 / 专精 / 模组逐项对目标；材料：仓库里有没有那么多（calc need 的 short）。
    关卡、活动、其他：算不了，verified 为 None。只要有一项没到就是 False；全到了但有读不到的项是 None。"""
    from maapilot.tools import calc
    out: dict = {"verified": None, "short": [], "current": {}, "why": []}
    try:
        obj = calc.parse_object(object_text or "")
    except CliError as e:
        out["why"].append(f"对象写法看不懂（{e.message}）")
        return out
    if obj["kind"] == "材料":
        try:
            r = calc.need(object_text, ignore_goals=True)
        except CliError as e:
            out["why"].append(e.message)
            return out
        out["depot_at"] = r.get("depot_at")
        out["current"] = {"items": r["have"]["items"]}
        if not r.get("depot_at"):
            out["why"].append("还没有仓库快照")
            return out
        out["short"] = r["short"]["items"]
        out["verified"] = not out["short"]
        return out
    if obj["kind"] != "干员":
        out["why"].append(f"{obj['kind']}目标没法按快照核对")
        return out
    box, box_at = calc.box_snapshot()
    out["box_at"] = box_at
    name, tgt = obj["operator"], obj["target"]
    owned = box.get(name)
    if not box_at:
        out["why"].append("还没有干员识别的快照")
        return out
    if not owned:
        out["why"].append(f"干员识别里没有{name}")
        return out
    out["current"] = {k: owned.get(k) for k in ("elite", "level", "skill_level", "skills", "modules") if k in owned}
    if owned.get("live"):
        out["live"] = owned["live"]                     # 有几项用的是现场读数（森空岛还没更新）
    short, why = [], []

    def miss(part, target, current):
        short.append({"part": part, "target": target, "current": current})

    if "elite" in tgt or "level" in tgt:
        ce, cl = owned.get("elite"), owned.get("level")
        te = tgt.get("elite", ce)
        tl = tgt.get("level", 1)
        if ce is None or cl is None:
            why.append("快照里没有精英化 / 等级")
        elif (int(ce), int(cl)) < (int(te), int(tl)):
            miss("精英化与等级", _op_text(te, tl), _op_text(ce, cl))
    if tgt.get("skill_level") is not None:
        cs = owned.get("skill_level")
        if cs is None:
            why.append("快照里没有技能等级")
        elif int(cs) < tgt["skill_level"]:
            miss("技能等级", f"{tgt['skill_level']}级", f"{cs}级")
    skills = owned.get("skills")
    for k, m in (tgt.get("mastery") or {}).items():
        sk = next((x for x in skills or [] if x.get("index") == k), None)
        if sk is None or sk.get("mastery") is None:
            why.append(f"快照里没有技能{k}的专精")
        elif int(sk["mastery"]) < m:
            miss(f"技能{k}专精", f"专{m}", f"专{sk['mastery']}")
    for t, n in (tgt.get("module") or {}).items():
        mods = owned.get("modules")
        if mods is None:
            why.append("快照里没有模组")
            continue
        try:
            from maapilot.knowledge import gamedata as g
            mid = g.find_module(name, t).get("uniEquipId")
        except Exception:                                # noqa: BLE001 - 没有模组表、名字不对：这一项核对不了
            why.append(f"查不到模组{t}")
            continue
        md = next((x for x in mods if x.get("id") == mid), None)
        lv = int((md or {}).get("level") or 0)
        if lv < n:
            miss(f"模组{t}", f"{n}级", f"{lv}级" if lv else "没解锁")
    out["short"], out["why"] = short, why
    out["verified"] = False if short else (None if why else True)
    return out


def _short_text(short: list[dict]) -> str:
    """没到的项 → 一句话：干员「技能3专精 专1→专3」，材料「聚酸酯组 差 4」。"""
    return "、".join(f"{x['part']} {x['current']}→{x['target']}" if "part" in x else f"{x.get('name')} 差 {x.get('count')}"
                    for x in short)


def remove(title, status, reason=None, confirmed: bool = False) -> dict:
    """完成或放弃：整段挪进 goals-done.md。--reason 必填。
    完成时先 verify()：没做完 → exit 3 not_achieved（只有用户能决定，用户说算完成再加 confirmed）；
    核对不了不拦，在"结束"那行记下"没能核对：…"。"""
    if status not in (DONE, DROPPED):
        raise CliError("bad_arguments", f"--status 只能是 {DONE} 或 {DROPPED}", exit=BAD_REQUEST)
    if not (reason or "").strip():
        raise CliError("reason_required", "要写 --reason：为什么完成或放弃（会记进 goals-done.md）", exit=BAD_REQUEST,
                       hint="如 --reason \"遥已精2 1级（干员识别 10-20）\"、--reason \"用户说不要了（10-20）\"")
    reason = _one_line("--reason", reason)
    gf = load()
    sec = find(gf, title)
    check = None
    ended = f"{_now():%Y-%m-%d} {status}，{reason}"
    if status == DONE:
        check = verify(sec.value("对象"))
        if check["verified"] is False and not confirmed:
            raise CliError("not_achieved", f"「{sec.title}」按快照看还没做完", exit=NEEDS_USER,
                           hint="告诉用户哪里没到，只有用户能决定算不算完成；用户说算，再加 --confirmed-by-user",
                           data={k: check.get(k) for k in ("short", "current", "depot_at", "box_at", "live") if k in check})
        if confirmed and check["verified"] is not True:
            ended += "（用户确认" + (f"：快照里还没到 {_short_text(check['short'])}" if check["short"] else "") + "）"
        elif check["verified"] is None:
            ended += "（没能核对：" + "；".join(check["why"] or ["不知道原因"]) + "）"
        elif check.get("live"):
            lv = check["live"]
            ended += f"（已核对：快照加 {str(lv.get('at') or '')[5:16].replace('T', ' ')} {lv.get('source')} 的现场读数）"
        else:
            ended += "（已核对快照）"
    elif confirmed:
        ended += "（用户确认）"
    moved = _ended_copy(sec, status, ended)
    _move_to_done(gf, [(sec, moved)])
    out = {"goal": to_dict(moved), "active": sum(1 for s in gf.sections if is_active(s)),
           "verified": None if check is None else check["verified"]}
    if check is not None:
        out["check"] = {k: check.get(k) for k in ("short", "why", "depot_at", "box_at") if k in check}
    return out


def move(title, top: bool = False, before: str | None = None, after: str | None = None) -> dict:
    """调整先后（= 优先级）：整段挪到最前 / 某个目标前面 / 后面，段落内容一字不改。"""
    if [bool(top), before is not None, after is not None].count(True) != 1:
        raise CliError("bad_arguments", "--top、--before、--after 要且只要一个", exit=BAD_REQUEST)
    gf = load()
    sec = find(gf, title)
    anchor = find(gf, before if before is not None else after) if not top else None
    if anchor is sec:
        raise CliError("bad_arguments", "不能相对自己挪", exit=BAD_REQUEST)
    secs = [s for s in gf.sections if s is not sec]
    at = 0 if top else secs.index(anchor) + (1 if after is not None else 0)
    secs.insert(at, sec)
    for s in secs:                                   # 每段以空行结束（段与段之间空一行），最后一段不留尾部空行
        while len(s.lines) > 1 and not s.lines[-1].strip():
            s.lines.pop()
    for s in secs[:-1]:
        s.lines.append("\n")
    gf.sections = secs
    if gf.preamble and gf.preamble[-1].strip():
        gf.preamble.append("\n")
    save(gf)
    pr = priorities(gf)
    return {"moved": sec.title, "order": [{"title": s.title, "status": s.value("状态"), "priority": pr.get(id(s))}
                                          for s in gf.sections]}


# --- 前后对照（daily run 在目标 agent 前后各拍一次） -----------------------------------------------------------------

SNAP_FIELDS = {**FIELDS, DEADLINE_KEY: "deadline"}


def snapshot(path: Path | None = None) -> dict:
    """goals.md 里进行中、暂停的目标：{标题: {status, object, said, approved, premium, deadline}}（原文字符串，没写是 None）。"""
    out = {}
    for s in load(path).sections:
        if (s.value("状态") or "").strip().startswith((ACTIVE, PAUSED)):
            out[s.title] = {name: s.value(key) for key, name in SNAP_FIELDS.items()}
    return out


def _done_entry(done: GoalsFile, title: str) -> dict | None:
    hit = [s for s in done.sections if s.title == title]
    if not hit:
        return None
    s = hit[-1]                                          # 同名的取最后挪进去的
    return {"status": s.value("状态"), "ended": s.value(DONE_KEY)}


def diff(before: dict, after: dict, done: GoalsFile | None = None) -> dict:
    """两次 snapshot() 的差别：{removed: [{title, fields, done_entry}], added: [{title, fields}],
    changed: [{title, field, before, after}]}。removed 的去 goals-done.md 找同名段（status + ended），找不到是 None
    ——那就是没经过 goals remove 就没了。改标题算一删一加。只读文件，不写。"""
    if done is None:
        done = load(paths.GOALS_DONE)
    removed = [{"title": t, "fields": f, "done_entry": _done_entry(done, t)} for t, f in before.items() if t not in after]
    added = [{"title": t, "fields": f} for t, f in after.items() if t not in before]
    changed = [{"title": t, "field": k, "before": f.get(k), "after": after[t].get(k)}
               for t, f in before.items() if t in after
               for k in dict.fromkeys([*f, *after[t]]) if f.get(k) != after[t].get(k)]
    return {"removed": removed, "added": added, "changed": changed}


def expire_due(now: datetime | None = None) -> list[dict]:
    """把 goals.md 里过了期限的目标（进行中、暂停都算）挪进 goals-done.md，状态写"到期"。
    返回挪走的目标（同 goals list 里的一项）；没有到期的就什么都不写、返回 []。daily run 开头调用。"""
    now = now or _now()
    gf = load()
    moves = []
    for sec in gf.sections:
        if not is_expired(sec, now):
            continue
        dv = (sec.value(DEADLINE_KEY) or "").strip()
        moved = _ended_copy(sec, EXPIRED, f"{now:%Y-%m-%d} {EXPIRED}，期限 {dv} 已过，自动结束",
                            note=f"到期（期限 {dv}），自动结束")
        moves.append((sec, moved))
    if moves:
        _move_to_done(gf, moves)
    return [to_dict(m, now) for _, m in moves]


def expire() -> dict:
    """goals expire 的函数版本。"""
    moved = expire_due()
    return {"expired": moved, "active": active_count()}


def list_goals(all_: bool = False) -> dict:
    gf = load()
    now = _now()
    goals, probs = [], []
    pr = priorities(gf)
    for s in gf.sections:
        goals.append({**to_dict(s, now), "priority": pr.get(id(s))})
        probs += [{"title": s.title, "file": "goals.md", "message": m} for m in problems(s)]
    titles = [s.title for s in gf.sections]
    for t in sorted({t for t in titles if titles.count(t) > 1}):
        probs.append({"title": t, "file": "goals.md", "message": "标题重复：改开之后才能用 goals 命令改它"})
    out = {"goals": goals, "active": sum(1 for s in gf.sections if is_active(s, now)), "problems": probs}
    if all_:
        done = load(paths.GOALS_DONE)
        out["done"] = [to_dict(s, now) for s in done.sections]
        probs += [{"title": s.title, "file": "goals-done.md", "message": m}
                  for s in done.sections for m in problems(s, done=True)]
    return out


# --- 命令 ------------------------------------------------------------------------------------------------------------

def register(sub) -> None:
    g = sub.add_parser("goals", help="目标清单（private/goals.md）")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")

    p = gs.add_parser("list", help="列出目标")
    p.add_argument("--all", action="store_true", help="连完成、放弃的一起")
    p.set_defaults(func=lambda a: list_goals(a.all), command="goals list")

    p = gs.add_parser("add", help="追加一个目标（状态：进行中）")
    p.add_argument("--title", required=True)
    p.add_argument("--object", required=True, help="如「干员 遥 → 精2 1级」")
    p.add_argument("--said", required=True, help="用户原话（带日期）")
    p.add_argument("--approved", "--agreed", dest="approved", required=True,
                   help="用户同意的记录：「2026-09-30 21:40，已报：…」")
    p.add_argument("--premium", default="否", help="要不要花合成玉/源石：是 / 否（默认否）")
    p.add_argument("--note", help="分解与进展的第一行")
    p.add_argument("--deadline", help="期限：「2026-10-30 03:59」（游戏时间 UTC+8）或「2026-10-30」（算到次日 04:00）；"
                                      "活动目标必填")
    p.set_defaults(func=lambda a: add(a.title, a.object, a.said, a.approved, a.premium, a.note, a.deadline),
                   command="goals add")

    p = gs.add_parser("update", help="改固定字段，或在分解与进展末尾追加一行")
    p.add_argument("title")
    p.add_argument("--status", help=f"{ACTIVE} / {PAUSED}")
    p.add_argument("--object")
    p.add_argument("--approved", "--agreed", dest="approved")
    p.add_argument("--premium")
    p.add_argument("--note")
    p.add_argument("--rename", help="改标题")
    p.add_argument("--deadline", help="改期限（写法同 goals add）；none 或空字符串去掉期限")
    p.set_defaults(func=lambda a: update(a.title, a.status, a.object, a.approved, a.premium, a.note, a.rename,
                                         a.deadline), command="goals update")

    p = gs.add_parser("remove", help="完成或放弃：整段挪进 goals-done.md")
    p.add_argument("title")
    p.add_argument("--status", required=True, help=f"{DONE} / {DROPPED}")
    p.add_argument("--reason", help="为什么完成或放弃（必填，记进 goals-done.md）")
    p.add_argument("--confirmed-by-user", dest="confirmed", action="store_true",
                   help="用户确认算完成（快照核对没过时才用）")
    p.set_defaults(func=lambda a: remove(a.title, a.status, a.reason, a.confirmed), command="goals remove")

    p = gs.add_parser("move", help="调整先后（goals.md 里越靠前优先级越高）")
    p.add_argument("title")
    w = p.add_mutually_exclusive_group(required=True)
    w.add_argument("--top", action="store_true", help="挪到最前（优先级最高）")
    w.add_argument("--before", metavar="标题", help="挪到这个目标前面")
    w.add_argument("--after", metavar="标题", help="挪到这个目标后面")
    p.set_defaults(func=lambda a: move(a.title, a.top, a.before, a.after), command="goals move")

    p = gs.add_parser("expire", help="过了期限的目标挪进 goals-done.md（状态：到期）")
    p.set_defaults(func=lambda a: expire(), command="goals expire")
