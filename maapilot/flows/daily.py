"""`maapilot daily run / status / schedule / unschedule`：日常与定时。契约见 docs/cli.md「daily」，设计见 docs/design.md 第 6 节。

daily run 只做编排，每一步都调别的命令（子进程 `python -m maapilot.cli …`，继承本进程的模拟器锁）：
  startup（配了的话）→ guard snapshot（上次之后有人动过就改做对账）→ 其余 MAA 日常项（award 留到最后）
  → 刷理智（有进行中的目标交给 agent，没有就刷默认关）→ award → guard check → 汇报。
定时运行（--trigger schedule）里某项失败，当场起一个无头 `claude -p`（skill maapilot-recovery）接管这一项，
等它结束（超时杀掉整个进程组）再接着跑；每个 agent 结束后换一次锁的 token（它留下的命令不能再进来）、对一次账。
手动运行（默认，agent 在会话里）不起无头 agent，失败记下交回在场的 agent；有进行中的目标时刷理智和 award
都交给在场的 agent（goals_pending），它做完自己领奖励、对账、补汇报。

定时：crontab 里本项目自己的行带标记 `# maapilot-v2 <仓库路径>`，只增删这些行，别的行一个字节都不动。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from maapilot import cliutil, config, paths
from maapilot.cliutil import BAD_REQUEST, BUSY, ENVIRONMENT, FAILED, NEEDS_USER, CliError
from maapilot.crew import lock as lockmod
from maapilot.flows import goals as goalsmod
from maapilot.flows import report as reportmod
from maapilot.flows._common import journal, now, rel

DAILY_ENV = "MAAPILOT_DAILY_RUN"          # 无头 agent 的环境里带着它：agent 里不能再起一次 daily run
AGENT_TIMEOUT_MIN = 90                    # 做目标部分的无头 agent 最长跑多久（config: daily.agent_timeout_min）
RECOVERY_TIMEOUT_MIN = 30                 # 接管失败项的无头 agent 最长跑多久（config: daily.recovery_timeout_min）
TASK_TIMEOUT_MIN = 90                     # 一个 MAA 日常项最长跑多久（config: daily.task_timeout_min）
FARM = "farm"                             # 刷理智（不在 daily.tasks 里；--only 里可以写）


def _marker() -> Path:
    return paths.PRIVATE / "daily-run.json"


# --- 子进程 ----------------------------------------------------------------------------------------------------------

def _run_group(cmd: list[str], *, env: dict, timeout_s: float | None, stdout=subprocess.PIPE,
               cwd: Path | None = None) -> tuple[int | None, str, bool]:
    """跑一个子进程（自成进程组）；超时 → 先 TERM 再 KILL 整个进程组。→ (退出码或 None, stdout 文本, 是否超时)。"""
    p = subprocess.Popen(cmd, cwd=str(cwd or paths.ROOT), env=env, stdout=stdout, stderr=None,
                         stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                         start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout_s)
        return p.returncode, out or "", False
    except subprocess.TimeoutExpired:
        for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
            try:
                os.killpg(p.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                out, _ = p.communicate(timeout=wait)
                return p.returncode, out or "", True
            except subprocess.TimeoutExpired:
                continue
        return None, "", True


def maapilot_call(args: list[str], env: dict, timeout_s: float | None = None) -> tuple[int, dict]:
    """调一条 maapilot 命令（同一个 Python、同一份代码），返回 (退出码, 信封)。测试里替换掉。"""
    cmd = [sys.executable, "-m", "maapilot.cli", *args]
    code, out, timed_out = _run_group(cmd, env=env, timeout_s=timeout_s)
    if timed_out:
        return FAILED, {"ok": False, "data": None,
                        "error": {"code": "timeout", "message": f"超过 {int((timeout_s or 0) // 60)} 分钟没结束，已停下"}}
    try:
        env_ = json.loads(out)
        if not isinstance(env_, dict):
            raise ValueError
    except ValueError:
        env_ = {"ok": False, "data": None,
                "error": {"code": "bad_output", "message": (out or "").strip()[-300:] or "没有输出"}}
    return (code if code is not None else FAILED), env_


# --- 无头 agent ------------------------------------------------------------------------------------------------------

def _claude_bin(cfg: dict) -> str:
    c = str((cfg.get("tools") or {}).get("claude") or "claude")
    return shutil.which(c) or c


def agent_command(claude: str, prompt: str) -> list[str]:
    """无头 agent 的命令行：要弹确认的一律自动拒绝。显式带上项目的权限设置（--settings）：
    文件夹没被 Claude Code 信任过时，无头 claude 不读项目的 .claude/settings.json。"""
    return [claude, "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--settings", str(paths.ROOT / ".claude" / "settings.json"),
            "--permission-mode", "default", "--permission-prompts", "none"]


_OUTCOME = re.compile(r"结果[：:]\s*(完成|没做成|做完|失败|处理好了|没处理好)")


def parse_transcript(path: Path) -> dict:
    """stream-json 输出里最后一条 result：总结、出错没有、花费。"""
    res = None
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
                if ev.get("type") == "result":
                    res = ev
    except OSError:
        pass
    if res is None:
        return {"summary": None, "is_error": True}
    summary = (res.get("result") or "").strip() or None
    m = None
    for m in _OUTCOME.finditer(summary or ""):
        pass
    recovered = None if m is None else m.group(1) in ("完成", "做完", "处理好了")
    return {"summary": summary, "is_error": bool(res.get("is_error")), "recovered": recovered,
            "cost_usd": res.get("total_cost_usd"), "turns": res.get("num_turns")}


def run_agent(cfg: dict, prompt: str, env: dict, transcript: Path, *, recovery: bool = False) -> dict:
    """起一个无头 claude -p 并等它结束。→ {ok, summary, recovered, timed_out, transcript, duration_s, …}。"""
    dcfg = cfg.get("daily") or {}
    if recovery:
        timeout_s = float(dcfg.get("recovery_timeout_min") or RECOVERY_TIMEOUT_MIN) * 60
    else:
        timeout_s = float(dcfg.get("agent_timeout_min") or AGENT_TIMEOUT_MIN) * 60
    transcript.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    cmd = agent_command(_claude_bin(cfg), prompt)
    cliutil.log(f"[daily] 叫无头 agent（最长 {int(timeout_s // 60)} 分钟），记录在 {rel(transcript)}")
    try:
        with open(transcript, "w", encoding="utf-8") as out:
            code, _, timed_out = _run_group(cmd, env=env, timeout_s=timeout_s, stdout=out)
    except OSError as e:
        return {"ok": False, "error": f"起不来 claude（{cmd[0]}）：{e}", "recovered": False,
                "transcript": rel(transcript), "duration_s": 0}
    info = parse_transcript(transcript)
    info.update(ok=(code == 0 and not timed_out and not info.get("is_error")), exit=code, timed_out=timed_out,
                transcript=rel(transcript), duration_s=round(time.monotonic() - t0, 1))
    if timed_out:
        info["recovered"] = False
    return info


def evidence_lines(ev: dict | None, task: str) -> list[str]:
    """失败取证（maa 命令的 data.evidence）→ 给 agent 的几行提示。"""
    if not ev:
        return []
    out = []
    if ev.get("shot_now"):
        out.append(f"失败那一刻的截图：{ev['shot_now']}")
    shots = ev.get("maa_shots") or []
    if shots:
        out.append("MAA 自己在这段时间存的截图（按时间）：" + "；".join(f"{s.get('at', '')[11:19]} {s['path']}" for s in shots))
    if ev.get("missing"):
        out.append("没取到：" + "；".join(ev["missing"]))
    out.append(f"查原因：maapilot maa why --task {task}（MAA 详细日志里这段时间的经过、最后的错误、推断的原因）。")
    return out


def recovery_prompt(run_id: str, task: str, err: dict, data: dict | None, argv: list[str] | None = None) -> str:
    log = (data or {}).get("log")
    cmd = " ".join(shlex.quote(a) for a in (argv or [task]))
    why_task = (argv or [task])[0]
    return "\n".join([
        f"你是 MaaPilot 的无头 agent，被定时日常（运行 {run_id}）叫醒，用户不在场。",
        f"日常项「{reportmod.label(task)}」（maapilot maa {cmd}）刚才失败了：{err.get('message') or err.get('code')}。",
        f"MAA 日志：{log}。" if log else "",
        *evidence_lines((data or {}).get("evidence"), why_task),
        "加载 skill maapilot-recovery，只处理这一项：先看上面的截图、查原因，再截图看现在停在哪，处理卡点，需要时重跑这一项，最后回到游戏首页。",
        "被登出、掉线（停在开始唤醒的标题页）：用户不在场，直接重新登录（maapilot maa startup）再重跑这一项，并写进总结。",
        "不做别的日常项、不刷理智、不改目标。要用户决定或确认的事一律不做，写进总结。",
        "先 maapilot lock status：inherited 应为 true（日常把模拟器锁交给了你）；不是就别操作，写进总结后结束。",
        "长命令（maa 任务）在前台跑、Bash 超时设 600000 毫秒，不放后台。",
        "结束时用几句中文写做了什么、现在怎样；最后一行单独写「结果：完成」或「结果：没做成」。",
    ]).replace("\n\n", "\n")


def goals_prompt(run_id: str, active: list[dict], tasks: list[dict]) -> str:
    done = "、".join(f"{reportmod.label(t['task'])}{'' if t.get('ok') else '（没做成）'}" for t in tasks) or "无"
    titles = "；".join(f"{g['title']}（{g.get('object') or '?'}）" for g in active)
    return "\n".join([
        f"你是 MaaPilot 的无头 agent，被定时日常（运行 {run_id}）叫醒，用户不在场。",
        f"MAA 日常项已经跑完：{done}。",
        f"private/goals.md 里有 {len(active)} 个进行中的目标：{titles}。",
        "加载 skill maapilot-daily，做目标部分：用剩下的理智刷目标缺的材料，材料够了就养成，"
        "在 goals.md 里更新分解与进展，做完的目标移除。",
        "用户不在场：目标之外的花费、要用户决定或确认的事一律不做，写进总结。",
        "先 maapilot lock status：inherited 应为 true（日常把模拟器锁交给了你）；不是就别操作，写进总结后结束。",
        "长命令在前台跑、Bash 超时设 600000 毫秒，不放后台；刷图按 skill 里说的分批（--times 每批几次）。",
        "结束时用几句中文写做了什么、花了什么、目标进展到哪，以及要用户决定的事；最后一行单独写「结果：完成」或「结果：没做成」。",
    ])


# --- 运行标记（同一时间只跑一次日常） ---------------------------------------------------------------------------------

def _read_marker() -> dict | None:
    try:
        return json.loads(_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def running() -> dict | None:
    """正在跑的日常（标记在、持有进程还活着）；没有 → None。"""
    d = _read_marker()
    if not d:
        return None
    if d.get("host") == socket.gethostname() and d.get("pid") and not lockmod.pid_alive(int(d["pid"])):
        return None
    return d


def _take_marker(run_id: str, trigger: str) -> None:
    cur = running()
    if cur:
        raise CliError("daily_running", f"日常 {cur.get('run_id')} 正在跑（{cur.get('started_at', '')[:16]} 开始）",
                       hint="等它跑完；daily status 看进度", exit=BUSY, data={"running": cur})
    _marker().parent.mkdir(parents=True, exist_ok=True)
    body = {"run_id": run_id, "trigger": trigger, "pid": os.getpid(), "host": socket.gethostname(),
            "started_at": now().isoformat(timespec="seconds"), "step": None}
    try:
        fd = os.open(_marker(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if running():                                # 刚被别人抢到
            raise CliError("daily_running", "另一个日常刚开始", exit=BUSY) from None
        _marker().unlink(missing_ok=True)            # 残留的标记
        fd = os.open(_marker(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)


def _set_step(step: str | None) -> None:
    d = _read_marker()
    if d and d.get("pid") == os.getpid():
        d["step"] = step
        tmp = _marker().with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _marker())


def _drop_marker() -> None:
    d = _read_marker()
    if d and d.get("pid") == os.getpid():
        _marker().unlink(missing_ok=True)


# --- 仓库差 ----------------------------------------------------------------------------------------------------------

def _depot_snapshot() -> dict | None:
    try:
        d = json.loads((paths.STATE / "depot.json").read_text(encoding="utf-8"))
        return d if isinstance(d.get("items"), dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def depot_change(before: dict | None, after: dict | None) -> list[dict] | None:
    """两份仓库快照的差（按变化量从大到小的绝对值排）；少一份或是同一份 → None。"""
    if not before or not after or before.get("at") == after.get("at"):
        return None
    try:
        from maapilot.knowledge.items import display_name
    except Exception:                                    # noqa: BLE001
        display_name = str
    a, b = before["items"], after["items"]
    out = []
    for k in sorted(set(a) | set(b)):
        delta = int(b.get(k, 0)) - int(a.get(k, 0))
        if delta:
            try:
                name = display_name(str(k))
            except Exception:                            # noqa: BLE001 - 没有数据表时用 id
                name = str(k)
            out.append({"id": str(k), "name": name, "delta": delta})
    return sorted(out, key=lambda c: -abs(c["delta"]))


# --- daily run -------------------------------------------------------------------------------------------------------

def _new_run_id() -> str:
    t = now()
    rid = f"{t:%Y%m%d-%H%M}"
    if (paths.REPORTS / f"{reportmod.stem(rid)}.json").exists():
        rid = f"{t:%Y%m%d-%H%M%S}"
    return rid


class _Run:
    """一次日常的记录：tasks、失败、告警；每一步写进 private/log/daily-<run_id>.jsonl，汇报随时可存。"""

    def __init__(self, run_id: str, trigger: str):
        self.data = {"run_id": run_id, "trigger": trigger, "started_at": now().isoformat(timespec="seconds"),
                     "finished_at": None, "status": "running", "ok": None, "tasks": [], "failed": [],
                     "goals_active": 0, "goals_pending": False, "farm": None, "depot_change": None,
                     "guard": {"ok": None}, "alerts": [], "agents": 0}
        self.events = paths.LOGS / f"daily-{run_id}.jsonl"

    def event(self, kind: str, **kw) -> None:
        try:
            self.events.parent.mkdir(parents=True, exist_ok=True)
            with open(self.events, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": now().isoformat(timespec="seconds"), "event": kind, **kw},
                                   ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def save(self) -> None:
        try:
            reportmod.save(self.data)
        except OSError as e:
            cliutil.log(f"[daily] 汇报写不了：{e}")


class _Stop(Exception):
    """对账不过：整个日常停下。"""


def run(only: list[str] | None, trigger: str, no_agent: bool) -> dict:
    if os.environ.get(DAILY_ENV):
        raise CliError("nested_daily", f"这是日常 {os.environ[DAILY_ENV]} 叫起来的 agent，不能再跑一次日常",
                       hint="只处理交给你的那一项", exit=BAD_REQUEST)
    cfg = config.load()
    dcfg = cfg.get("daily") or {}
    tasks = [str(t) for t in (dcfg.get("tasks") or [])]
    if only:
        unknown = [t for t in only if t not in tasks and t != FARM]
        if unknown:
            raise CliError("bad_arguments", f"--only 里有不在 daily.tasks 里的项：{', '.join(unknown)}",
                           hint=f"能写的：{', '.join(tasks + [FARM])}", exit=BAD_REQUEST)
    stop_file = paths.GUARD / "stop.json"
    if stop_file.exists():
        raise CliError("guard_stop", "合成玉 / 源石对账停机中，日常不开始", exit=NEEDS_USER,
                       hint="先把停机的原因告诉用户（guard status），用户说明后再处理")
    run_id = _new_run_id()
    headless = trigger == "schedule" and not no_agent
    _take_marker(run_id, trigger)
    t0 = time.monotonic()
    r = _Run(run_id, trigger)
    code = 0
    try:
        with cliutil.device("daily run", job="daily") as lk:
            env = {**lk.child_env(), DAILY_ENV: run_id, "MAAPILOT_RUN_ID": run_id}
            with lk.keepalive():
                _steps(r, cfg, tasks, only, headless, env, lk)
    except BaseException as e:                          # 被占用、连不上、中途出错：汇报里写明没跑完
        code = e.exit if isinstance(e, CliError) else FAILED
        r.data.update(status="aborted", ok=False)
        msg = e.message if isinstance(e, CliError) else f"{type(e).__name__}: {e}"
        r.data["alerts"].append(f"日常没跑完：{msg}")
        raise
    finally:
        r.data["finished_at"] = now().isoformat(timespec="seconds")
        if r.data["status"] == "running":
            r.data["status"] = "done"
        r.save()
        r.event("end", status=r.data["status"], ok=r.data["ok"])
        journal("daily run", {"only": only, "trigger": trigger, "no_agent": no_agent}, bool(r.data["ok"]),
                code, time.monotonic() - t0, run_id=run_id, depot_change=r.data.get("depot_change"))
        _drop_marker()
    return _finish(r)


def _finish(r: _Run) -> dict:
    d = r.data
    if d["status"] == "stopped":
        raise CliError("currency_decreased", d.get("stop_reason") or "合成玉 / 源石有没经同意的减少，日常停下了",
                       hint="把这笔减少告诉用户，等用户说明（guard status）", exit=NEEDS_USER, data=d)
    if d.get("handoff"):
        h = d["handoff"]
        raise CliError("handoff", f"{reportmod.label(h['task'])}没做成，日常停在这里交给你：{h['error']}",
                       hint=f"按 maapilot-recovery 先看 handoff.evidence 的截图、maapilot maa why 查原因，处理好再 "
                            f"maapilot daily run --only {','.join(h['rest'])} 接着跑",
                       exit=FAILED, data=d)
    if d["failed"]:
        raise CliError("daily_incomplete", f"有 {len(d['failed'])} 项没做成：{'、'.join(map(reportmod.label, d['failed']))}",
                       hint="看 data.tasks 里的原因；在场的话截图看看、处理后 daily run --only <项> 补跑",
                       exit=FAILED, data=d)
    return d


def _guard(r: _Run, which: str, env: dict, after: str) -> None:
    """guard snapshot / check。check 不过 → 记下并 _Stop；命令本身没做成 → 告警，接着跑。"""
    code, env_ = maapilot_call(["guard", which], env, timeout_s=15 * 60)
    r.event(f"guard_{which}", after=after, exit=code, error=env_.get("error"))
    if which == "snapshot" and code != NEEDS_USER:       # 退出码 3：上次之后有人动过，改做的对账没过
        if code != 0:
            r.data["alerts"].append(f"开始前没能记下合成玉、源石（{(env_.get('error') or {}).get('message')}），"
                                    "这次对账以上一次的记录为准")
        return
    if code == NEEDS_USER:
        g = env_.get("data") or {}
        r.data["guard"] = {"ok": False, "after": after, **{k: g.get(k) for k in ("before", "after", "unexplained")}}
        r.data["stop_reason"] = f"{after}之后发现合成玉 / 源石有没经同意的减少"
        r.data["status"] = "stopped"
        raise _Stop()
    if code == 0:
        r.data["guard"] = {"ok": True}
    else:
        r.data["guard"] = {"ok": None, "error": (env_.get("error") or {}).get("message")}


def _maa_task(r: _Run, cfg: dict, task: str, args: list[str], headless: bool, env: dict, lk) -> dict:
    timeout_s = float((cfg.get("daily") or {}).get("task_timeout_min") or TASK_TIMEOUT_MIN) * 60
    _set_step(task)
    lk.heartbeat(step=task)
    cliutil.log(f"[daily] {reportmod.label(task)} …")
    t0 = time.monotonic()
    code, env_ = maapilot_call(["maa", *args], env, timeout_s=timeout_s)
    err = env_.get("error") or {}
    row = {"task": task, "ok": code == 0, "duration_s": round(time.monotonic() - t0, 1)}
    data = env_.get("data") if isinstance(env_.get("data"), dict) else None
    if code == 0:
        if task == FARM:
            row["result"] = {k: (data or {}).get(k) for k in ("stage", "times_done", "sanity_used", "medicine_used",
                                                               "drops")}
    else:
        row.update(error=err.get("message") or err.get("code") or f"退出码 {code}", code=err.get("code"),
                   log=(data or {}).get("log"))
        if (data or {}).get("evidence"):
            row["evidence"] = data["evidence"]
    r.event("task", **row)
    r.data["tasks"].append(row)
    if code != 0:
        cliutil.log(f"[daily] {reportmod.label(task)} 没做成：{row['error']}")
        if code == BUSY:
            pass                                         # 被占用：叫 agent 也没用
        elif headless:
            r.data["agents"] += 1
            _set_step(f"{task}:agent")
            info = run_agent(cfg, recovery_prompt(r.data["run_id"], task, err, data, args), env,
                             paths.LOGS / f"agent-{r.data['run_id']}-{r.data['agents']}-{task}.jsonl", recovery=True)
            _after_agent(r, env, lk)
            row["agent"] = info
            row["recovered"] = bool(info.get("recovered"))
            r.event("agent", task=task, **{k: info.get(k) for k in ("ok", "recovered", "timed_out", "summary")})
            r.save()
            _guard(r, "check", env, f"agent 处理{reportmod.label(task)}")
        if not row.get("recovered"):
            r.data["failed"].append(task)
    r.save()
    return row


LEFTOVER_WAIT_S = 60


def _leftover_maa() -> list[int]:
    """日常拿着锁、自己没在跑 maa 时还在的 maa 进程：只能是 agent 留下的。"""
    return lockmod.external_maa_pids()


def _after_agent(r: _Run, env: dict, lk) -> None:
    """无头 agent 结束（或超时被杀）之后：换锁的 token，它留下还在跑的 maapilot 命令不能再进来；
    还有 maa 进程在跑（agent 起的，没随它一起结束）就等一会儿，还不走就结束掉。"""
    try:
        env[lockmod.TOKEN_ENV] = lk.rotate_token()
    except Exception as e:                               # noqa: BLE001
        cliutil.log(f"[daily] 换锁 token 失败：{e}")
    deadline = time.monotonic() + LEFTOVER_WAIT_S
    pids = _leftover_maa()
    while pids and time.monotonic() < deadline:
        time.sleep(5)
        pids = _leftover_maa()
    if pids:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        r.data["alerts"].append(f"agent 结束后还有 maa 进程在跑（pid {', '.join(map(str, pids))}），已结束掉")
        r.event("leftover_maa", pids=pids)


def _annihilation_skip() -> str | None:
    """这周不用 / 不该再打剿灭的原因；该打 → None。"""
    try:
        from maapilot.tools.maa_cmd import annihilation_week
        w = annihilation_week()
    except Exception:
        return None
    if w.get("capped"):
        return "本周剿灭的合成玉已经拿满"
    if w.get("blocked"):
        return f"这周已经连着 {w.get('failures')} 次没打成（多半是还不能代理），这周先不打"
    return None


def _annihilation_failed(r: _Run) -> None:
    try:
        from maapilot.tools.maa_cmd import record_annihilation_failure
        w = record_annihilation_failure()
    except Exception:
        return
    if w.get("blocked"):
        r.data["alerts"].append(f"剿灭这周连着 {w['failures']} 次没打成，这周的日常先不打了；"
                                "多半是这张图还不能代理（没 400 杀满过），要用户决定怎么办")


def _steps(r: _Run, cfg: dict, tasks: list[str], only: list[str] | None, headless: bool, env: dict, lk) -> None:
    dcfg = cfg.get("daily") or {}
    want = (lambda t: t in only) if only else (lambda t: True)
    r.event("start", trigger=r.data["trigger"], only=only, headless=headless)
    r.save()
    order = ([t for t in tasks if t == "startup"] + [t for t in tasks if t not in ("award", "startup")]
             + [FARM] + [t for t in tasks if t == "award"])
    order = [t for t in order if want(t)]

    def handoff(row: dict) -> None:
        """会话里跑（不是定时）：第一项失败就停下，把这一项和后面没跑的交给在场的 agent。"""
        if headless or row.get("ok") or row.get("code") == "busy":
            return
        t = row["task"]
        r.data["handoff"] = {"task": t, "error": row.get("error"), "log": row.get("log"),
                             "evidence": row.get("evidence"),
                             "rest": order[order.index(t):] if t in order else [t]}
        r.data["status"] = "handed_off"
        raise _Stop()

    try:
        if "startup" in tasks and want("startup"):      # 先把游戏开到首页：读仓库（对账）要在游戏里
            handoff(_maa_task(r, cfg, "startup", ["startup"], headless, env, lk))
        _set_step("guard")
        _guard(r, "snapshot", env, "上次对账")
        before = _depot_snapshot()
        for t in tasks:
            if t not in ("award", "startup") and want(t):
                why = _annihilation_skip() if t == "annihilation" else None
                if why:
                    row = {"task": t, "ok": True, "skipped": why}
                    r.event("task", **row)
                    r.data["tasks"].append(row)
                    continue
                row = _maa_task(r, cfg, t, [t], headless, env, lk)
                handoff(row)
                if t == "annihilation" and not row["ok"] and not row.get("recovered") \
                        and row.get("code") not in ("offline", "busy"):
                    _annihilation_failed(r)
        if want(FARM):
            active = goalsmod.active_goals()
            r.data["goals_active"] = len(active)
            if active and not headless:
                r.data["goals_pending"] = True           # 在场的 agent 接着做目标部分
            elif active:
                _set_step("goals:agent")
                r.data["agents"] += 1
                info = run_agent(cfg, goals_prompt(r.data["run_id"], active, r.data["tasks"]), env,
                                 paths.LOGS / f"agent-{r.data['run_id']}-{r.data['agents']}-goals.jsonl")
                _after_agent(r, env, lk)
                row = {"task": "goals", "ok": bool(info.get("ok")), "agent": info}
                r.data["tasks"].append(row)
                r.event("agent", task="goals", **{k: info.get(k) for k in ("ok", "recovered", "timed_out", "summary")})
                if not info.get("ok"):
                    r.data["alerts"].append("目标部分的 agent 没有正常结束：" + (info.get("error") or (
                        "超时被停下" if info.get("timed_out") else "看它的记录")))
                r.save()
                _guard(r, "check", env, "目标部分")
            else:
                stage = str(dcfg.get("default_stage") or "1-7")
                row = _maa_task(r, cfg, FARM, ["fight", stage], headless, env, lk)
                handoff(row)
                r.data["farm"] = row.get("result") or {"stage": stage, "ok": False}
        if "award" in tasks and want("award"):
            if r.data["goals_pending"]:                  # award 要在刷理智之后：交给在场的 agent 做完目标再领
                r.data["award_pending"] = True
            else:
                handoff(_maa_task(r, cfg, "award", ["award"], headless, env, lk))
        _set_step("guard")
        _guard(r, "check", env, "日常")
        r.data["depot_change"] = depot_change(before, _depot_snapshot())
        r.data["ok"] = not r.data["failed"]
    except _Stop:
        r.data["ok"] = False
        r.event("stopped", reason=r.data.get("stop_reason"))


# --- 定时（crontab） -------------------------------------------------------------------------------------------------

TAG = "# maapilot-v2"
_OLD_MARKS = ("maapilot job tick", "bin/daily.sh")       # 旧版 MaaPilot 的定时任务
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_CRON_HM = re.compile(r"^\s*(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*\s")


def _crontab() -> str:
    """crontab 程序；测试用 MAAPILOT_CRONTAB 换成假的。"""
    return os.environ.get("MAAPILOT_CRONTAB") or "crontab"


def read_crontab() -> str:
    try:
        p = subprocess.run([_crontab(), "-l"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise CliError("no_crontab", f"读不了 crontab：{e}", exit=ENVIRONMENT) from None
    if p.returncode != 0:
        if "no crontab" in (p.stderr or "").lower():
            return ""
        raise CliError("no_crontab", f"读不了 crontab：{(p.stderr or '').strip()}", exit=ENVIRONMENT)
    return p.stdout


def write_crontab(text: str) -> None:
    try:
        p = subprocess.run([_crontab(), "-"], input=text, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise CliError("crontab_failed", f"写不了 crontab：{e}", exit=ENVIRONMENT) from None
    if p.returncode != 0:
        raise CliError("crontab_failed", f"写不了 crontab：{(p.stderr or '').strip()}",
                       hint="macOS 弹窗问终端能不能改 crontab 时要点允许", exit=ENVIRONMENT)


def _mine(line: str) -> bool:
    return TAG in line and line.rstrip().endswith(f"{TAG} {paths.ROOT}")


def _old(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("#") and TAG not in s and any(m in s for m in _OLD_MARKS)


def _parse_times(times: list[str]) -> list[tuple[int, int]]:
    out = []
    for t in times:
        m = _TIME.match(str(t).strip())
        if not m:
            raise CliError("bad_arguments", f"时间「{t}」要写成 HH:MM（24 小时制）", exit=BAD_REQUEST)
        out.append((int(m.group(1)), int(m.group(2))))
    if not out:
        raise CliError("bad_arguments", "至少要有一个时间点", exit=BAD_REQUEST)
    return sorted(set(out))


def _path_dirs(cfg: dict) -> str:
    """定时任务的 PATH：项目的 .venv/bin（叫起来的 agent 直接用 maapilot）、maa、claude、adb 所在目录 + 系统目录。"""
    dirs = [str(paths.ROOT / ".venv" / "bin")]
    tools = cfg.get("tools") or {}
    for prog in (tools.get("maa") or "maa", tools.get("claude") or "claude",
                 (cfg.get("emulator") or {}).get("adb") or "adb"):
        found = shutil.which(str(prog))
        if found:
            d = str(Path(found).parent)
            if d not in dirs:
                dirs.append(d)
    for d in ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if d not in dirs:
            dirs.append(d)
    return ":".join(dirs)


def cron_lines(times: list[tuple[int, int]], cfg: dict) -> list[str]:
    def q(s) -> str:
        return shlex.quote(str(s)).replace("%", r"\%")    # crontab 里 % 是换行
    envs = f"PATH={q(_path_dirs(cfg))} PYTHONUTF8=1"
    if os.environ.get("MAAPILOT_PRIVATE"):
        envs += f" MAAPILOT_PRIVATE={q(paths.PRIVATE)}"
    cmd = (f"{envs} {q(paths.ROOT / '.venv' / 'bin' / 'maapilot')} daily run --trigger schedule"
           f" >> {q(paths.LOGS / 'cron.log')} 2>&1")
    return [f"{m} {h} * * * {cmd} {TAG} {paths.ROOT}" for h, m in times]


def _entries(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    return [ln for ln in lines if _mine(ln)], [ln for ln in lines if _old(ln)]


def _times_of(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        m = _CRON_HM.match(ln)
        if m:
            out.append(f"{int(m.group(2)):02d}:{int(m.group(1)):02d}")
    return sorted(out)


def schedule(times: str | None, replace_old: bool, dry_run: bool) -> dict:
    cfg = config.load()
    hm = _parse_times(times.split(",") if times else list(cfg.get("schedule") or []))
    cur = read_crontab()
    mine, old = _entries(cur)
    keep = [ln for ln in cur.splitlines() if not _mine(ln) and not (replace_old and _old(ln))]
    new_lines = cron_lines(hm, cfg)
    text = "\n".join(keep + new_lines) + "\n"
    changed = text != (cur if cur.endswith("\n") or not cur else cur + "\n")
    if not dry_run and changed:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        write_crontab(text)
    out = {"times": [f"{h:02d}:{m:02d}" for h, m in hm], "installed": new_lines, "replaced": mine,
           "old_entries": old, "removed_old": old if replace_old else [], "changed": changed, "dry_run": dry_run}
    if dry_run:
        out["crontab"] = text
    return out


def unschedule(dry_run: bool) -> dict:
    cur = read_crontab()
    mine, old = _entries(cur)
    keep = [ln for ln in cur.splitlines() if not _mine(ln)]
    text = "\n".join(keep) + ("\n" if keep else "")
    if mine and not dry_run:
        write_crontab(text)
    out = {"removed": mine, "old_entries": old, "dry_run": dry_run}
    if dry_run:
        out["crontab"] = text
    return out


def schedule_info() -> dict:
    """crontab 里本项目的定时（doctor 的 schedule 检查、--brief 用）：{installed, times, old_entries} 或带 error。"""
    try:
        mine, old = _entries(read_crontab())
    except CliError as e:
        return {"installed": None, "times": [], "old_entries": [], "error": e.message}
    return {"installed": bool(mine), "times": _times_of(mine), "old_entries": old}


def _next_at(times: list[str]) -> str | None:
    t = now()
    cands = []
    for s in times:
        h, m = map(int, s.split(":"))
        c = t.replace(hour=h, minute=m, second=0, microsecond=0)
        cands.append(c if c > t else c + timedelta(days=1))
    return min(cands).isoformat(timespec="minutes") if cands else None


def status() -> dict:
    sched = schedule_info()
    return {"running": running(), "last": reportmod.latest(), "schedule": sched,
            "next_at": _next_at(sched["times"]) if sched.get("installed") else None}


# --- 命令 ------------------------------------------------------------------------------------------------------------

def _split(v: str | None) -> list[str] | None:
    if not v:
        return None
    return [x.strip() for x in v.split(",") if x.strip()]


def register(sub) -> None:
    g = sub.add_parser("daily", help="日常与定时")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")

    p = gs.add_parser("run", help="跑一次日常（20–60 分钟，用后台运行）")
    p.add_argument("--only", help="只跑这几项（逗号分隔；刷理智写 farm）")
    p.add_argument("--trigger", choices=["manual", "schedule"], default="manual",
                   help="schedule：定时运行，失败当场叫无头 agent；manual（默认）：会话里的 agent 在场，失败交回")
    p.add_argument("--interactive", action="store_true", help="同 --trigger manual")
    p.add_argument("--no-agent", action="store_true", help="不起无头 agent（失败只记下）")
    p.set_defaults(func=lambda a: run(_split(a.only), "manual" if a.interactive else a.trigger, a.no_agent),
                   command="daily run")

    p = gs.add_parser("status", help="上次运行、下次定时、有没有在跑")
    p.set_defaults(func=lambda a: status(), command="daily status")

    p = gs.add_parser("schedule", help="设定时任务（改 crontab，只动本项目的行）")
    p.add_argument("--times", help="HH:MM[,HH:MM]；默认 config.yaml 的 schedule")
    p.add_argument("--replace-old", action="store_true", help="同时删掉旧版 MaaPilot 的定时任务")
    p.add_argument("--dry-run", action="store_true", help="只给出改完的 crontab，不写")
    p.set_defaults(func=lambda a: schedule(a.times, a.replace_old, a.dry_run), command="daily schedule")

    p = gs.add_parser("unschedule", help="取消本项目的定时任务")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=lambda a: unschedule(a.dry_run), command="daily unschedule")
