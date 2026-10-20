"""`maapilot maa <任务>`：用 maa-cli 跑一个 MAA 任务（拿锁）。契约见 docs/cli.md「maa」。

参数的来源依次合并：DEFAULTS → config.yaml 的 `daily.params.<任务>` → `--params`；然后按
maapilot.knowledge.maa_protocol 的白名单校验（写错退出码 2）。两条硬规定：
- **源石永远是 0**：任何来源写了 `stone` 都拒绝；Fight 固定写 `stone = 0`。
- **理智液只听 `--medicine N`**：`medicine`、`medicine_expire_days`、`expiring_medicine`、`use_sanity_potion`
  不能从 `--params` 或 config 进来；`--medicine` 默认 0，`--expiring-medicine` 只用快过期的。

`run(task, **opts)` 是不拿锁的函数版本（`daily run` 已经拿着锁时直接调）；命令行的处理函数在外面包锁与心跳。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from maapilot import config
from maapilot.cliutil import BAD_REQUEST, FAILED, CliError
from maapilot.tools import common

# 任务 → (MAA 任务类型, 超时秒数)
TASKS = {
    "startup": ("StartUp", 900),
    "recruit": ("Recruit", 1200),
    "infrast": ("Infrast", 2400),
    "mall": ("Mall", 1200),
    "award": ("Award", 900),
    "fight": ("Fight", 3 * 3600),
    "annihilation": ("Fight", 3600),
    "copilot": ("Copilot", 3600),
    "closedown": ("CloseDown", 300),
}

# 默认参数：常见、不花资源的设置。client_type / server 在 build_params 里按 config 填。
DEFAULTS = {
    "startup": {"start_game_enabled": True},
    "recruit": {"refresh": True, "select": [4], "confirm": [3, 4], "times": 4, "set_time": True,
                "expedite": False, "skip_robot": True},
    "infrast": {"mode": 0, "facility": ["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"],
                "drones": "Money", "threshold": 0.3, "replenish": True, "dorm_notstationed_enabled": False,
                "dorm_trust_enabled": True},
    "mall": {"shopping": True, "visit_friends": True, "buy_first": ["招聘许可"], "blacklist": ["家具零件"]},
    "award": {"award": True, "mail": True, "recruit": True},
    "fight": {},
    "annihilation": {},
    "closedown": {},
}

FORBIDDEN = {"stone": "源石永远是 0，这个参数不接受",
             "medicine": "理智液只能用 --medicine N 指定", "medicine_expire_days": "用 --expiring-medicine",
             "expiring_medicine": "用 --expiring-medicine", "use_sanity_potion": "理智液只能用 --medicine N 指定"}


def _json_params(text) -> dict:
    if not text:
        return {}
    if isinstance(text, dict):
        return dict(text)
    try:
        v = json.loads(text)
    except ValueError as e:
        raise CliError("bad_params", f"--params 不是合法的 JSON：{e}", exit=BAD_REQUEST) from None
    if not isinstance(v, dict):
        raise CliError("bad_params", "--params 要是 JSON 对象", exit=BAD_REQUEST)
    return v


def _check_forbidden(params: dict, where: str) -> None:
    bad = [k for k in params if k in FORBIDDEN]
    if bad:
        raise CliError("forbidden_param", f"{where} 里有不接受的参数 {bad[0]}：{FORBIDDEN[bad[0]]}", exit=BAD_REQUEST)


def resolve_stage(code: str) -> tuple[str, dict]:
    """关卡写法 → (给 MAA 的值, 关卡信息)。数据表里认不出的原样交给 MAA（比数据表新的活动关等）。
    今天不开的资源本 / 芯片本 → CliError(not_open)；MAA 导航不了的活动关（MAA 活动列表里没有这期、
    或这期只支持别的几关）→ CliError(maa_unsupported_stage)。"""
    info: dict = {"code": code}
    try:
        from maapilot.knowledge import calendar, stages
        ref = stages.resolve_stage(code)
        days = stages.open_weekdays(ref.stage_id)
    except Exception:                              # 没有数据表、认不出、重名：交给 MAA 自己认
        return code, info
    info.update({"stage_id": ref.stage_id, "code": ref.code, "ap_cost": ref.ap_cost, "nav_kind": ref.nav_kind})
    if ref.nav_kind == "not_navigable" and ref.reason:     # 只有读到了 MAA 的活动列表才有 reason
        not_yet = "活动列表里还没有" in ref.reason
        raise CliError("maa_unsupported_stage", ref.reason, exit=FAILED,
                       hint=("MAA 一般活动开始后一两天更新；明天再试，或者手动进关卡页用代理" if not_yet else
                             "换 MAA 支持的关，或者手动进关卡页用代理"),
                       data={"stage": ref.code, "stage_id": ref.stage_id, "reason": ref.reason})
    if days is not None:
        wd = calendar.game_weekday()
        info["open_today"] = wd in days
        if wd not in days:
            names = "、".join("一二三四五六日"[d - 1] for d in sorted(days))
            raise CliError("not_open", f"{ref.code} 今天不开（开放：周{names}）", exit=FAILED,
                           hint="换一关，或等开放日", data={"stage": ref.code, "open_weekdays": sorted(days)})
    return ref.maa_value or ref.code, info


def _annihilation_value(stage: str | None) -> str:
    from maapilot.knowledge.stages import ANNIHILATION_CURRENT, ANNIHILATION_MAA_VALUES
    if not stage or stage in ("当期", "当期剿灭", ANNIHILATION_CURRENT):
        return ANNIHILATION_CURRENT
    if stage in ANNIHILATION_MAA_VALUES:
        return ANNIHILATION_MAA_VALUES[stage]
    if stage in ANNIHILATION_MAA_VALUES.values():
        return stage
    raise CliError("unknown_stage", f"认不出剿灭关 {stage}", exit=BAD_REQUEST,
                   data={"suggestions": ["当期剿灭", *ANNIHILATION_MAA_VALUES]})


def build_params(task: str, opts: dict, cfg: dict) -> tuple[str, dict, dict]:
    """(MAA 任务类型, 参数, 关卡信息)。参数不对抛 CliError(退出码 2)。"""
    from maapilot.knowledge.maa_protocol import TASK_PARAMS, validate_params
    ttype = TASKS[task][0]
    extra = _json_params(opts.get("params"))
    from_cfg = ((cfg.get("daily") or {}).get("params") or {}).get(task) or {}
    if not isinstance(from_cfg, dict):
        raise CliError("invalid_config", f"config.yaml 的 daily.params.{task} 要是映射", exit=BAD_REQUEST)
    _check_forbidden(from_cfg, f"config.yaml 的 daily.params.{task}")
    _check_forbidden(extra, "--params")
    params = {**DEFAULTS.get(task, {}), **from_cfg, **extra}
    info: dict = {}
    if "client_type" in TASK_PARAMS[ttype]:
        params["client_type"] = config.server(cfg)
    if task in ("fight", "annihilation"):
        if task == "fight":
            if not opts.get("stage"):
                raise CliError("bad_arguments", "要给关卡", exit=BAD_REQUEST)
            params["stage"], info = resolve_stage(opts["stage"])
        else:
            params["stage"] = _annihilation_value(opts.get("stage"))
            info = {"code": opts.get("stage") or "当期剿灭"}
        times = opts.get("times")
        if times is None and task == "annihilation":
            times = 1
        if times is not None:
            if int(times) < 1:
                raise CliError("bad_arguments", "--times 至少 1", exit=BAD_REQUEST)
            params["times"] = int(times)
        medicine = int(opts.get("medicine") or 0)
        if medicine < 0:
            raise CliError("bad_arguments", "--medicine 不能是负数", exit=BAD_REQUEST)
        params["medicine"] = medicine
        if opts.get("expiring_medicine"):
            params["medicine_expire_days"] = int(opts["expiring_medicine"])
        params["stone"] = 0
        params.setdefault("server", config.penguin_server(cfg))
        params.setdefault("report_to_penguin", False)
    errs = validate_params(ttype, params)
    if errs:
        raise CliError("bad_params", "；".join(errs), hint="参数名见 MAA 文档的任务参数", exit=BAD_REQUEST)
    return ttype, params, info


def _drops(names: dict) -> list[dict]:
    out = []
    for name, n in names.items():
        iid = None
        try:
            from maapilot.knowledge.items import resolve_item
            iid = resolve_item(name)
        except Exception:
            pass
        out.append({"id": iid, "name": name, "count": n})
    return out


def _save_sanity(last) -> None:
    if last:
        from maapilot import paths
        common.write_json(paths.STATE / "sanity.json",
                          {"at": common.iso(), "current": last[0], "max": last[1], "source": "maa-log"})


def output_problem(task: str, mrun):
    """maa 说做成了（退出码 0），输出里却认不出程序要读的行 → UpstreamFormat（maa-cli 的输出格式可能变了）；正常 → None。
    - `maa run` 的标准输出里没有一行 Summary 任务行（`[任务] … Completed`）；作业（copilot）不查。
    - 刷图（fight）的日志里既没有 "Current sanity" 也没有 "Mission started"：理智、次数、掉落都记不到。
      剿灭不查：本周拿满、找不到入口时 MAA 也报完成、日志里没有这两行（另有记录判断）。"""
    from maapilot.knowledge.sources import UpstreamFormat
    if mrun.error or mrun.timed_out or mrun.returncode != 0:
        return None
    p = mrun.parsed
    if task != "copilot" and not p.get("tasks"):
        return UpstreamFormat("maa_cli", "标准输出里 Summary 的任务行", effect="分不出任务做没做成")
    if task == "fight" and mrun.ok and not p.get("sanity_first") and not p.get("fights"):
        return UpstreamFormat("maa_cli", "日志里的 Current sanity / Mission started 行", effect="刷图次数、理智、掉落都记不到")
    return None


def _note_output(task: str, mrun, data: dict) -> None:
    """把 output_problem 记进 sources.json（命令照常返回，data 里多一条 upstream_format）。"""
    from maapilot.knowledge import sources
    e = output_problem(task, mrun)
    if e is None:
        if mrun.returncode == 0 and not mrun.error and not mrun.timed_out:
            sources.ok("maa_cli")
        return
    sources.bad("maa_cli", e)
    data["upstream_format"] = sources.line({"message": e.message, "effect": e.effect})


def result(task: str, mrun, info: dict) -> dict:
    """MaaRun → 命令的 data。"""
    p = mrun.parsed
    data = {"task": task, "ok": mrun.ok, "duration_s": mrun.duration_s,
            "tasks": [{"name": t["name"], "status": t["status"]} for t in p.get("tasks") or []],
            "offline": mrun.offline, "log": common.rel(mrun.log)}
    if task in ("fight", "annihilation"):
        times = p.get("fights") or p.get("fight_times") or 0
        first, last = p.get("sanity_first"), p.get("sanity_last")
        ap = info.get("ap_cost")
        failed, refund = p.get("proxy_failed") or 0, p.get("proxy_refund") or 0
        data.update({"stage": info.get("code"), "times_done": times,
                     "sanity_used": max(0, times * ap - refund) if ap else
                     (max(0, first[0] - last[0]) if first and last else None),
                     "sanity": {"current": last[0], "max": last[1]} if last else None,
                     "medicine_used": p.get("medicine") or 0, "drops": _drops(p.get("drops") or {})})
        if failed:                                   # 代理没打赢：理智退回、没掉东西，MAA 照样算"完成"
            data["proxy_failed"] = failed
            data["note"] = f"{failed} 场代理失败（理智已退回、没掉东西）"
        _save_sanity(last)
        if task == "annihilation":
            got = sum(d["count"] for d in data["drops"] if d["id"] == "4003" or d["name"] == "合成玉")
            week = record_annihilation(times, got, drops_seen=bool(data["drops"]))
            data["orundum_gained"] = got
            data["weekly_capped"] = week["capped"]
            data["week"] = {"runs": week["runs"], "orundum": week["orundum"]}
    if task == "copilot":
        data["copilot"] = info
    return data


# --- 剿灭的每周记录（本周合成玉拿满了，日常就不再打） --------------------------------------------------

ORUNDUM_WEEKLY_CAP = 1800


def annihilation_week() -> dict:
    """本游戏周（周一 04:00 起）剿灭的记录 `private/state/annihilation.json`：
    {week, runs, orundum, capped}；跨周清零。剿灭没打成不在这里记：交给 agent 判断原因。"""
    from maapilot import paths
    from maapilot.knowledge.calendar import week_start
    wk = week_start().date().isoformat()
    d = common.read_json(paths.STATE / "annihilation.json") or {}
    if d.get("week") != wk:
        d = {"week": wk, "runs": 0, "orundum": 0, "capped": False}
    return d


def record_annihilation(times: int, got: int, *, drops_seen: bool) -> dict:
    """记下一次剿灭。打了、认出了掉落、却没有合成玉，或本周累计到上限 → 本周拿满了。
    掉落一样都没认出（日志没解析到）时不下结论，免得误判后整周不打。"""
    from maapilot import paths
    d = annihilation_week()
    d["runs"] += int(times or 0)
    d["orundum"] += int(got or 0)
    if (times and not got and drops_seen) or d["orundum"] >= ORUNDUM_WEEKLY_CAP:
        d["capped"] = True
    common.write_json(paths.STATE / "annihilation.json", d)
    return d


def note_annihilation_progress(current: int, total: int, *, source: str = "skland") -> dict:
    """游戏里的本周剿灭合成玉（森空岛给的 current/total，准）记进本周记录：以它为准覆盖自己累加的数，拿满了就 capped。"""
    from maapilot import paths
    d = annihilation_week()
    d["orundum"] = int(current)
    d["capped"] = int(current) >= int(total)
    d["progress"] = {"current": int(current), "total": int(total), "source": source, "at": common.iso()}
    common.write_json(paths.STATE / "annihilation.json", d)
    return d



def _local_path(uri: str):
    """本地作业：普通路径，或 file:// URI（file:///C:/x.json、file:///Users/x.json；按 Path.as_uri() 的格式解析）。"""
    from pathlib import Path
    from urllib.parse import unquote, urlparse
    if not uri.lower().startswith("file:"):
        return Path(uri).expanduser()
    u = urlparse(uri)
    path = unquote(u.path)
    if re.match(r"^/[A-Za-z]:", path):                  # Windows 盘符：/C:/x → C:/x
        path = path[1:]
    if u.netloc and u.netloc != "localhost":            # UNC：file://server/share/x
        path = f"//{u.netloc}{path}"
    return Path(path).expanduser()


_PLAN_URI = re.compile(r"^(?:prts://|maa://)?(\d+)$", re.I)


def plan_id_of(uri: str) -> int | None:
    """作业站的作业：`prts://<编号>`（新写法）、`maa://<编号>`（旧写法）或纯数字 → 编号；别的（本地文件）→ None。"""
    m = _PLAN_URI.match(str(uri).strip())
    return int(m.group(1)) if m else None


def fetch_plan(plan_id: int) -> Path:
    """从作业站取作业存成 private/cache/copilot/<编号>.json，交给 maa-cli 的是这个本地文件：
    maa-cli 0.7.6 起作业码改成 prts://（maa:// 会报弃用），0.7.5 只认 maa://，给本地文件哪个版本都一样。"""
    from maapilot import paths
    from maapilot.tools import copilot_cmd
    c = copilot_cmd.content(copilot_cmd.get(plan_id))
    if not c:
        raise CliError("bad_plan", f"作业 {plan_id} 的内容读不出来", exit=FAILED)
    path = paths.PRIVATE / "cache" / "copilot" / f"{plan_id}.json"
    common.write_json(path, c)
    return path


def _copilot_argv(opts: dict, fetch=fetch_plan) -> tuple[list[str], dict]:
    uri = (opts.get("uri") or "").strip()
    if not uri:
        raise CliError("bad_arguments", "要给作业：prts://<编号> 或本地 JSON 文件", exit=BAD_REQUEST)
    if re.match(r"^prts://s", uri, re.I):
        raise CliError("bad_arguments", f"作业集（{uri}）还不支持：一份一份跑", exit=BAD_REQUEST,
                       hint="copilot find 找单份作业，用 prts://<编号>")
    pid = plan_id_of(uri)
    if pid is not None:
        path = Path(fetch(pid)).resolve()
        info: dict = {"uri": f"prts://{pid}", "file": str(path)}
    else:
        path = _local_path(uri)
        if not path.exists():
            raise CliError("not_found", f"作业文件不存在：{path}", exit=BAD_REQUEST)
        path = path.resolve()
        info = {"uri": str(path)}
    argv = ["copilot", str(path)]
    info["formation"] = bool(opts.get("formation"))
    if opts.get("formation"):
        argv.append("--formation")
    if opts.get("formation_index"):
        argv += ["--formation-index", str(int(opts["formation_index"]))]
        info["formation_index"] = int(opts["formation_index"])
    if opts.get("add_trust"):
        argv.append("--add-trust")
        info["add_trust"] = True
    if opts.get("ignore_requirements"):
        argv.append("--ignore-requirements")
        info["ignore_requirements"] = True
    if opts.get("support"):
        argv += ["--support-unit-usage", str(int(opts["support"]))]
        info["support"] = int(opts["support"])
    if opts.get("support_name"):
        argv += ["--support-unit-name", opts["support_name"]]
        info["support_name"] = opts["support_name"]
    if opts.get("raid") == "both":
        # 只给一份作业时 maa-cli 是单作业模式，突袭设置被丢掉，"both" 实际只按屏幕上的模式打一次
        raise CliError("bad_arguments", "--raid both 做不到：单份作业时 maa-cli 只按屏幕上的模式打一次", exit=BAD_REQUEST,
                       hint="分两次跑：先在关卡详情页常规模式下跑一次（不加 --raid），再切到突袭、加 --raid raid 跑一次")
    if opts.get("raid") == "raid":
        argv += ["--raid", opts["raid"]]
        info["raid"] = opts["raid"]
    if opts.get("loop_times"):
        argv += ["--loop-times", str(int(opts["loop_times"]))]
    # 不加 --use-sanity-potion：作业不用理智液
    return argv, info


def raid_shot():
    """`--raid raid` 开打前截一张（同 `screen shot`，只读连接、不点）→ (图, 路径)。测试里替换掉。"""
    from maapilot.tools import screen
    img, meta = screen.capture(common.connect(ocr=False, readonly=True), "raid-check")
    return img, meta["path"]


def check_raid_mode() -> dict:
    """单份作业时 maa-cli 丢掉突袭设置、按屏幕上的模式打，所以 `--raid raid` 开打前确认关卡详情页已经是突袭模式。
    只截图、认切换按钮，**不点任何东西**（不切模式、不点开始行动 / 代理）。是突袭 → {mode, shot}；
    不是（常规，或认不出：不在详情页）→ CliError not_raid_mode（退出码 1），由 agent 切到突袭再跑。"""
    from maapilot.crew import battle
    img, shot = raid_shot()
    mode = battle.detail_mode(img)
    data = {"mode": mode, "shot": shot}
    if mode == "raid":
        return data
    msg = ("关卡详情页现在是常规模式，不是突袭" if mode == "normal"
           else "看不出关卡详情页的模式（不在详情页，或这关没有突袭）")
    raise CliError("not_raid_mode", msg + "：单份作业时 maa-cli 会按屏幕上的模式打，所以没有开打", exit=FAILED,
                   data={"raid_check": data},
                   hint="回到这关的关卡详情页，点切换按钮切到突袭、截图确认后再跑同一条命令（不要先进编队页）")


def go_home() -> bool | None:
    """StartUp 成功后游戏已经开着时，MAA 不管停在哪一页（真机：留在终端页）。用 MaaFramework 回首页
    （关弹窗、左上菜单"首页"、返回键；只导航，不点会花东西的按钮）。→ 到首页了 True / 没回去 False / 连不上 None。"""
    try:
        from maapilot.crew import nav
        return bool(nav.ensure_home(common.connect(ocr=True)))
    except Exception:                                  # noqa: BLE001 - 回不去交给 agent 看截图
        return None


EVIDENCE_SHOTS = 6        # 失败时最多带几张 MAA 自己的截图


def failure_shot(task: str) -> str:
    """失败那一刻截一张（同 `screen shot`），返回路径；连不上抛异常。测试里替换掉。"""
    from maapilot.tools import screen
    return screen.capture(common.connect(ocr=False, readonly=True), f"fail-{task}")[1]["path"]


def collect_evidence(task: str, started, ended) -> dict:
    """失败时的取证：先截图（趁还没别的东西动屏幕），再找 MAA 这段时间自己存的截图。尽力而为，
    哪样拿不到写进 `missing`，从不让命令因此失败。"""
    from datetime import timedelta
    ev: dict = {"shot_now": None, "maa_shots": [], "window": {"start": common.iso(started), "end": common.iso(ended)},
                "why": f"maapilot maa why --task {task}", "missing": []}
    try:
        ev["shot_now"] = failure_shot(task)
    except CliError as e:
        ev["missing"].append(f"失败时的截图：{e.message}")
    except Exception as e:                             # noqa: BLE001
        ev["missing"].append(f"失败时的截图：{type(e).__name__}: {e}")
    try:
        from maapilot.crew import maalog
        root = maalog.debug_dir()
        if root is None:
            ev["missing"].append("MAA 的截图：找不到 MAA 的日志目录")
        else:
            ev["maa_shots"] = maalog.import_shots(maalog.screenshots(started - timedelta(seconds=3), common.now(), root,
                                                 limit=EVIDENCE_SHOTS), task)
        bad = [s["source"] for s in ev["maa_shots"] if not s.get("path")]
        if bad:
            ev["missing"].append(f"MAA 的截图复制不进项目：{len(bad)} 张")
    except Exception as e:                             # noqa: BLE001
        ev["missing"].append(f"MAA 的截图：{type(e).__name__}: {e}")
    if not ev["missing"]:
        del ev["missing"]
    return ev


def run(task: str, **opts) -> dict:
    """跑一个 MAA 任务，不拿锁（调用方负责）。成功返回 data；失败抛 CliError（data 里是同样的结构）。
    opts：stage、times、medicine、expiring_medicine、params（JSON 文本或 dict）、uri、formation、loop_times、timeout_s。"""
    from maapilot.crew import maa
    if task not in TASKS:
        raise CliError("unknown_task", f"不认识的任务 {task}（有：{', '.join(TASKS)}）", exit=BAD_REQUEST)
    cfg = config.load()
    if task == "copilot":
        argv, info = _copilot_argv(opts)
    else:
        ttype, params, info = build_params(task, opts, cfg)
    common.ensure_device()
    maa.install_config()                               # 缺的配置补上，profile 的 adb_path 对上 emulator.adb
    if task == "copilot" and info.get("raid") == "raid":
        info["raid_check"] = check_raid_mode()
    timeout = opts.get("timeout_s") or TASKS[task][1]
    started = common.now()
    if task == "copilot":
        mrun = maa.execute(argv, label="copilot", timeout_s=timeout)
    else:
        mrun = maa.run_tasks(task, [{"name": task, "type": ttype, "params": params}], timeout_s=timeout)
    data = result(task, mrun, info)
    _note_output(task, mrun, data)
    if mrun.ok:
        if task == "startup":
            data["home"] = go_home()
        return data
    data["evidence"] = collect_evidence(task, started, common.now())
    if data["evidence"].get("shot_now"):
        data["_log"] = {"shot_now": data["evidence"]["shot_now"]}   # 记进命令记录，maa why 也用
    if mrun.error:
        raise CliError("maa_failed", mrun.error, hint="maapilot doctor", exit=FAILED, data=data)
    data["errors"] = maa.error_lines(mrun)
    if mrun.offline:
        raise CliError("offline", "游戏掉线或服务器维护，任务没做完", exit=FAILED, data=data,
                       hint="重新登录（maapilot maa startup）后再试；维护中就等维护结束")
    why = "超时，已终止 maa" if mrun.timed_out else f"maa 退出码 {mrun.returncode}"
    raise CliError("maa_failed", f"{task} 没做成（{why}）", exit=FAILED, data=data,
                   hint="先看 evidence 里的截图、跑 maapilot maa why 查原因，再按 maapilot-recovery 处理")


@common.logged
def cmd(args):
    opts = {k: getattr(args, k, None) for k in ("stage", "times", "medicine", "expiring_medicine", "params",
                                                   "formation", "uri", "loop_times", "formation_index", "add_trust",
                                                   "ignore_requirements", "support", "support_name", "raid")}
    with common.device(f"maa {args.task}", job=f"maa {args.task}") as lk:
        with lk.keepalive():
            return run(args.task, **opts)


def _common(p, task: str) -> None:
    p.add_argument("--params", metavar="JSON", help="覆盖 MAA 任务参数（JSON 对象）")
    p.set_defaults(func=cmd, command=f"maa {task}", task=task)


def register(sub) -> None:
    g = sub.add_parser("maa", help="用 maa-cli 跑一个 MAA 任务")
    gs = g.add_subparsers(dest="sub", metavar="<任务>")
    helps = {"startup": "启动游戏、回首页", "recruit": "公开招募", "infrast": "基建换班、收菜", "mall": "信用商店、访问好友",
             "award": "任务奖励、邮件", "closedown": "关游戏"}
    for name, h in helps.items():
        _common(gs.add_parser(name, help=h), name)
    p = gs.add_parser("fight", help="刷关")
    p.add_argument("stage", help="关卡，如 1-7、CE-6、PR-C-2")
    p.add_argument("--times", type=int, help="次数（默认打到理智用完）")
    p.add_argument("--medicine", type=int, default=0, help="最多用几瓶理智液（默认 0）")
    p.add_argument("--expiring-medicine", type=int, nargs="?", const=2, metavar="天",
                   help="用 N 天内过期的理智液（不写天数为 2）")
    _common(p, "fight")
    p = gs.add_parser("annihilation", help="剿灭")
    p.add_argument("--stage", help="剿灭关（默认当期剿灭；也可写 龙门外环 / 龙门市区 / 切尔诺伯格）")
    p.add_argument("--times", type=int, help="次数（默认 1）")
    p.add_argument("--medicine", type=int, default=0, help="最多用几瓶理智液（默认 0）")
    p.add_argument("--expiring-medicine", type=int, nargs="?", const=2, metavar="天")
    _common(p, "annihilation")
    from maapilot.tools import maa_why
    maa_why.register(gs)
    p = gs.add_parser("copilot", help="作业（自动战斗）")
    p.add_argument("uri", help="prts://<编号>（旧写法 maa://<编号> 也认）或本地 JSON 文件")
    p.add_argument("--formation", action="store_true", help="自动编队")
    p.add_argument("--formation-index", type=int, choices=range(1, 5), help="用第几个编队（1–4；默认当前编队）")
    p.add_argument("--add-trust", action="store_true", help="自动编队时空位按信赖从低到高补人")
    p.add_argument("--ignore-requirements", action="store_true", help="不管作业写的练度要求，照样编队开打")
    p.add_argument("--support", type=int, choices=range(0, 4), metavar="0-3",
                   help="助战：0 不用（默认）；1 只缺一人时借；2 缺一人时借，否则借 --support-name；3 缺一人时借，否则随便借")
    p.add_argument("--support-name", help="借哪个助战干员")
    p.add_argument("--raid", choices=("normal", "raid", "both"),
                   help="normal 普通（默认）/ raid 突袭（开打前核对详情页是突袭模式）/ both 不支持，分两次跑")
    p.add_argument("--loop-times", type=int, help="重复几次")
    p.set_defaults(func=cmd, command="maa copilot", task="copilot")
