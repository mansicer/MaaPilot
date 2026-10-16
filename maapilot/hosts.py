"""agent 宿主（Claude Code、Codex）：定时日常怎么叫起无头 agent、怎么读它的记录，以及怎么把消息推进用户开着的会话。

- 用哪个：config.yaml 的 `agent`（claude / codex，默认 claude），程序路径在 `tools.<宿主>`。
- 无头 agent：
  - claude：`claude -p … --output-format stream-json`，显式带上项目的权限设置，要确认的一律自动拒绝；
  - codex：`codex exec --sandbox workspace-write --json`，项目的 `.codex/rules/` 放行 maapilot、禁止直接碰 adb / maa，
    要确认的命令在 exec 里一律被拒（审批模式固定是"从不"）；关掉 Computer Use、浏览器这些用不上的能力。
    项目规则只有 Codex 信任这个文件夹时才加载（`maapilot host install codex`）。
- 用户的会话：Claude Code 会话里后台挂 `daily watch`，日常跑完它就叫醒 agent；Codex 的后台命令叫不醒模型，
  改由定时日常跑完时 `codex queue` 把消息推进用户开着的会话。会话编号由启动检查（`doctor --brief`）记下：
  Codex 跑的命令带 `CODEX_THREAD_ID`，Claude Code 的带 `CLAUDECODE`。两个宿主各记最近一个会话，
  记在 private/state/agent-session.json（`{"claude": {...}, "codex": {...}}`），开一次 Claude Code 不会让推给 Codex 的消息断掉。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, CliError
from maapilot.oscompat import exe, procs

HOSTS = ("claude", "codex")
DEFAULT = "claude"
QUEUE_TIMEOUT_S = 30
# 无头 codex 用不上、又可能越过 maapilot 去动屏幕或网页的能力
CODEX_DISABLE = ("computer_use", "browser_use", "browser_use_external")


def name(cfg: dict) -> str:
    h = str(cfg.get("agent") or DEFAULT).strip().lower()
    if h not in HOSTS:
        raise CliError("invalid_config", f"config.yaml 的 agent 写的是 {h}，只认 {' / '.join(HOSTS)}", exit=BAD_REQUEST)
    return h


# 定时任务里 PATH 很短（agent 是 claude 时可能没有 codex 所在的目录）：再到这些常见位置找。%VAR% 没有的位置跳过，
# 所以 Windows 的写法放在这里也不影响 macOS / Linux。Windows 上 %LOCALAPPDATA%\Programs\… 是猜的安装位置（没核实）
CODEX_FALLBACKS = ("~/.local/bin/codex", "/opt/homebrew/bin/codex", "/usr/local/bin/codex",
                   r"%APPDATA%\npm\codex.cmd", r"%LOCALAPPDATA%\pnpm\codex.cmd", r"%USERPROFILE%\scoop\shims\codex.exe",
                   r"%USERPROFILE%\.local\bin\codex.exe", r"%LOCALAPPDATA%\Programs\OpenAI\Codex\codex.exe")
# Claude Code：原生安装器装在 ~/.local/bin（Windows 上是 %USERPROFILE%\.local\bin\claude.exe），npm 装的在 npm 的目录
CLAUDE_FALLBACKS = ("~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
                    r"%USERPROFILE%\.local\bin\claude.exe", r"%APPDATA%\npm\claude.cmd", r"%LOCALAPPDATA%\pnpm\claude.cmd")


def fallbacks(host: str) -> tuple[str, ...]:
    return CODEX_FALLBACKS if host == "codex" else CLAUDE_FALLBACKS


def resolve(cfg: dict, host: str, *, which=shutil.which, environ=None, fallback: bool = False) -> str | None:
    """宿主程序的绝对路径；Windows 上找到 npm 的包装时优先原生的 .exe（见 oscompat.exe）。找不到 → None。
    写了 tools.<宿主> 就只认它（写错了要报出来，不悄悄换成别的程序；fallback=True 时再往下找）；
    没写：PATH → 常见安装位置。无头 agent、doctor、host status 用同一个规则，找到的是同一个程序。"""
    configured = (cfg.get("tools") or {}).get(host)
    cands = [str(configured)] if configured else []
    if fallback or not configured:
        cands += [host, *fallbacks(host)]
    return exe.resolve(host, cands, which=which, environ=environ)


def binary(cfg: dict, host: str | None = None) -> str:
    """要执行的宿主程序（找得到就给绝对路径，找不到给 tools.<宿主> 原样的写法）。"""
    host = host or name(cfg)
    return resolve(cfg, host) or str((cfg.get("tools") or {}).get(host) or host)


def codex_path(cfg: dict) -> str | None:
    """推消息用的 codex 的绝对路径：tools.codex 找不到时也往下找（PATH、常见安装位置），推不到只是少一条通知。
    tools.codex 在的话和无头 agent 用的是同一个。找不到 → None。"""
    return resolve(cfg, "codex", fallback=True)


# --- 无头 agent ------------------------------------------------------------------------------------------------------

def last_message_file(transcript: Path) -> Path:
    return transcript.with_suffix(".last.txt")


def prompt_on_stdin(binary_: str) -> bool:
    """提示从 stdin 给、不放在参数里：Windows 上 npm 装的 claude.cmd / codex.cmd 经 cmd.exe 转，多行参数会被截断。"""
    return procs.is_batch(binary_)


def command(host: str, binary_: str, prompt: str, transcript: Path, *, stdin: bool = False) -> list[str]:
    """无头 agent 的命令行。stdin=True：提示不放进参数（claude -p 不带提示、codex exec 写 -，都从 stdin 读）。"""
    if host == "codex":
        # --skip-git-repo-check：项目不是 git 仓库（下载的压缩包）也能跑；--ephemeral：无头会话不留在用户的会话列表里
        argv = [binary_, "exec", "-C", str(paths.ROOT), "--sandbox", "workspace-write", "--json",
                "--skip-git-repo-check", "--ephemeral",
                "-o", str(last_message_file(transcript)), "-c", "notify=[]"]
        for f in CODEX_DISABLE:
            argv += ["--disable", f]
        return argv + ["-" if stdin else prompt]
    # claude：文件夹没被 Claude Code 信任过时，无头 claude 不读项目的 .claude/settings.json，所以显式带上
    return [binary_, "-p", *([] if stdin else [prompt]), "--output-format", "stream-json", "--verbose",
            "--settings", str(paths.ROOT / ".claude" / "settings.json"),
            "--permission-mode", "default", "--permission-prompts", "none"]


def _events(path: Path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    yield ev
    except OSError:
        return


def parse(host: str, transcript: Path) -> dict:
    """agent 的输出记录 → {summary, is_error, cost_usd, turns}（summary 是它最后说的话）。"""
    if host == "codex":
        # 顶层的 error 事件不算失败：断线重连时也会报（"Reconnecting… 1/5"），重连上了这一轮照样做完。
        # 失败看：这一轮报了 turn.failed、没有一轮做完（turn.completed）、或者没留下最后的话。
        summary, failed, turns = None, False, 0
        for ev in _events(transcript):
            t = ev.get("type")
            if t == "turn.completed":
                turns += 1
            elif t == "turn.failed":
                failed = True
            elif t == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
                summary = (ev["item"].get("text") or "").strip() or summary
        try:
            summary = last_message_file(transcript).read_text(encoding="utf-8").strip() or summary
        except OSError:
            pass
        return {"summary": summary, "is_error": failed or not turns or summary is None, "cost_usd": None,
                "turns": turns or None}
    res = None
    for ev in _events(transcript):
        if ev.get("type") == "result":
            res = ev
    if res is None:
        return {"summary": None, "is_error": True}
    return {"summary": (res.get("result") or "").strip() or None, "is_error": bool(res.get("is_error")),
            "cost_usd": res.get("total_cost_usd"), "turns": res.get("num_turns")}


# --- 用户开着的会话 ------------------------------------------------------------------------------------------------

def _session_file() -> Path:
    return paths.STATE / "agent-session.json"


def current_session(environ=None) -> dict | None:
    """这条命令是在哪个宿主的会话里跑的：{host, thread}；看不出来 → None。
    两个变量都有时算 Claude Code：子进程会继承父进程的环境，在 Codex 的终端里开的 claude 也带着那个 Codex 会话的
    CODEX_THREAD_ID，当成 Codex 会话就会把消息推进别的对话；反过来认错（Codex 开在 Claude Code 里）只是少推一次，
    下次启动检查会补上没说过的事。"""
    e = os.environ if environ is None else environ
    if e.get("CLAUDECODE"):
        return {"host": "claude", "thread": e.get("CLAUDE_CODE_SESSION_ID")}
    if e.get("CODEX_THREAD_ID"):
        return {"host": "codex", "thread": e["CODEX_THREAD_ID"]}
    return None


def sessions() -> dict:
    """记下的会话，每个宿主最近一个：{"claude": {thread, at, ts}, "codex": {...}}（没有的不列）。
    旧版只记一个（{host, thread, at}），读的时候按宿主放好。"""
    from maapilot.tools import common
    d = common.read_json(_session_file())
    if not isinstance(d, dict):
        return {}
    if d.get("host") in HOSTS:                          # 旧格式
        return {d["host"]: {k: v for k, v in d.items() if k != "host"}}
    return {h: d[h] for h in HOSTS if isinstance(d.get(h), dict)}


def _when(rec: dict) -> float:
    if isinstance(rec.get("ts"), (int, float)):
        return float(rec["ts"])
    from maapilot.tools import common
    t = common.parse_iso(rec.get("at"))
    return t.timestamp() if t else 0.0


def record_session(environ=None) -> dict | None:
    """启动检查时记下用户的会话（定时日常叫起的无头 agent 不算）。→ 记下的内容 {host, thread, at} 或 None。"""
    import time
    from maapilot.flows.daily.base import DAILY_ENV
    from maapilot.tools import common
    e = os.environ if environ is None else environ
    s = current_session(e)
    if s is None or e.get(DAILY_ENV):
        return None
    all_ = sessions()
    all_[s["host"]] = {"thread": s["thread"], "at": common.iso(), "ts": time.time()}
    common.write_json(_session_file(), all_)
    return {"host": s["host"], **all_[s["host"]]}


def session() -> dict | None:
    """最近的一个会话（不管哪个宿主）：{host, thread, at, ts} 或 None。"""
    all_ = sessions()
    if not all_:
        return None
    h = max(all_, key=lambda k: _when(all_[k]))
    return {"host": h, **all_[h]}


def push(text: str, cfg: dict | None = None) -> dict:
    """把消息推进用户的 Codex 会话（`codex queue`）：会话闲着马上处理，忙着就排在这轮后面，关着就等它恢复。
    推的条件：记过 Codex 会话，并且它比 Claude Code 的会话新、或者 config.yaml 的 agent 是 codex。
    最近用的是 Claude Code（它自己挂着 daily watch）或没记过 → 不推。→ {pushed, reason?, thread?}。"""
    from maapilot import config
    all_ = sessions()
    cx = all_.get("codex")
    if not cx or not cx.get("thread"):
        return {"pushed": False, "reason": "没记下用户的 Codex 会话" if all_ else "没记下用户的会话"}
    cfg = cfg if cfg is not None else config.load_or_defaults()
    try:
        agent = name(cfg)
    except CliError:
        agent = None
    cl = all_.get("claude")
    if agent != "codex" and cl and _when(cl) >= _when(cx):
        return {"pushed": False, "thread": cx["thread"], "reason": "最近的会话是 Claude Code 的"}
    prog = codex_path(cfg)
    if not prog:
        return {"pushed": False, "thread": cx["thread"],
                "reason": "找不到 codex（在 config.yaml 的 tools.codex 写它的绝对路径）"}
    argv = [prog, "queue", "--thread", cx["thread"], "--message", procs.batch_arg(text) if procs.is_batch(prog) else text]
    try:
        p = procs.run_text(argv, timeout=QUEUE_TIMEOUT_S, cwd=str(paths.ROOT))
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"pushed": False, "thread": cx["thread"], "reason": f"codex queue 没跑成：{e}"}
    if p.returncode != 0:
        return {"pushed": False, "thread": cx["thread"],
                "reason": f"codex queue 退出码 {p.returncode}：{(p.stderr or p.stdout).strip()[-200:]}"}
    return {"pushed": True, "thread": cx["thread"]}
