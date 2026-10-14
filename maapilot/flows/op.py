"""`maapilot op upgrade / mastery / progress`：养成快捷操作。契约见 docs/cli.md。

upgrade / mastery 是旧版脚本 maapilot.crew.upgrade / mastery 的薄外壳；progress 交给 MaaCore 的干员培养
（OperProgress，经 maapilot.crew.maacore 直接调本体）。

能做成就省事；做不成老实报失败（退出码 1 shortcut_failed，带做到哪一步和截图），agent 按 maapilot-training 在界面上自己做。
花了多少不在这里算：要知道就前后 `state depot`。
"""
from __future__ import annotations

from maapilot import paths
from maapilot.cliutil import BAD_REQUEST, ENVIRONMENT, FAILED, CliError
from maapilot.flows._common import check_operator, depot_names, rel, run_shortcut


def upgrade(name: str, elite: int | None, level: int | None, skill: int | None) -> dict:
    if elite is None and level is None and skill is None:
        raise CliError("bad_arguments", "至少给 --elite / --level / --skill 之一", exit=BAD_REQUEST)
    info = check_operator(name)
    maxes = info.get("max_levels") or []
    if elite is not None and not (0 <= elite < len(maxes)):
        raise CliError("bad_arguments", f"{name} 最高精{len(maxes) - 1}", exit=BAD_REQUEST)
    if level is not None and level < 1:
        raise CliError("bad_arguments", "--level 至少是 1", exit=BAD_REQUEST)
    if elite is not None and level is not None and level > maxes[elite]:
        raise CliError("bad_arguments", f"{name} 精{elite} 最高 {maxes[elite]} 级", exit=BAD_REQUEST)
    if skill is not None and not (1 <= skill <= 7):
        raise CliError("bad_arguments", "--skill 是技能等级 1–7（专精用 op mastery）", exit=BAD_REQUEST)
    args = {"operator": name, "elite": elite, "level": level, "skill": skill}

    def body(g, pr):
        from maapilot.crew import upgrade as up
        e_t, l_t = elite, level
        if e_t is None or l_t is None:                  # 只给了一部分：没给的按现在的练度
            pr.stage = "读现在的练度"
            cur = up.read_operator(g, name)
            if e_t is None:
                e_t = cur["elite"]
            if l_t is None:
                l_t = 1 if e_t > cur["elite"] else (cur["level"] or 1)
        pr.stage = "升级 / 精英化 / 升技能"
        recovered = None
        try:
            res = up.upgrade_one(g, name, e_t, l_t, skill)
        except Exception as e:                          # noqa: BLE001 - 先核对一次：做成了就不算失败
            res = _verify_upgrade(g, name, e_t, l_t, skill, e)
            if res is None:
                raise
            recovered = str(e)
        up.leave_list_unfiltered(g)
        shot = g.save(f"op_upgrade_{name}")
        after = {"elite": res.get("elite"), "level": res.get("level"), "skill": res.get("skill")}
        data = {"operator": name, "target": {"elite": e_t, "level": l_t, "skill": skill},
                "before": res.get("start"), "after": after, "shot": rel(shot)}
        if recovered:
            data["recovered"] = recovered
        reached = res.get("reached") and (skill is None or (after["skill"] or 0) >= skill)
        if after["elite"] is not None:                 # 做完回干员页读的：记成现场练度（森空岛会滞后）
            from maapilot.tools import live_box
            live_box.record(name, source="op upgrade", elite=after["elite"], level=after["level"],
                            skill_level=after["skill"])
        if not reached:
            raise CliError("shortcut_failed", f"{name} 没到目标练度（现在 {after}）",
                           hint="screen shot 看看停在哪，在界面上接着做", exit=FAILED,
                           data={**data, "stage": pr.stage, "done": after})
        return data

    return run_shortcut("op upgrade", args, f"op_upgrade_error_{name}", body)


def _verify_upgrade(g, name: str, elite: int, level: int, skill: int | None, err: Exception) -> dict | None:
    """快捷操作中途报错时，回干员页重读一次练度（只读）：已经到目标了 → 当成做成（返回和 upgrade_one 一样的 dict，
    start 取报错时带的）；没到、读不出 → None（照原样报失败）。10-05、10-06 升到 7 级其实成了却报失败。"""
    from maapilot.crew import upgrade as up
    try:
        cur = up.read_operator(g, name)
    except Exception:                                   # noqa: BLE001
        return None
    lvl_ok = (cur["elite"], cur["level"] or 0) >= (elite, level) if cur["level"] is not None else cur["elite"] > elite
    if not lvl_ok or (skill is not None and (cur.get("skill") or 0) < skill):
        return None
    start = (getattr(err, "reached", None) or {}).get("start")
    return {**cur, "reached": True, "start": start}


# start_mastery 的状态 → (退出码 1 时的 error.code, 说明)。"started" / "reached" 算成功。
_MASTERY_FAIL = {
    "room busy": ("training_busy", "训练室正在练别的专精", "等它练完，或在游戏里看看是谁在练"),
    "no dialog": ("shortcut_failed", "专精确认框没打开（可能已经专三，或没点中）", "看截图"),
    "rank unknown": ("shortcut_failed", "认不出这次要开的是专几，什么都没开", "看截图里的确认框"),
    "costs unreadable": ("shortcut_failed", "确认框里的材料数没认全，什么都没开",
                         "看截图里的确认框：材料都够就手动点开（训练室选人、点技能、点确定），不够再去刷"),
}


def mastery(name: str, skill: int, rank: int) -> dict:
    if not (1 <= skill <= 3):
        raise CliError("bad_arguments", "--skill 是第几个技能（1–3）", exit=BAD_REQUEST)
    if not (1 <= rank <= 3):
        raise CliError("bad_arguments", "--rank 是专几（1–3）", exit=BAD_REQUEST)
    info = check_operator(name)
    if skill > (info.get("skills") or 0):
        raise CliError("bad_arguments", f"{name} 没有第 {skill} 个技能", exit=BAD_REQUEST)
    args = {"operator": name, "skill": skill, "rank": rank}

    def body(g, pr):
        from maapilot.crew import mastery as ms
        pr.stage = "训练室"
        res = ms.start_mastery(g, name, skill, depot_names, max_rank=rank)
        status = res.get("status", "")
        data = {"operator": name, "skill": skill, "status": status, "rank": res.get("target_rank"),
                "hours": res.get("hours"), "short": res.get("short") or None, "shot": rel(res.get("shot"))}
        if res.get("craft", {}).get("batches"):
            data["crafted"] = [{"item": i, "times": t} for i, t in res["craft"]["batches"]]
        if status in ("started", "reached"):
            return data
        if status.startswith("blocked"):
            raise CliError("short_materials", f"材料不够，没开专精：{status[len('blocked: '):]}",
                           hint="缺的材料要去刷；能合成的已经试过合成", exit=FAILED, data=data)
        code, msg, hint = _MASTERY_FAIL.get(status, ("shortcut_failed", f"没确认专精开始（{status}）", "看截图"))
        raise CliError(code, msg, hint=hint, exit=FAILED, data={**data, "stage": pr.stage})

    return run_shortcut("op mastery", args, f"op_mastery_error_{name}", body)


# --- op progress：MaaCore 的干员培养 ------------------------------------------------------

PROGRESS_MIN_CORE = "v6.19.0"
PROGRESS_TIMEOUT_S = 3600
# OperProgressProcessTask 的 ResultDetail / Result（按枚举顺序；回调里可能是名字也可能是序号）
RESULT_DETAILS = ("Completed", "AlreadySatisfied", "ResourceInsufficient", "OperatorNotFound", "PrerequisiteNotMet",
                  "ChipNotCraftable", "FormulaLocked", "Unsupported", "RecognitionFailed", "TrainingRoomBusy",
                  "PrerequisiteTraining", "Interrupt")
RESULTS = ("Success", "Failed", "Skipped")
DETAIL_TEXT = {"Completed": "做完了", "AlreadySatisfied": "本来就到了", "ResourceInsufficient": "材料不够（合成也补不上）",
               "OperatorNotFound": "干员列表里没找到", "PrerequisiteNotMet": "前置练度不够",
               "ChipNotCraftable": "芯片合不出来", "FormulaLocked": "配方没解锁", "Unsupported": "MAA 不支持",
               "RecognitionFailed": "MAA 认不出页面", "TrainingRoomBusy": "训练室正在练别的",
               "PrerequisiteTraining": "开了前一级专精（一次只开一级）", "Interrupt": "被中断"}
# 失败时 error.code：按第一条失败的 result_detail
_PROGRESS_FAIL = {"ResourceInsufficient": "short_materials", "ChipNotCraftable": "short_materials",
                  "FormulaLocked": "short_materials", "OperatorNotFound": "operator_not_found",
                  "PrerequisiteNotMet": "prerequisite_not_met", "TrainingRoomBusy": "training_busy"}


def _enum(v, names: tuple) -> str:
    if isinstance(v, int) and 0 <= v < len(names):
        return names[v]
    return str(v)


def progress_plan(name: str, elite: int | None, skill_level: int | None, mastery: list[str] | None) -> tuple[dict, list[str]]:
    """参数 → (给 MaaCore 的一条 plan, 按 MaaCore 执行顺序的步骤名)。写错 → 退出码 2。"""
    info = check_operator(name)
    plan: dict = {"name": name}
    steps: list[str] = []
    if info.get("profession"):
        plan["role"] = info["profession"]               # 同名多职业（阿米娅）时 MaaCore 要求写明
    maxes = info.get("max_levels") or []
    if elite is not None:
        if elite not in (1, 2) or elite >= len(maxes):
            raise CliError("bad_arguments", f"--elite 只能是 1 或 2（{name} 最高精{len(maxes) - 1}）", exit=BAD_REQUEST)
        plan["elite"] = elite
        steps.append(f"精英化到精{elite}")
    if skill_level is not None:
        if not 2 <= skill_level <= 7:
            raise CliError("bad_arguments", "--skill-level 是技能等级 2–7", exit=BAD_REQUEST)
        plan["skill_level"] = skill_level
        steps.append(f"技能升到 {skill_level} 级")
    if mastery:
        arr = [0, 0, 0]
        for m in mastery:
            k, _, r = m.partition(":")
            if not (k.isdigit() and r.isdigit()) or not 1 <= int(k) <= 3 or not 1 <= int(r) <= 3:
                raise CliError("bad_arguments", f"--mastery 写成「技能:专几」，如 3:2（收到 {m}）", exit=BAD_REQUEST)
            if int(k) > (info.get("skills") or 0):
                raise CliError("bad_arguments", f"{name} 没有第 {k} 个技能", exit=BAD_REQUEST)
            arr[int(k) - 1] = int(r)
        plan["skill_mastery"] = arr
        steps += [f"技能{i + 1} 专{r}" for i, r in enumerate(arr) if r]
    if not steps:
        raise CliError("bad_arguments", "至少给 --elite / --skill-level / --mastery 之一", exit=BAD_REQUEST)
    return plan, steps


def progress_result(name: str, steps: list[str], run, plan: dict | None = None) -> dict:
    """MaaCore 的回调 → data；没做成抛 CliError（data 同结构）。"""
    details = run.extra("OperProgressDetail")
    summary = (run.extra("OperProgressSummary") or [None])[-1]
    rows = []
    for i, d in enumerate(details):
        detail = _enum(d.get("result_detail"), RESULT_DETAILS)
        result = _enum(d.get("result"), RESULTS)
        # 找干员失败时只有一条（没有成功的、后面也没有了）；其余按 MaaCore 的执行顺序（精英化 → 技能等级 → 各技能专精）对上
        locate_failed = detail == "OperatorNotFound" or (len(details) == 1 and len(steps) > 1 and result == "Failed")
        step = "找干员" if locate_failed else (steps[i] if i < len(steps) else "?")
        row = {"step": step, "result": result, "detail": detail, "text": DETAIL_TEXT.get(detail, detail)}
        if "elite" in d:
            row["elite"] = d["elite"]
        if "skill_mastery" in d:
            row["skill_mastery"] = d["skill_mastery"]
        rows.append(row)
    from maapilot.tools import live_box
    live_box.from_progress(name, plan or {}, rows)
    data = {"operator": name, "steps": rows,
            "summary": None if summary is None else {k: summary.get(k) for k in ("success", "failed", "skipped")},
            "training_started": any(r["detail"] in ("Completed", "PrerequisiteTraining") and r["step"].startswith("技能")
                                    and "专" in r["step"] for r in rows),
            "core": run.version, "log": rel(run.log), "duration_s": run.duration_s}
    if run.timed_out:
        raise CliError("maa_failed", f"干员培养超时（{PROGRESS_TIMEOUT_S // 60} 分钟），已停下", exit=FAILED, data=data,
                       hint="screen shot 看看停在哪；log 里是 MaaCore 的全部回报")
    if run.error:
        code, exit_ = ("maa_failed", FAILED) if run.returncode not in (10,) else ("maacore_broken", ENVIRONMENT)
        raise CliError(code, run.error, exit=exit_, data=data, hint="看 log；连不上就 maapilot doctor")
    if summary is None:
        raise CliError("maa_failed", "MaaCore 跑完了但没给结果", exit=FAILED, data=data, hint="看 log、screen shot")
    bad = [r for r in rows if r["result"] != "Success"]
    if bad:
        first = bad[0]
        raise CliError(_PROGRESS_FAIL.get(first["detail"], "shortcut_failed"),
                       f"{name} {first['step']}没做成：{first['text']}", exit=FAILED, data=data,
                       hint="screen shot 看看停在哪；材料不够就去刷，认不出就在界面上自己做")
    return data


def _elite_now(name: str) -> int | None:
    """练度快照（state box / skland）里的精英阶段；没有快照或没这个人 → None。"""
    from maapilot.tools import common
    snap = common.read_json(paths.STATE / "box.json", {}) or {}
    return ((snap.get("operators") or {}).get(name) or {}).get("elite")


def progress(name: str, elite: int | None, skill_level: int | None, mastery: list[str] | None,
             allow_catalyst: bool) -> dict:
    from maapilot.crew import maacore
    from maapilot.tools import common
    plan, steps = progress_plan(name, elite, skill_level, mastery)
    if plan.get("elite") == 2 and (check_operator(name).get("rarity") or 0) >= 5 and not allow_catalyst \
            and _elite_now(name) != 2:
        raise CliError("needs_consent", "5、6 星精二要双芯片：MAA 会在制造站造，芯片助剂不够时会用采购凭证去采购中心买，关不掉",
                       exit=BAD_REQUEST, data={"plan": plan},
                       hint="先问用户同不同意用采购凭证买芯片助剂；同意了加 --allow-catalyst 再跑")
    found = maacore.find()
    if found is None or not maacore.at_least(found[0], PROGRESS_MIN_CORE):
        raise CliError("maacore_missing", f"干员培养要单独装 MaaCore {PROGRESS_MIN_CORE} 以上"
                       + (f"（现在是 {found[0]}）" if found else ""), exit=ENVIRONMENT, hint="maapilot doctor --fix")
    from maapilot.tools import maa_cmd
    with common.device("op progress", job=f"op progress {name}") as lk:
        # MaaCore 的干员培养只会从首页进干员列表（真机：停在干员详情页时它点错、翻到了别的干员），所以先回首页
        if not maa_cmd.go_home():
            raise CliError("not_home", "回不到首页，没开始干员培养", exit=FAILED, data={"plan": plan},
                           hint="screen shot 看看停在哪，回首页后再试")
        with lk.keepalive():
            run = maacore.run_task("OperProgress", {"plans": [plan]}, label="op-progress", timeout_s=PROGRESS_TIMEOUT_S)
        home = maa_cmd.go_home()                        # 做完停在干员页；回首页，后面的命令从首页开始
    try:
        data = progress_result(name, steps, run, plan)
    except CliError as e:
        e.data = {**(e.data or {}), "plan": plan, "home": home}
        fb = _mastery_fallback_step(e.data)
        if fb is None or not home:
            raise
        return _mastery_fallback(name, e.data, fb, run)
    data.update(plan=plan, home=home)
    return data


# MaaCore 的训练室选人只认卡片上的名字；在基建设施里工作的干员卡片上显示的是设施名（"制造站B201"），
# 它就找不到人、报 RecognitionFailed（10-14 真机）。专精这一步认不出页面时改用我们自己的专精流程接着做。
TRAINEE_NOT_FOUND = "select_training_trainee | oper not found"


def _mastery_fallback_step(data: dict) -> tuple[int, int] | None:
    """第一条没做成的是"技能K 专M"且是认不出页面 → (K, M)；否则 None。"""
    bad = next((r for r in data.get("steps") or [] if r.get("result") != "Success"), None)
    if not bad or bad.get("detail") != "RecognitionFailed":
        return None
    step = bad.get("step") or ""
    if not (step.startswith("技能") and " 专" in step):
        return None
    k, _, m = step[len("技能"):].partition(" 专")
    return (int(k), int(m)) if k.isdigit() and m.isdigit() else None


def _mastery_fallback(name: str, data: dict, km: tuple[int, int], run) -> dict:
    from maapilot.tools import live_box
    k, m = km
    reason = ("训练室选人名单里没认出他（多半在基建设施里工作，卡片上显示的是设施名）"
              if any(TRAINEE_NOT_FOUND in w for w in run.warnings) else "MAA 认不出页面")
    try:
        md = mastery(name, k, m)
    except CliError as e:
        e.message = f"干员培养的技能{k} 专{m}没做成（{reason}），改用旧的专精命令也没做成：{e.message}"
        e.data = {**data, "fallback": {"command": "op mastery", "reason": reason, **(e.data or {})}}
        raise
    if md.get("status") == "started" and (md.get("rank") or 0) > 1:
        live_box.record(name, source="op mastery", mastery={k: md["rank"] - 1})
    data["fallback"] = {"command": "op mastery", "reason": reason, **md}
    data["training_started"] = md.get("status") == "started"
    return data


def register(sub) -> None:
    from maapilot.tools import common
    g = sub.add_parser("op", help="养成快捷操作（升级、精英化、升技能、专精、干员培养）")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")

    p = gs.add_parser("upgrade", help="升级 / 精英化 / 升技能到指定练度")
    p.add_argument("operator")
    p.add_argument("--elite", type=int)
    p.add_argument("--level", type=int)
    p.add_argument("--skill", type=int, help="通用技能等级 1–7")
    p.set_defaults(func=lambda a: upgrade(a.operator, a.elite, a.level, a.skill), command="op upgrade")

    p = gs.add_parser("mastery", help="在训练室开始技能 K 的下一级专精（不超过专 M）")
    p.add_argument("operator")
    p.add_argument("--skill", type=int, required=True, help="第几个技能 1–3")
    p.add_argument("--rank", type=int, required=True, help="最高专几 1–3")
    p.set_defaults(func=lambda a: mastery(a.operator, a.skill, a.rank), command="op mastery")

    p = gs.add_parser("progress", help="交给 MAA 的干员培养：精英化、技能等级、专精（缺的材料 MAA 自己合）")
    p.add_argument("operator")
    p.add_argument("--elite", type=int, help="精英阶段 1–2")
    p.add_argument("--skill-level", type=int, help="技能等级 2–7")
    p.add_argument("--mastery", action="append", metavar="K:M", help="技能 K 专到 M（可写多次；一次只开一级）")
    p.add_argument("--allow-catalyst", action="store_true", help="用户同意了缺芯片助剂时用采购凭证买")
    p.set_defaults(func=common.logged(lambda a: progress(a.operator, a.elite, a.skill_level, a.mastery,
                                                         a.allow_catalyst)), command="op progress")
