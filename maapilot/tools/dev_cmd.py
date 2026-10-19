"""`maapilot dev review`：开发用，代替手动翻会话记录。不碰模拟器，只读。
`maapilot dev rules [--check]`：按 maapilot/policy.py 生成两个宿主的权限规则文件（见那里的说明）。

读两样东西，按类归好给开发者看：
- 命令记录 `private/log/<日期>.jsonl`：失败按命令、错误码归类；参数写错（退出码 2）的用法；同一条命令反复失败；
  快捷操作（op / craft / state op）失败后接着手动点（input …）的。
- Claude Code 的会话记录 `~/.claude/projects/<仓库路径把非字母数字换成 ->/*.jsonl`（用户的 agent 和定时日常叫起的无头 agent）：
  每个会话里被拒的工具调用、工具出错、不是 `maapilot …` 单条命令形式的 Bash（管道、&&、cat、grep…）、
  退出码不为 0 的 maapilot 命令、最后的"结果：…"。开发会话（改过代码、跑过测试、`--exclude` 的、当前会话）不算。

这个命令不给用户的 agent 用：不写进 skill、不加放行规则、`maapilot --help` 里也不列。
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, CliError
from maapilot.tools import common

DEFAULT_DAYS = 3
SHORTCUTS = ("op upgrade", "op mastery", "craft", "state op")
MANUAL = ("input tap", "input swipe", "input back", "input text")
MANUAL_WINDOW_MIN = 30          # 快捷操作失败后多久内的手动点击算"接着手动做"
DENIED = ("Permission for this tool use was denied", "Permission for this action was denied",
          "The user doesn't want to proceed with this tool use", "auto mode classifier gave no verdict")
# agent 该用的 Bash 形式：一条 maapilot 命令（不接管道、&&、;、重定向、$()），或放行的 sleep 300
_STANDARD = re.compile(r"^(maapilot\s[^|;&<>`$\n]*|maapilot|sleep 300)$")
_QUOTED = re.compile(r'"(?:\\.|[^"\\])*"|\'[^\']*\'')         # 引号里的 | ; 等不算（如 --find "a|b"）
# 开发会话的迹象：改代码、跑测试、提交
_DEV_PATH = re.compile(r"/(maapilot|tests|docs)/|/\.(claude|agents)/skills/|(CLAUDE|CLAUDE_DEV|AGENTS)\.md$")
_DEV_BASH = re.compile(r"\bpytest\b|\bgit (commit|diff|add)\b|CLAUDE_DEV\.md")


# --- 时间 ---------------------------------------------------------------------------------

def parse_since(s: str | None) -> datetime:
    """`3d`、`12h`、`2026-10-11`、`2026-10-11T10:00`；没给 → DEFAULT_DAYS 天前。"""
    now = common.now()
    if not s:
        return now - timedelta(days=DEFAULT_DAYS)
    m = re.fullmatch(r"(\d+)\s*([dh])", s.strip())
    if m:
        n = int(m.group(1))
        return now - (timedelta(days=n) if m.group(2) == "d" else timedelta(hours=n))
    t = common.parse_iso(s.strip())
    if t is None:
        raise CliError("bad_arguments", f"--since 不认识：{s}（写 3d、12h、2026-10-11 或 2026-10-11T10:00）",
                       exit=BAD_REQUEST)
    return t


def _ts(s) -> datetime | None:
    if not isinstance(s, str):
        return None
    return common.parse_iso(s.replace("Z", "+00:00"))


def _short(s, n: int = 160) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


# --- 命令记录 -------------------------------------------------------------------------------

def log_rows(since: datetime) -> list[dict]:
    out = []
    for f in sorted(paths.LOGS.glob("????-??-??.jsonl")):
        if f.stem < since.date().isoformat():
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            t = _ts(r.get("at")) if isinstance(r, dict) else None
            if t is not None and t >= since:
                r["_t"] = t
                out.append(r)
    out.sort(key=lambda r: r["_t"])
    return out


def review_log(rows: list[dict], limit: int) -> dict:
    fails = [r for r in rows if not r.get("ok")]
    groups: dict[tuple, dict] = {}
    for r in fails:
        k = (r.get("command"), r.get("error") or ("ok=false" if r.get("exit") == 0 else f"exit {r.get('exit')}"))
        g = groups.setdefault(k, {"command": k[0], "error": k[1], "exit": r.get("exit"), "count": 0, "runs": set()})
        g["count"] += 1
        g["last_at"] = r.get("at")
        g["message"] = _short(r.get("message"), 200) or None
        if r.get("run_id"):
            g["runs"].add(r["run_id"])
    failures = sorted(groups.values(), key=lambda g: (-g["count"], g["command"] or ""))
    for g in failures:
        g["runs"] = sorted(g["runs"]) or None

    misuse: dict[tuple, dict] = {}
    for r in fails:
        if r.get("exit") != BAD_REQUEST:
            continue
        argv = r.get("argv") or [*(r.get("command") or "").split(), *(f"{k}={v}" for k, v in (r.get("args") or {}).items())]
        k = (r.get("command"), r.get("error"), r.get("message"))
        m = misuse.setdefault(k, {"command": k[0], "error": k[1], "message": _short(k[2], 200) or None,
                                  "argv": argv, "count": 0})
        m["count"] += 1
        m["last_at"] = r.get("at")

    same: dict[tuple, list] = defaultdict(list)
    for r in fails:
        if r.get("exit") == BAD_REQUEST:
            continue
        same[(r.get("command"), json.dumps(r.get("args") or {}, ensure_ascii=False, sort_keys=True))].append(r)
    repeated = [{"command": c, "args": json.loads(a), "count": len(rs), "errors": sorted({str(x.get("error")) for x in rs}),
                 "first_at": rs[0].get("at"), "last_at": rs[-1].get("at")}
                for (c, a), rs in same.items() if len(rs) >= 2]
    repeated.sort(key=lambda x: -x["count"])

    manual = []
    for i, r in enumerate(rows):
        if r.get("ok") or r.get("command") not in SHORTCUTS or r.get("exit") == BAD_REQUEST:
            continue
        end = r["_t"] + timedelta(minutes=MANUAL_WINDOW_MIN)
        taps = 0
        for x in rows[i + 1:]:
            if x["_t"] > end or x.get("command") == r.get("command"):
                break
            if x.get("command") in MANUAL:
                taps += 1
        if taps:
            manual.append({"command": r.get("command"), "args": r.get("args"), "error": r.get("error"),
                           "stage": r.get("stage"), "at": r.get("at"), "manual_inputs": taps})

    return {"rows": len(rows), "failed": len(fails),
            "failures": failures[:limit * 2], "misuse": sorted(misuse.values(), key=lambda m: -m["count"])[:limit * 2],
            "repeated": repeated[:limit], "shortcut_then_manual": manual[:limit]}


# --- 会话记录 -------------------------------------------------------------------------------

def project_dir(root: Path | None = None) -> Path:
    """Claude Code 存这个仓库会话记录的目录：仓库路径里非字母数字的字符都换成 -。
    在 git worktree（`<仓库>/.claude/worktrees/<名>`）里跑时看主仓库的。"""
    root = Path(root or paths.ROOT).resolve()
    parts = root.parts
    if len(parts) > 3 and parts[-3:-1] == (".claude", "worktrees"):
        root = Path(*parts[:-3])
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root))


def agent_labels() -> dict[str, str]:
    """定时日常叫起的无头 agent：会话编号 → "日常 <运行编号> <项>"（看 private/log/agent-*.jsonl 的第一行）。"""
    out = {}
    for f in paths.LOGS.glob("agent-*.jsonl"):
        try:
            with open(f, encoding="utf-8") as fh:
                first = json.loads(fh.readline() or "{}")
        except (OSError, ValueError):
            continue
        sid = first.get("session_id")
        m = re.fullmatch(r"agent-(\d{8}-\d{4})-\d+-(.+)", f.stem)
        if sid and m:
            out[sid] = f"日常 {m.group(1)} {m.group(2)}"
    return out


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return ""


def _envelope(text: str) -> dict | None:
    i = text.find("{")
    if i < 0:
        return None
    try:
        d = json.loads(text[i:])
        return d if isinstance(d, dict) and "ok" in d else None
    except ValueError:
        m = re.search(r'"code":\s*"([^"]+)"', text)
        return {"error": {"code": m.group(1)}} if m else None


def _maapilot_cmd(cmd: str) -> str | None:
    """Bash 命令里的 maapilot 命令名（前两个词）；没有 → None。"""
    m = re.search(r"(?:^|[\s/;&|(])maapilot\s+([^\s|;&<>]+)(?:\s+([^\s|;&<>-][^\s|;&<>]*))?", cmd)
    if not m:
        return None
    sub = m.group(2) if m.group(2) and re.fullmatch(r"[a-z]+", m.group(2)) else None
    return " ".join(x for x in (m.group(1), sub) if x)


def read_session(path: Path, since: datetime) -> dict | None:
    uses: dict[str, dict] = {}
    s = {"id": path.stem, "start": None, "end": None, "prompt": None, "dev": None, "agent_name": None,
         "denied": [], "tool_errors": [], "maapilot_failed": [], "bash_nonstandard": [], "bash_total": 0,
         "result": None, "in_window": False}
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return None
    with fh:
        for ln in fh:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if d.get("agentName") and not s["agent_name"]:
                s["agent_name"] = d["agentName"]
            t = _ts(d.get("timestamp"))
            if t is not None:
                s["start"] = s["start"] or t
                s["end"] = t
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            content = msg.get("content")
            if d.get("type") == "user" and isinstance(content, str) and not d.get("isMeta") and s["prompt"] is None:
                if not content.startswith("<"):
                    s["prompt"] = _short(content, 120)
            if not isinstance(content, list):
                continue
            new = t is None or t >= since
            if new:
                s["in_window"] = True
            for x in content:
                if not isinstance(x, dict):
                    continue
                if x.get("type") == "tool_use":
                    inp = x.get("input") if isinstance(x.get("input"), dict) else {}
                    uses[x.get("id")] = {"name": x.get("name"), "input": inp}
                    name = x.get("name")
                    if name in ("Edit", "Write", "NotebookEdit") and _DEV_PATH.search(str(inp.get("file_path", ""))):
                        s["dev"] = s["dev"] or f"改了 {Path(str(inp['file_path'])).name}"
                    if name == "Bash":
                        cmd = str(inp.get("command", "")).strip()
                        if _DEV_BASH.search(cmd):
                            s["dev"] = s["dev"] or f"跑了 {_short(cmd, 40)}"
                        if new:
                            s["bash_total"] += 1
                            bare = _QUOTED.sub('""', cmd)
                            if not _STANDARD.match(bare):
                                kind = "compound" if re.search(r"\||&&|;|>|\$\(|`|\n", bare) else "other"
                                s["bash_nonstandard"].append({"kind": kind, "first": cmd.split()[0] if cmd else "",
                                                              "cmd": _short(cmd, 120)})
                elif x.get("type") == "tool_result" and new:
                    text = _text(x.get("content"))
                    use = uses.get(x.get("tool_use_id")) or {}
                    what = _short(use.get("input", {}).get("command") or use.get("input", {}).get("file_path")
                                  or use.get("input", {}), 120)
                    if any(p in text for p in DENIED):
                        s["denied"].append({"tool": use.get("name"), "what": what, "why": _short(text, 100)})
                        continue
                    if not x.get("is_error"):
                        continue
                    cmd = str(use.get("input", {}).get("command", "")) if use.get("name") == "Bash" else ""
                    mp = _maapilot_cmd(cmd) if cmd else None
                    m = re.match(r"Exit code (\d+)", text)
                    if mp and m:
                        env = _envelope(text) or {}
                        err = env.get("error") or {}
                        s["maapilot_failed"].append({"command": env.get("command") or mp, "exit": int(m.group(1)),
                                                     "error": err.get("code"), "message": _short(err.get("message"), 160) or None,
                                                     "cmd": _short(cmd, 120)})
                    else:
                        s["tool_errors"].append({"tool": use.get("name"), "what": what, "error": _short(text, 160)})
                elif x.get("type") == "text" and d.get("type") == "assistant" and new:
                    lines = [l.strip() for l in str(x.get("text", "")).splitlines() if l.strip().startswith("结果：")]
                    if lines:
                        s["result"] = lines[-1]
    return s


def _group(items: list[dict], key, limit: int) -> list[dict]:
    """同样的条目合并，带 count。"""
    c = Counter()
    first = {}
    for it in items:
        k = key(it)
        c[k] += 1
        first.setdefault(k, it)
    return [{**first[k], **({"count": n} if n > 1 else {})} for k, n in c.most_common(limit)]


def summarize_session(s: dict, label: str | None, limit: int) -> dict:
    nonstd = s["bash_nonstandard"]
    def local(t):
        return t.astimezone().isoformat(timespec="minutes") if t else None
    out = {"session": s["id"], "label": label, "start": local(s["start"]), "end": local(s["end"]), "prompt": s["prompt"],
           "result": s["result"], "bash": s["bash_total"]}
    if s["denied"]:
        out["denied"] = _group(s["denied"], lambda d: (d["tool"], d["what"]), limit)
    if s["maapilot_failed"]:
        out["maapilot_failed"] = _group(s["maapilot_failed"], lambda d: (d["command"], d["exit"], d["error"]), limit)
    if s["tool_errors"]:
        out["tool_errors"] = _group(s["tool_errors"], lambda d: (d["tool"], d["error"][:60]), limit)
    if nonstd:
        out["bash_nonstandard"] = {"count": len(nonstd),
                                   "by_first_word": dict(Counter(n["first"] for n in nonstd).most_common(8)),
                                   "compound": sum(1 for n in nonstd if n["kind"] == "compound"),
                                   "examples": [n["cmd"] for n in nonstd[:min(limit, 5)]]}
    return out


def review_transcripts(since: datetime, tdir: Path, only: list[str] | None, exclude: list[str],
                       include_dev: bool, limit: int) -> dict:
    if not tdir.is_dir():
        return {"dir": str(tdir), "note": "没有这个目录（还没在这个仓库里开过 Claude Code？）", "sessions": []}
    me = os.environ.get("CLAUDE_CODE_SESSION_ID")
    labels = agent_labels()
    sessions, skipped = [], []
    for f in sorted(tdir.glob("*.jsonl")):
        sid = f.stem
        if only and not any(sid.startswith(o) for o in only):
            continue
        try:
            if f.stat().st_mtime < since.timestamp():
                continue
        except OSError:
            continue
        if any(sid.startswith(e) for e in exclude):
            skipped.append({"session": sid, "why": "--exclude"})
            continue
        if me and sid == me and not only:
            skipped.append({"session": sid, "why": "当前会话"})
            continue
        s = read_session(f, since)
        if s is None or not s["in_window"]:
            continue
        if not include_dev and not only:
            dev = s["dev"] or (f"开发 agent {s['agent_name']}" if str(s["agent_name"] or "").startswith("dev") else None)
            if dev:
                skipped.append({"session": sid, "why": dev})
                continue
        sessions.append(summarize_session(s, labels.get(sid), limit))
    sessions.sort(key=lambda x: x["start"] or "")
    return {"dir": str(tdir), "sessions": sessions, "excluded": skipped}


# --- 汇总 ---------------------------------------------------------------------------------

def _lines(log: dict, tr: dict) -> list[str]:
    out = [f"命令记录 {log['rows']} 行，失败 {log['failed']}"]
    for g in log["failures"][:5]:
        out.append(f"  失败 {g['command']} / {g['error']} ×{g['count']}" + (f"：{_short(g['message'], 60)}" if g.get("message") else ""))
    for m in log["misuse"][:5]:
        out.append(f"  用错 {' '.join(map(str, m['argv']))} ×{m['count']}：{_short(m.get('message'), 60)}")
    for r in log["repeated"][:3]:
        out.append(f"  反复失败 {r['command']} {json.dumps(r['args'], ensure_ascii=False)} ×{r['count']}")
    for r in log["shortcut_then_manual"][:3]:
        out.append(f"  快捷操作失败后手动点 {r['command']} {json.dumps(r.get('args'), ensure_ascii=False)}"
                   f"（{r.get('stage') or r.get('error') or '旧记录没写原因'}）→ {r['manual_inputs']} 次 input")
    ss = tr.get("sessions") or []
    out.append(f"会话 {len(ss)} 个（排除 {len(tr.get('excluded') or [])}）")
    for s in ss:
        bits = []
        for k, name in (("denied", "被拒"), ("maapilot_failed", "maapilot 失败"), ("tool_errors", "工具出错")):
            if s.get(k):
                bits.append(f"{name} {sum(x.get('count', 1) for x in s[k])}")
        if s.get("bash_nonstandard"):
            bits.append(f"非标准 Bash {s['bash_nonstandard']['count']}/{s['bash']}")
        out.append(f"  {s['session'][:8]} {s['label'] or _short(s['prompt'], 30)} {s['start'] or ''}："
                   + ("、".join(bits) or "没问题") + (f"；{_short(s['result'], 60)}" if s.get("result") else ""))
    return out


def review(since: datetime, *, sessions: list[str] | None = None, exclude: list[str] | None = None,
           include_dev: bool = False, transcripts: Path | None = None, limit: int = 10) -> dict:
    rows = log_rows(since)
    log = review_log(rows, limit)
    tr = review_transcripts(since, Path(transcripts).expanduser() if transcripts else project_dir(),
                            sessions, exclude or [], include_dev, limit)
    return {"since": since.isoformat(timespec="minutes"), "lines": _lines(log, tr), "log": log, "transcripts": tr}


def cmd_review(args):
    return review(parse_since(args.since), sessions=args.session, exclude=args.exclude or [],
                  include_dev=args.include_dev, transcripts=args.transcripts, limit=max(1, args.limit))


def cmd_rules(args):
    """生成（或 --check 只核对）.claude/settings.json 的权限、.codex/rules/maapilot.rules、docs/cli.md 里抄的设置。"""
    from maapilot import policy
    root = Path(args.root) if args.root else paths.ROOT
    files = [{"path": p.as_posix(), "changed": now != want} for p, now, want in policy.generated(root)]
    stale = [f["path"] for f in files if f["changed"]]
    if args.check:
        if stale:
            raise CliError("rules_out_of_date", f"这些文件和 maapilot/policy.py 生成的不一样：{'、'.join(stale)}",
                           hint="改规则改 maapilot/policy.py，再跑 maapilot dev rules；不要手改这几个文件", data={"files": files})
        return {"files": files, "written": False}
    for p, now, want in policy.generated(root):
        if now != want:
            (root / p).parent.mkdir(parents=True, exist_ok=True)
            (root / p).write_text(want, encoding="utf-8", newline="\n")
    return {"files": files, "written": bool(stale)}


def register(sub) -> None:
    g = sub.add_parser("dev")               # 不给 help：开发用，`maapilot --help` 里不列
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("review", help="看命令记录和会话记录里的失败、用错、被拒（开发用，只读）")
    p.add_argument("--since", help=f"从什么时候起：3d、12h、2026-10-11、2026-10-11T10:00（默认 {DEFAULT_DAYS} 天）")
    p.add_argument("--session", action="append", metavar="编号", help="只看这个会话（编号开头几位就行；可多次）")
    p.add_argument("--exclude", action="append", metavar="编号", help="不看这个会话（可多次；当前会话自动不看）")
    p.add_argument("--include-dev", action="store_true", help="也看开发会话（改过代码、跑过测试的）")
    p.add_argument("--transcripts", metavar="目录", help="会话记录目录（默认按仓库路径推出 ~/.claude/projects/ 下的）")
    p.add_argument("--limit", type=int, default=10, help="每类最多列几条")
    p.set_defaults(func=cmd_review, command="dev review")
    p = gs.add_parser("rules", help="按 maapilot/policy.py 生成两个宿主的权限规则文件（开发用）")
    p.add_argument("--check", action="store_true", help="只核对，不一样就退出码 1")
    p.add_argument("--root", metavar="目录", help="仓库目录（默认这个仓库；测试用）")
    p.set_defaults(func=cmd_rules, command="dev rules")
