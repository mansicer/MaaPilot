"""给无头 agent 的提示：接管失败项、目标部分、到期目标、停机维护、更新游戏。"""
from __future__ import annotations

import shlex

from maapilot.flows import report as reportmod

# 每个无头 agent 的提示都带上：语言、总结怎么交（最后的回复就是总结，程序放进汇报；自己再追加会重复）
REPORT_RULES = ("用户偏好在 private/memory/MEMORY.md（目录）和同目录的文件里，做决定前先看。"
                "全程用中文（包括中间的说明），总结写给用户看。"
                "不要用 maapilot report write 往汇报里追加：你最后的回复就是总结，程序会把它放进这次的汇报。")


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
    out.append(f"查原因：maapilot maa why --task {task}（MAA 详细日志里这段时间的经过：做了什么、看到什么字、等了多久、出错；原因你自己判断）。")
    return out


def recovery_prompt(run_id: str, task: str, err: dict, data: dict | None, argv: list[str] | None = None,
                    cli: list[str] | None = None) -> str:
    """argv：`maapilot maa` 后面的部分；不是 maa 任务的项（识别干员练度）用 cli 给整条 maapilot 命令。"""
    log = (data or {}).get("log")
    cli = cli or ["maa", *(argv or [task])]
    cmd = " ".join(shlex.quote(a) for a in cli)
    why_task = cli[1] if cli[0] == "maa" and len(cli) > 1 else None
    return "\n".join([
        f"你是 MaaPilot 的无头 agent，被定时日常（运行 {run_id}）叫醒，用户不在场。",
        f"日常项「{reportmod.label(task)}」（maapilot {cmd}）刚才失败了：{err.get('message') or err.get('code')}。",
        f"MAA 日志：{log}。" if log else "",
        *(evidence_lines((data or {}).get("evidence"), why_task) if why_task else []),
        f"重跑这一项用 maapilot {cmd}（照抄这条命令）。",
        "加载 skill maapilot-recovery，只处理这一项：先看上面的截图、查原因，再截图看现在停在哪，处理卡点，需要时重跑这一项，最后回到游戏首页。",
        "被登出、掉线（停在开始唤醒的标题页）：用户不在场，直接重新登录（maapilot maa startup）再重跑这一项，并写进总结。",
        "不做别的日常项、不刷理智、不改目标。要用户决定或确认的事一律不做，写进总结。",
        "先 maapilot lock status：inherited 应为 true（日常把模拟器锁交给了你）；不是就别操作，写进总结后结束。",
        "长命令（maa 任务）在前台跑、Bash 超时设 600000 毫秒，不放后台。",
        REPORT_RULES,
        "结束时用几句中文写做了什么、现在怎样；最后一行单独写「结果：完成」或「结果：没做成」。",
    ]).replace("\n\n", "\n")


def _titles(goals: list) -> str:
    return "、".join(f"「{g.get('title') if isinstance(g, dict) else g}」" for g in goals or [])


def expired_line(expired: list) -> str:
    """开头到期挪走的目标 → 给 agent 的一句话。"""
    return (f"这些限时目标到期，程序已挪进已完成（goals-done.md）：{_titles(expired)}；"
            "看一眼还有没有没做完的（例如活动商店没换完、活动关没三星），写进总结告诉用户。")


def goals_prompt(run_id: str, active: list[dict], tasks: list[dict], expired: list | None = None) -> str:
    done = "、".join(f"{reportmod.label(t['task'])}{'' if t.get('ok') else '（没做成）'}" for t in tasks) or "无"
    titles = "；".join(f"{g['title']}（{g.get('object') or '?'}）" for g in active)
    return "\n".join([
        f"你是 MaaPilot 的无头 agent，被定时日常（运行 {run_id}）叫醒，用户不在场。",
        f"MAA 日常项已经跑完：{done}。",
        f"private/goals.md 里有 {len(active)} 个进行中的目标：{titles}。",
        *([expired_line(expired)] if expired else []),
        "加载 skill maapilot-daily，做目标部分：用剩下的理智刷目标缺的材料，材料够了就养成，"
        "在 goals.md 里更新分解与进展，做完的目标移除。",
        "用户不在场：目标之外的花费、要用户决定或确认的事一律不做，写进总结。",
        "先 maapilot lock status：inherited 应为 true（日常把模拟器锁交给了你）；不是就别操作，写进总结后结束。",
        "长命令在前台跑、Bash 超时设 600000 毫秒，不放后台；刷图按 skill 里说的分批（--times 每批几次）。",
        REPORT_RULES,
        "结束时用几句中文写做了什么、花了什么、目标进展到哪，以及要用户决定的事；最后一行单独写「结果：完成」或「结果：没做成」。",
    ])


def expired_prompt(run_id: str, expired: list, tasks: list[dict]) -> str:
    """没有进行中的目标、只有开头到期挪走的目标：叫一个短的 agent 看一眼，告诉用户。"""
    done = "、".join(f"{reportmod.label(t['task'])}{'' if t.get('ok') else '（没做成）'}" for t in tasks) or "无"
    return "\n".join([
        f"你是 MaaPilot 的无头 agent，被定时日常（运行 {run_id}）叫醒，用户不在场。",
        f"日常已经跑完：{done}。没有进行中的目标。",
        expired_line(expired),
        "maapilot goals list --all 看它们的记录（goals-done.md 里）；要看游戏里的情况就截图看（maapilot screen shot），"
        "不刷理智、不兑换、不花任何东西、不改目标，要用户决定的事写进总结。",
        "先 maapilot lock status：inherited 应为 true（日常把模拟器锁交给了你）；不是就别操作模拟器，只看文件。",
        REPORT_RULES,
        "结束时用几句中文写给用户看的总结；最后一行单独写「结果：完成」。",
    ])


def maintenance_prompt(run_id: str, window: dict, info: dict | None = None, expired: list | None = None) -> str:
    """官方停机维护中：日常什么都没跑，叫无头 agent 知道这件事、想想对目标的影响。info：查到的游戏版本（可能没有）。"""
    from datetime import datetime
    try:
        s, e = datetime.fromisoformat(window["start"]), datetime.fromisoformat(window["end"])
        when = f"{s.month}月{s.day}日 {s:%H:%M} 到 " + (f"{e:%H:%M}" if e.date() == s.date()
                                                      else f"{e.month}月{e.day}日 {e:%H:%M}")
    except (KeyError, TypeError, ValueError):
        when = "时间不明"
    ver = ""
    if info and (info.get("installed") or info.get("latest")):
        ver = f"现在模拟器上的游戏是 {_ver(info.get('installed'))}，官方最新 {_ver(info.get('latest'))}。"
    return "\n".join([
        f"你是 MaaPilot 的无头 agent，被定时日常（运行 {run_id}）叫醒，用户不在场。",
        f"官方正在停机维护（{when}，公告「{window.get('title') or '?'}」），这次日常的 MAA 项都没跑（进不了游戏）。",
        ("维护后要强制更新游戏：下次日常会先更新，或者会话里的 agent 按 skill maapilot-events 处理；你现在不用更新。" + ver)
        if window.get("forced_update") else ver,
        "维护期间进不了游戏：不要操作模拟器（不跑 maa、input、game、state 这类命令）。"
        "可以 maapilot news list / maapilot news show <cid> 看公告，maapilot goals list 看进行中的目标。",
        "想想这次维护对进行中的目标有什么影响（例如活动什么时候开始、限时目标的期限、维护期间理智满了会浪费多少），"
        "写成给用户看的几句中文总结；要用户决定的事写清楚。不改目标、不花任何东西。",
        (f"这些限时目标到期，程序已挪进已完成（goals-done.md）：{_titles(expired)}；"
         "看看记录里还有没有没做完的，写进总结告诉用户。") if expired else "",
        REPORT_RULES,
        "最后一行单独写「结果：完成」。",
    ]).replace("\n\n", "\n")


def _ver(v: dict | None) -> str:
    return str((v or {}).get("version") or "?")


def update_prompt(run_id: str, info: dict) -> str:
    """游戏客户端旧了：叫无头 agent 更新、第一次启动。"""
    terms = ("用户协议 / 隐私政策更新的弹窗：直接点「同意」，写进总结。" if info.get("accept_terms") else
             "用户协议 / 隐私政策更新的弹窗：不要点同意，停下，写进总结（要用户决定的事），最后写「结果：没做成」。")
    return "\n".join([
        f"你是 MaaPilot 的无头 agent，被定时日常（运行 {run_id}）叫醒，用户不在场。",
        f"模拟器上的游戏客户端旧了（装的是 {_ver(info.get('installed'))}，最新是 {_ver(info.get('latest'))}），"
        "不更新什么日常都做不了。你只做更新游戏这一件事。",
        "先 maapilot lock status：inherited 应为 true（日常把模拟器锁交给了你）；不是就别操作，写进总结后结束。",
        "1. 跑 maapilot game update（前台跑、Bash 超时设 600000 毫秒，不放后台）。可能超过 10 分钟：Bash 超时了就再跑一次同一条命令，"
        "它会接着下载，直到它说装好了。",
        "2. 跑 maapilot maa startup，处理第一次启动：加载 skill maapilot-recovery，截图看停在哪，游戏里要下载资源的提示点确认、"
        "公告弹窗关掉，直到回到游戏首页。",
        terms,
        "不做别的日常项、不刷理智、不改目标。别的要用户决定或确认的事一律不做，写进总结。",
        REPORT_RULES,
        "结束时用几句中文写做了什么、现在是哪个版本、停在哪；最后一行单独写「结果：完成」或「结果：没做成」。",
    ])
