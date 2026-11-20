"""`maapilot feedback …`：用户反馈。agent 起草一份（本机的草稿），在对话里给用户看过、用户同意后才上传。

分工（skill `maapilot-feedback`）：
- agent 写的：用户的反馈原话、这次会话的经过（`--session-file`，要不要另附带工具调用细节的原始记录 `--raw-session-file`
  由 agent 自己整理、自己先脱敏）、它所在的 harness 和模型；
- 程序收集的：版本、系统、环境检查各项通不通过、最近命令的成败和错误代码、最近一次日常各项成败。
  只取骨架：命令的参数、结果摘要（里面有货币数量、干员名）、目标、偏好都不带；
- 程序兜底脱敏（`maapilot.tools.scrub`）：路径、用户名、模拟器地址、森空岛 uid 和昵称、agent 另外指定的字样。

草稿在 `private/feedback/<编号>/`：`bundle/` 里是要上传的全部内容（上传的就是这个文件夹打的包，一个字节不多），
`state.json` 是本机的记录（状态、指定隐去的字样），不上传。
`send` 要带上给用户看过的那一版的 `digest`：草稿之后被改过就对不上，拒绝发送。日常在跑或没收尾时不发（用户多半不在）。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import secrets
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, FAILED, NEEDS_USER, CliError
from maapilot.oscompat import fs
from maapilot.tools import common
from maapilot.tools.scrub import Scrubber

URL = "https://maapilot.sicer.top/feedback"
URL_ENV = "MAAPILOT_FEEDBACK_URL"         # 开发时指到测试用的服务器
SCHEMA = 1
MAX_BYTES = 5 * 1024 * 1024               # 上传的包（压缩后）的上限，服务器同样按这个数拒收
MAX_TEXT = 200_000                        # 用户的话、会话经过：各自最多这么多字
MAX_RAW = 3_000_000                       # 原始会话记录最多这么多字
MAX_SHOTS = 4
JOURNAL_ROWS = 40
MAA_LOG_LINES = 200
PREVIEW_CHARS = 80                        # 清单里带的原话开头：服务器通知开发者时显示
SHOW_CHARS = 20_000                       # `show --part` 一次给多少字
CATEGORIES = ("bug", "experience", "idea", "other")
ID_RE = re.compile(r"fb-\d{8}-\d{6}-[0-9a-f]{4}")

# 上传的文件名（都在 bundle/ 下）
INFO, SESSION, RAW, MAA_LOG, SHOT_DIR = "feedback.json", "session.md", "session-raw.txt", "maa-log.txt", "shots"
PART_NAMES = {"info": INFO, "session": SESSION, "raw-session": RAW, "maa-log": MAA_LOG}
REMOVABLE = ("raw-session", "maa-log", "shots", "contact")

# 说明在哪个 harness 里的环境变量：只记名字在不在；值不是个人信息的这几个连值一起记
HARNESS_ENV = re.compile(r"^(CLAUDE|CODEX|OPENCLAW|PI_|DSH|DEEPSEEK|ANTIGRAVITY|GEMINI|AI_AGENT)", re.I)
HARNESS_ENV_VALUES = ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_DESKTOP_APP_VERSION", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_EFFORT",
                      "AI_AGENT")


def root() -> Path:
    return paths.PRIVATE / "feedback"


def draft_dir(fid: str) -> Path:
    if not ID_RE.fullmatch(fid or ""):
        raise CliError("bad_id", f"反馈编号不对：{fid}", hint="maapilot feedback list", exit=BAD_REQUEST)
    d = root() / fid
    if not (d / "state.json").exists():
        raise CliError("no_draft", f"没有这份反馈草稿：{fid}", hint="maapilot feedback list", exit=BAD_REQUEST)
    return d


def _state(d: Path) -> dict:
    return common.read_json(d / "state.json") or {}


def _save_state(d: Path, st: dict) -> None:
    fs.atomic_write_text(d / "state.json", json.dumps(st, ensure_ascii=False, indent=1) + "\n")


# --- 程序收集的部分 --------------------------------------------------------------------------

def harness_env() -> dict:
    names = sorted(k for k in os.environ if HARNESS_ENV.match(k))
    return {"names": names, "values": {k: os.environ[k] for k in HARNESS_ENV_VALUES if os.environ.get(k)}}


def environment() -> dict:
    from maapilot import config, version
    out = {"maapilot": version.info(), "os": platform.system(), "os_release": platform.release(),
           "arch": platform.machine(), "python": platform.python_version(),
           "setup_done": paths.SETUP.exists(), "server": None}
    try:
        out["server"] = config.server()
    except Exception:                                   # noqa: BLE001
        pass
    return out


def checks() -> list[dict] | None:
    """环境检查（不碰模拟器、不联网的那几项）的骨架：名字、通不通过、说明。跑不了 → None。"""
    try:
        from maapilot.tools import doctor
        items = doctor.run_checks(offline=True)
    except Exception:                                   # noqa: BLE001
        return None
    keep = ("name", "ok", "required", "detail", "version", "core", "warnings")
    return [{k: c[k] for k in keep if k in c} for c in items]


def _error_code(err) -> str | None:
    """命令记录里的 error：错误代码原样；没料到的异常记的是 "异常类型: 原话"，只留类型。"""
    if not isinstance(err, str) or not err:
        return None
    return err.split(":", 1)[0].strip()[:80]


def journal(limit: int = JOURNAL_ROWS) -> list[dict]:
    """最近的命令记录，只留骨架：时间、命令名、成败、退出码、耗时、错误代码。参数和结果摘要不带。"""
    rows: list[dict] = []
    try:
        files = sorted(paths.LOGS.glob("????-??-??.jsonl"))[-3:]
    except OSError:
        return []
    for f in files:
        try:
            lines = fs.read_text_utf8(f).splitlines()
        except (OSError, ValueError):
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            row = {"at": r.get("at"), "command": r.get("command"), "ok": r.get("ok"), "exit": r.get("exit"),
                   "duration_s": r.get("duration_s"), "error": _error_code(r.get("error"))}
            if r.get("run_id"):
                row["run_id"] = r["run_id"]
            rows.append(row)
    return rows[-limit:]


def last_daily() -> dict | None:
    """最近一次日常的骨架：每一项成败和错误代码。没跑过 → None。"""
    try:
        reports = sorted(r for r in paths.REPORTS.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}", r.stem))
    except OSError:
        return None
    if not reports:
        return None
    d = common.read_json(reports[-1]) or {}
    tasks = [{"task": t.get("task"), "ok": t.get("ok"), "code": t.get("code")}
             for t in d.get("tasks") or [] if isinstance(t, dict)]
    handoff = d.get("handoff") if isinstance(d.get("handoff"), dict) else None
    return {"run_id": d.get("run_id"), "trigger": d.get("trigger"), "status": d.get("status"), "ok": d.get("ok"),
            "started_at": d.get("started_at"), "finished_at": d.get("finished_at"), "tasks": tasks,
            "failed": d.get("failed"), "handoff_task": handoff.get("task") if handoff else None}


def broken_sources() -> list[dict]:
    try:
        from maapilot.knowledge import sources
        return [{k: s.get(k) for k in ("source", "at", "code", "message")} for s in sources.unhealthy()]
    except Exception:                                   # noqa: BLE001
        return []


def maa_log_tail(lines: int = MAA_LOG_LINES) -> str | None:
    """最近一份 maa-cli 日志的末尾；没有 → None。"""
    try:
        logs = sorted((paths.LOGS / "maa").glob("*.log"))
    except OSError:
        return None
    if not logs:
        return None
    try:
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return f"# {logs[-1].name}\n" + "\n".join(text.splitlines()[-lines:]) + "\n"


# --- 草稿 ------------------------------------------------------------------------------------

def _read_text(path: str, what: str, limit: int) -> str:
    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise CliError("bad_file", f"读不了{what}（{path}）：{type(e).__name__}", exit=BAD_REQUEST) from None
    if len(text) > limit:
        raise CliError("too_long", f"{what}有 {len(text)} 个字，最多 {limit}", exit=BAD_REQUEST,
                       hint="只留和这个问题有关的那一段")
    if not text.strip():
        raise CliError("bad_file", f"{what}（{path}）是空的", exit=BAD_REQUEST)
    return text


def _shot(path: str) -> Path:
    """截图只收 private/ 下的 PNG（`screen shot` 存的、失败时留的证据）。"""
    p = Path(path).expanduser().resolve()
    try:
        p.relative_to(paths.PRIVATE.resolve())
    except ValueError:
        raise CliError("bad_file", f"截图要是 private/ 下的文件：{path}", exit=BAD_REQUEST) from None
    if p.suffix.lower() != ".png" or not p.is_file():
        raise CliError("bad_file", f"不是 PNG 截图：{path}", exit=BAD_REQUEST)
    return p


def bundle_files(d: Path) -> list[Path]:
    b = d / "bundle"
    return sorted(p for p in b.rglob("*") if p.is_file())


def digest(d: Path) -> str:
    """要上传的内容的指纹：文件名和每个文件的内容。给用户看过哪一版，`send` 就只发哪一版。"""
    h = hashlib.sha256()
    for p in bundle_files(d):
        h.update(p.relative_to(d / "bundle").as_posix().encode() + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()[:16]


def pack(d: Path) -> bytes:
    """bundle/ 打成 zip（文件时间固定：同样的内容打出同样的包）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in bundle_files(d):
            info = zipfile.ZipInfo(p.relative_to(d / "bundle").as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())
    return buf.getvalue()


def _write_bundle(d: Path, info: dict, texts: dict[str, str | None], masks: list[str]) -> dict:
    """把 info 和几段文字脱敏后写进 bundle/（texts 里值是 None 的文件删掉）。返回每一类换了几处。"""
    b = d / "bundle"
    b.mkdir(parents=True, exist_ok=True)
    scrub = Scrubber(masks)
    fs.atomic_write_text(b / INFO, json.dumps(scrub.apply_json(info), ensure_ascii=False, indent=1) + "\n")
    for name, text in texts.items():
        if text is None:
            (b / name).unlink(missing_ok=True)
        else:
            fs.atomic_write_text(b / name, scrub.apply(text))
    return scrub.counts


def summary(d: Path) -> dict:
    """草稿的概况（`draft` / `show` / `redact` 都返回这个）：agent 照着它在对话里告诉用户要上传什么。"""
    st = _state(d)
    b = d / "bundle"
    info = common.read_json(b / INFO) or {}
    parts = []
    for p in bundle_files(d):
        name = p.relative_to(b).as_posix()
        part = {"file": name, "bytes": p.stat().st_size}
        if p.suffix in (".md", ".txt"):
            part["chars"] = len(p.read_text(encoding="utf-8", errors="replace"))
        parts.append(part)
    size = len(pack(d))
    auto = info.get("auto") or {}
    out = {"id": st.get("id"), "status": st.get("status"), "digest": digest(d), "dir": common.rel(d),
           "upload_bytes": size, "too_big": size > MAX_BYTES, "max_bytes": MAX_BYTES, "parts": parts,
           "includes": {"session": (b / SESSION).exists(), "raw_session": (b / RAW).exists(),
                        "maa_log": (b / MAA_LOG).exists(),
                        "shots": [p.name for p in sorted((b / SHOT_DIR).glob("*.png"))] if (b / SHOT_DIR).is_dir() else [],
                        "contact": bool(info.get("contact")),
                        "checks": len(auto.get("checks") or []), "journal": len(auto.get("journal") or []),
                        "last_daily": bool(auto.get("last_daily"))},
           "category": info.get("category"), "harness": info.get("harness"), "model": info.get("model"),
           "scrubbed": st.get("scrubbed") or {}, "masks": len(st.get("masks") or []),
           "url": os.environ.get(URL_ENV) or URL}
    if st.get("status") == "sent":
        out["sent_at"], out["server_id"] = st.get("sent_at"), st.get("server_id")
    return out


def cmd_draft(args) -> dict:
    if args.category not in CATEGORIES:
        raise CliError("bad_arguments", f"--category 只能是 {' / '.join(CATEGORIES)}", exit=BAD_REQUEST)
    text = (args.text or "").strip()
    if not text:
        raise CliError("bad_arguments", "--text（用户的反馈原话）不能是空的", exit=BAD_REQUEST)
    if len(text) > MAX_TEXT:
        raise CliError("too_long", f"--text 有 {len(text)} 个字，最多 {MAX_TEXT}", exit=BAD_REQUEST)
    if not args.harness.strip() or not args.model.strip():
        raise CliError("bad_arguments", "--harness 和 --model 都要写（不确定就写\"不确定\"，不要猜）", exit=BAD_REQUEST)
    session = _read_text(args.session_file, "会话经过", MAX_TEXT)
    raw = _read_text(args.raw_session_file, "原始会话记录", MAX_RAW) if args.raw_session_file else None
    shots = [_shot(s) for s in args.shot or []]
    if len(shots) > MAX_SHOTS:
        raise CliError("bad_arguments", f"截图最多 {MAX_SHOTS} 张", exit=BAD_REQUEST)

    fid = f"fb-{common.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    d = root() / fid
    (d / "bundle").mkdir(parents=True)
    info = {"schema": SCHEMA, "id": fid, "created_at": common.iso(), "category": args.category, "text": text,
            "contact": (args.contact or "").strip() or None,
            "harness": {"name": args.harness.strip(), "version": (args.harness_version or "").strip() or None,
                        "env": harness_env()},
            "model": {"name": args.model.strip(), "effort": (args.effort or "").strip() or None},
            "auto": {"environment": environment(), "checks": None if args.no_checks else checks(),
                     "journal": journal(), "last_daily": last_daily(), "sources": broken_sources()}}
    texts = {SESSION: session, RAW: raw, MAA_LOG: maa_log_tail() if args.maa_log else None}
    masks = [m for m in args.mask or [] if m.strip()]
    counts = _write_bundle(d, info, texts, masks)
    if shots:
        (d / "bundle" / SHOT_DIR).mkdir()
        for i, s in enumerate(shots, 1):
            shutil.copyfile(s, d / "bundle" / SHOT_DIR / f"{i}.png")
    _save_state(d, {"id": fid, "status": "draft", "created_at": info["created_at"], "masks": masks, "scrubbed": counts})
    out = summary(d)
    out["hint"] = ("在对话里给用户看要上传什么（用户的话和会话经过贴全文，别的各说一句），用户说可以再 "
                   f"maapilot feedback send {fid} --digest {out['digest']}")
    return out


def cmd_show(args) -> dict:
    d = draft_dir(args.id)
    out = summary(d)
    if args.part:
        name = PART_NAMES.get(args.part)
        if name is None:
            raise CliError("bad_arguments", f"--part 只能是 {' / '.join(PART_NAMES)}", exit=BAD_REQUEST)
        p = d / "bundle" / name
        if not p.exists():
            raise CliError("no_part", f"这份草稿里没有 {args.part}", exit=BAD_REQUEST)
        text = p.read_text(encoding="utf-8", errors="replace")
        start = max(0, args.offset or 0)
        out["part"] = {"name": args.part, "chars": len(text), "offset": start, "text": text[start:start + SHOW_CHARS],
                       "more": start + SHOW_CHARS < len(text)}
    return out


def cmd_redact(args) -> dict:
    """改草稿：再隐去几处字样、去掉某一部分、换掉用户的话或会话记录。改完 digest 变了，要重新给用户看。"""
    d = draft_dir(args.id)
    st = _state(d)
    if st.get("status") == "sent":
        raise CliError("already_sent", "这份反馈已经发出去了，改不了", exit=BAD_REQUEST)
    b = d / "bundle"
    info = common.read_json(b / INFO) or {}
    for r in args.remove or []:
        if r not in REMOVABLE:
            raise CliError("bad_arguments", f"--remove 只能是 {' / '.join(REMOVABLE)}", exit=BAD_REQUEST)
    removed = set(args.remove or [])
    if args.text is not None:
        if not args.text.strip() or len(args.text) > MAX_TEXT:
            raise CliError("bad_arguments", f"--text 不能是空的，最多 {MAX_TEXT} 个字", exit=BAD_REQUEST)
        info["text"] = args.text.strip()
    if "contact" in removed:
        info["contact"] = None

    def current(name: str) -> str | None:
        return (b / name).read_text(encoding="utf-8", errors="replace") if (b / name).exists() else None
    texts = {SESSION: _read_text(args.session_file, "会话经过", MAX_TEXT) if args.session_file else current(SESSION),
             RAW: None if "raw-session" in removed else
             (_read_text(args.raw_session_file, "原始会话记录", MAX_RAW) if args.raw_session_file else current(RAW)),
             MAA_LOG: None if "maa-log" in removed else current(MAA_LOG)}
    masks = list(dict.fromkeys([*(st.get("masks") or []), *[m for m in args.mask or [] if m.strip()]]))
    counts = _write_bundle(d, info, texts, masks)
    if "shots" in removed:
        shutil.rmtree(b / SHOT_DIR, ignore_errors=True)
    total = dict(st.get("scrubbed") or {})
    for k, v in counts.items():
        total[k] = total.get(k, 0) + v
    st.update(masks=masks, scrubbed=total)
    _save_state(d, st)
    return summary(d)


def cmd_list(args) -> dict:
    out = []
    try:
        dirs = sorted(p for p in root().iterdir() if ID_RE.fullmatch(p.name) and (p / "state.json").exists())
    except OSError:
        dirs = []
    for d in dirs:
        st = _state(d)
        out.append({k: st.get(k) for k in ("id", "status", "created_at", "sent_at", "server_id")})
    return {"drafts": out}


def cmd_discard(args) -> dict:
    d = draft_dir(args.id)
    if _state(d).get("status") == "sent":
        raise CliError("already_sent", "已经发出去的留着备查（里面有反馈编号）", exit=BAD_REQUEST)
    shutil.rmtree(d)
    return {"discarded": args.id}


# --- 上传 ------------------------------------------------------------------------------------

def http_post(url: str, body: bytes, headers: dict, timeout: float = 60) -> tuple[int, bytes]:
    """POST，返回 (状态码, 响应内容)；4xx / 5xx 也这样返回。连不上抛 OSError。测试换成假的。"""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536)


def multipart(manifest: dict, bundle: bytes) -> tuple[bytes, str]:
    boundary = "maapilot-" + secrets.token_hex(12)
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"manifest\"\r\nContent-Type: application/json\r\n\r\n".encode()
        + json.dumps(manifest, ensure_ascii=False).encode("utf-8") + b"\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"bundle\"; filename=\"{manifest['id']}.zip\"\r\n"
        f"Content-Type: application/zip\r\n\r\n".encode() + bundle + b"\r\n",
        f"--{boundary}--\r\n".encode()]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def cmd_send(args) -> dict:
    from maapilot import version
    from maapilot.tools import confirm
    d = draft_dir(args.id)
    st = _state(d)
    if st.get("status") == "sent":
        raise CliError("already_sent", f"这份反馈已经发过了（{st.get('server_id')}）", exit=BAD_REQUEST,
                       data={"server_id": st.get("server_id"), "sent_at": st.get("sent_at")})
    run = confirm.daily_in_progress()
    if run:                                             # 定时日常时用户多半不在：这时的"同意"不算数
        raise CliError("needs_user", f"日常 {run} 还在进行（在跑或没收尾），反馈现在不发", exit=NEEDS_USER,
                       hint="草稿留着；等这次日常 daily done 收尾后，用户在对话里看过、同意了再发")
    now = digest(d)
    if args.digest != now:
        raise CliError("draft_changed", "草稿和给用户看过的那一版不一样了，没有发", exit=BAD_REQUEST,
                       hint=f"maapilot feedback show {args.id}，把现在的内容重新给用户看，同意后带新的 --digest 再发",
                       data={"digest": now})
    bundle = pack(d)
    if len(bundle) > MAX_BYTES:
        raise CliError("too_big", f"要上传的包有 {len(bundle)} 字节，最多 {MAX_BYTES}", exit=BAD_REQUEST,
                       hint="去掉截图或原始会话记录（maapilot feedback redact --remove …），重新给用户看过再发")
    info = common.read_json(d / "bundle" / INFO) or {}
    manifest = {"schema": SCHEMA, "id": st["id"], "created_at": st.get("created_at"), "category": info.get("category"),
                "maapilot": version.info().get("version"), "digest": now, "bytes": len(bundle),
                "preview": " ".join(str(info.get("text") or "").split())[:PREVIEW_CHARS],    # 给开发者的通知用（脱敏后的原话开头）
                "sha256": hashlib.sha256(bundle).hexdigest(), "files": [p.relative_to(d / "bundle").as_posix()
                                                                        for p in bundle_files(d)]}
    body, ctype = multipart(manifest, bundle)
    url = os.environ.get(URL_ENV) or URL
    headers = {"Content-Type": ctype, "User-Agent": f"maapilot/{manifest['maapilot']}", "X-MaaPilot-Feedback": str(SCHEMA)}
    try:
        status, resp = http_post(url, body, headers)
    except (OSError, ValueError) as e:
        raise CliError("network", f"连不上反馈服务器：{type(e).__name__}: {e}", exit=FAILED,
                       hint="草稿还在，检查网络后原样再发一次") from None
    try:
        reply = json.loads(resp.decode("utf-8", "replace")) if resp else {}
    except ValueError:
        reply = {}
    reply = reply if isinstance(reply, dict) else {}
    if status == 429:
        raise CliError("rate_limited", "反馈发得太勤，服务器让等一会儿", exit=FAILED, hint="草稿还在，过一阵再发")
    if status == 413:
        raise CliError("too_big", "服务器嫌这份反馈太大", exit=BAD_REQUEST,
                       hint="去掉截图或原始会话记录，重新给用户看过再发")
    if not 200 <= status < 300:
        raise CliError("upload_failed", f"反馈服务器没收下（HTTP {status}）", exit=FAILED,
                       hint="草稿还在，稍后再发；一直不行就告诉用户去 GitHub 提 issue", data={"status": status})
    st.update(status="sent", sent_at=common.iso(), server_id=str(reply.get("id") or st["id"]))
    _save_state(d, st)
    return {"sent": True, "id": st["id"], "server_id": st["server_id"], "sent_at": st["sent_at"], "bytes": len(bundle)}


def register(sub) -> None:
    g = sub.add_parser("feedback", help="用户反馈：起草、给用户看过、用户同意后上传")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")

    p = gs.add_parser("draft", help="收集、脱敏，存成本机的草稿（不上传）")
    p.add_argument("--text", required=True, help="用户的反馈原话")
    p.add_argument("--session-file", required=True, help="agent 写的会话经过（文本文件，自己先脱敏）")
    p.add_argument("--harness", required=True, help="agent 所在的 harness（如 Claude Code 桌面版）")
    p.add_argument("--model", required=True, help="agent 的模型（不确定就写\"不确定\"）")
    p.add_argument("--harness-version")
    p.add_argument("--effort", help="思考强度等设置（知道才写）")
    p.add_argument("--category", default="other", help=" / ".join(CATEGORIES))
    p.add_argument("--contact", help="用户愿意留的联系方式（用户说了才带）")
    p.add_argument("--raw-session-file", help="带工具调用细节的原始会话记录（用户同意才带；agent 自己整理、先脱敏）")
    p.add_argument("--shot", action="append", help="截图（private/ 下的 PNG；用户同意才带；可以写几次）")
    p.add_argument("--maa-log", action="store_true", help="带上最近一份 MAA 日志的末尾（用户同意才带）")
    p.add_argument("--mask", action="append", help="另外要隐去的字样（原样匹配；可以写几次）")
    p.add_argument("--no-checks", action="store_true", help="不跑环境检查")
    p.set_defaults(func=cmd_draft, command="feedback draft")

    p = gs.add_parser("show", help="草稿的概况；--part 看某一部分的全文")
    p.add_argument("id")
    p.add_argument("--part", help=" / ".join(PART_NAMES))
    p.add_argument("--offset", type=int, default=0)
    p.set_defaults(func=cmd_show, command="feedback show")

    p = gs.add_parser("redact", help="改草稿：再隐去字样、去掉某一部分、换掉文字")
    p.add_argument("id")
    p.add_argument("--mask", action="append")
    p.add_argument("--remove", action="append", help=" / ".join(REMOVABLE))
    p.add_argument("--text")
    p.add_argument("--session-file")
    p.add_argument("--raw-session-file")
    p.set_defaults(func=cmd_redact, command="feedback redact")

    p = gs.add_parser("send", help="上传（用户在对话里同意之后）")
    p.add_argument("id")
    p.add_argument("--digest", required=True, help="给用户看过的那一版的 digest")
    p.set_defaults(func=cmd_send, command="feedback send")

    p = gs.add_parser("list", help="本机的反馈草稿和发过的反馈")
    p.set_defaults(func=cmd_list, command="feedback list")

    p = gs.add_parser("discard", help="丢掉一份没发的草稿")
    p.add_argument("id")
    p.set_defaults(func=cmd_discard, command="feedback discard")

