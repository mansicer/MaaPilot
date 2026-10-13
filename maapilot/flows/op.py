"""`maapilot op upgrade / mastery`：养成快捷操作（旧版脚本 maapilot.crew.upgrade / mastery 的薄外壳）。契约见 docs/cli.md。

能做成就省事；做不成老实报失败（退出码 1 shortcut_failed，带做到哪一步和截图），agent 按 maapilot-training 在界面上自己做。
花了多少不在这里算：要知道就前后 `state depot`。
"""
from __future__ import annotations

from maapilot.cliutil import BAD_REQUEST, FAILED, CliError
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


def register(sub) -> None:
    g = sub.add_parser("op", help="养成快捷操作（升级、精英化、升技能、专精）")
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
