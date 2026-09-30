"""`maapilot goals`：目标清单 private/goals.md（完成或放弃的挪进 goals-done.md）。契约与格式见 docs/cli.md「goals.md」。

半结构化 Markdown：每个目标一个 `## 标题`，下面固定 5 行字段（状态、对象、原话、同意、花合成玉/源石），
再往下是 agent 自由写的"分解与进展"。程序只读写固定字段；其余文字（文件开头的说明、分解与进展）原样保留。
agent 也会直接用文件工具改这个文件，所以解析要宽松：键的顺序不限、半角冒号也认、缺字段只报 problems 不报错。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, CliError

# 固定字段：键 → 输出里的名字
FIELDS = {"状态": "status", "对象": "object", "原话": "said", "同意": "approved", "花合成玉/源石": "premium"}
DONE_KEY = "结束"                                   # goals-done.md 里每段多的一行
ACTIVE, PAUSED, DONE, DROPPED = "进行中", "暂停", "完成", "放弃"
NOTES_HEAD = "分解与进展（agent 自由写）："
OBJECT_KINDS = ("干员", "材料", "关卡", "其他")

_HEADING = re.compile(r"^##(?!#)[ \t]*(.*?)\s*$")
_FIELD = re.compile(r"^[ \t]*[-*][ \t]*([^：:\n]{1,12}?)[ \t]*[：:][ \t]*(.*?)[ \t]*$")
_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")


def _norm_key(k: str) -> str | None:
    """字段键归一：去空白、全角斜杠；"花合成玉/源石"的各种写法（花合成玉、源石 / 合成玉/源石）都认。"""
    k = re.sub(r"\s+", "", k).replace("／", "/").replace("、", "/")
    if k in FIELDS or k == DONE_KEY:
        return k
    if "合成玉" in k or "源石" in k:
        return "花合成玉/源石"
    return None


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
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    return parse(text, p)


def save(gf: GoalsFile) -> None:
    """临时文件 + 替换；没改的段落字节不变（原来文件末尾没有换行的，照旧不加）。"""
    gf.path.parent.mkdir(parents=True, exist_ok=True)
    text = gf.text()
    if not gf.trailing_newline and text.endswith("\n"):
        text = text[:-1]
    tmp = gf.path.with_name(f".{gf.path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, gf.path)


# --- 读 --------------------------------------------------------------------------------------------------------------

def to_dict(sec: Section) -> dict:
    d = {"title": sec.title}
    for key, name in FIELDS.items():
        v = sec.value(key)
        if name == "premium":
            d[name] = None if v is None else (True if v.startswith("是") else False if v.startswith("否") else None)
        else:
            d[name] = v
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
    ok_status = (DONE, DROPPED) if done else (ACTIVE, PAUSED)
    if st and not st.startswith(ok_status):
        out.append(f"状态写成了「{st}」，应是{'或'.join(ok_status)}")
    pv = sec.value("花合成玉/源石")
    if pv and not pv.startswith(("是", "否")):
        out.append(f"花合成玉/源石写成了「{pv}」，应是 是 或 否")
    obj = sec.value("对象")
    if obj and not obj.startswith(OBJECT_KINDS):
        out.append(f"对象「{obj}」不是 干员 / 材料 / 关卡 / 其他 开头的写法")
    return out


def is_active(sec: Section) -> bool:
    return (sec.value("状态") or "").strip().startswith(ACTIVE)


def active_goals() -> list[dict]:
    """进行中的目标（daily run、doctor --brief 用）。"""
    return [to_dict(s) for s in load().sections if is_active(s)]


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
                       hint="以 干员 / 材料 / 关卡 / 其他 开头，如「干员 遥 → 精2 1级」「材料 聚酸酯组 × 10」"
                            "（docs/cli.md「对象的写法」）")
    return v


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


def add(title, object_, said, approved, premium="否", note=None) -> dict:
    title = _one_line("--title", title)
    if title.startswith("#"):
        raise CliError("bad_arguments", "标题不要带 #", exit=BAD_REQUEST)
    object_ = _object(object_)
    said = _one_line("--said", said)
    if not _DATE.search(said):
        said = f"{said}（{_now():%Y-%m-%d}）"
    approved = _approved(approved)
    premium = _premium(premium)
    gf = load()
    if any(s.title == title for s in gf.sections):
        raise CliError("duplicate_goal", f"已经有叫「{title}」的目标", hint="换个标题，或用 goals update 改它",
                       exit=BAD_REQUEST)
    if not gf.sections and not "".join(gf.preamble).strip():
        gf.preamble = ["# 目标清单\n"]
    _gap_before_append(gf)
    lines = [f"## {title}\n", f"- 状态：{ACTIVE}\n", f"- 对象：{object_}\n", f"- 原话：{said}\n",
             f"- 同意：{approved}\n", f"- 花合成玉/源石：{premium}\n", "\n", NOTES_HEAD + "\n"]
    if note and note.strip():
        lines.append(f"- {_now():%m-%d}：{_one_line('--note', note)}\n")
    sec = _parse_section(lines)
    gf.sections.append(sec)
    save(gf)
    return {"goal": to_dict(sec), "active": sum(1 for s in gf.sections if is_active(s))}


def update(title, status=None, object_=None, approved=None, premium=None, note=None, rename=None) -> dict:
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
                       hint="加 --status / --object / --approved / --premium / --note / --rename", exit=BAD_REQUEST)
    save(gf)
    return {"goal": to_dict(sec), "changed": changed}


def remove(title, status, reason) -> dict:
    if status not in (DONE, DROPPED):
        raise CliError("bad_arguments", f"--status 只能是 {DONE} 或 {DROPPED}", exit=BAD_REQUEST)
    reason = _one_line("--reason", reason)
    gf = load()
    sec = find(gf, title)
    lines = list(sec.lines)
    while len(lines) > 1 and not lines[-1].strip():  # 段尾空行不带走
        lines.pop()
    moved = _parse_section(lines)
    _set_field(moved, "状态", status)
    _set_field(moved, DONE_KEY, f"{_now():%Y-%m-%d} {status}，{reason}")
    # 先写 goals-done.md 再删原段：中途出错顶多重复一段，不会丢
    done = load(paths.GOALS_DONE)
    if not done.sections and not "".join(done.preamble).strip():
        done.preamble = ["# 完成或放弃的目标\n"]
    _gap_before_append(done)
    done.sections.append(moved)
    save(done)
    idx = gf.sections.index(sec)
    gf.sections.pop(idx)
    if idx == len(gf.sections):                      # 删的是最后一段：文件末尾不留多余空行
        _strip_eof_blank(gf)
    save(gf)
    return {"goal": to_dict(moved), "active": sum(1 for s in gf.sections if is_active(s))}


def list_goals(all_: bool = False) -> dict:
    gf = load()
    goals, probs = [], []
    for s in gf.sections:
        goals.append(to_dict(s))
        probs += [{"title": s.title, "file": "goals.md", "message": m} for m in problems(s)]
    titles = [s.title for s in gf.sections]
    for t in sorted({t for t in titles if titles.count(t) > 1}):
        probs.append({"title": t, "file": "goals.md", "message": "标题重复：改开之后才能用 goals 命令改它"})
    out = {"goals": goals, "active": sum(1 for s in gf.sections if is_active(s)), "problems": probs}
    if all_:
        done = load(paths.GOALS_DONE)
        out["done"] = [to_dict(s) for s in done.sections]
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
    p.set_defaults(func=lambda a: add(a.title, a.object, a.said, a.approved, a.premium, a.note), command="goals add")

    p = gs.add_parser("update", help="改固定字段，或在分解与进展末尾追加一行")
    p.add_argument("title")
    p.add_argument("--status", help=f"{ACTIVE} / {PAUSED}")
    p.add_argument("--object")
    p.add_argument("--approved", "--agreed", dest="approved")
    p.add_argument("--premium")
    p.add_argument("--note")
    p.add_argument("--rename", help="改标题")
    p.set_defaults(func=lambda a: update(a.title, a.status, a.object, a.approved, a.premium, a.note, a.rename),
                   command="goals update")

    p = gs.add_parser("remove", help="完成或放弃：整段挪进 goals-done.md")
    p.add_argument("title")
    p.add_argument("--status", required=True, help=f"{DONE} / {DROPPED}")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=lambda a: remove(a.title, a.status, a.reason), command="goals remove")
