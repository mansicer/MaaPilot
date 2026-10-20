"""`maapilot daily run / done / status / unschedule`：日常。契约见 docs/cli.md「daily」，设计见 docs/design.md「运行方式」。

agent 一直在场（用户的会话里；定时日常是 harness 的会话内定时在会话里叫醒它）。daily run 只做编排，
每一步都调别的命令（子进程 `python -m maapilot.cli …`，继承本进程的模拟器锁）：
  开始前：限时目标到期、官方公告（旧了重拉；停机维护中 → MAA 项、对账都不跑，status maintenance）、
  游戏版本（旧了：停下交给 agent 更新）
  → startup（配了的话）→ guard snapshot（上次之后有人动过就改做对账）→ 其余 MAA 日常项（award 留到最后）
  → 识别干员练度 → 刷理智（有进行中的目标交给 agent：goals_pending，没有就刷默认关）→ award → guard check → 汇报。
某项失败就停下交给 agent（handoff，rest 是还没跑的），它处理好后 `daily run --only <rest>` 接着写同一份汇报。
`daily run` 开始时记下"没收尾的日常"（base.open_daily），进程结束后还在，直到 agent 做完目标部分、领奖励、对账后
`daily done --summary …` 收尾（close）；这段时间里 `guard allow` 一律拒绝（tools/confirm.py）。
`daily unschedule` 只删 v0.1 留下的系统定时（crontab 行 / Windows 任务，见 cron）。

按阶段分成几个模块，这里是门面：别的模块和测试都 `from maapilot.flows import daily` 再用 `daily.X`。
  base       常量、运行标记、没收尾的日常、一次运行的记录（_Run / _Stop）
  calls      子进程（maapilot_call）、任务超时后留下的 maa
  preflight  开始前：目标到期、官方公告与维护、游戏版本
  tasks      对账、仓库差、识别干员练度、跑一个 MAA 项、剿灭
  farm       刷理智：读理智、算时限
  steps      daily run 的编排
  close      daily done：收尾
  cron       v0.1 的系统定时（只认、只删）与 daily status
测试替换的名字（maapilot_call、refresh_news、maintenance_now、version_info、running、open_daily、_set_step、_leftover_maa、
procs.kill_pids、TIMEOUT_LEFTOVER_WAIT_S、_stage_cost、now）子模块都经 `daily.X` 取，替换才生效；子模块里新用到这些名字也要这样取。
"""
from __future__ import annotations


from maapilot.flows._common import now  # noqa: F401 - 测试替换 daily.now（_box_fresh 经它取）
from maapilot.flows.daily.base import (  # noqa: F401
    BOX, BOX_TIMEOUT_MIN, FARM, OPEN_STALE_H, TASK_TIMEOUT_MIN, UPDATE, _clear_open, _drop_marker, _err, _marker,
    _new_run_id, _open_file, _read_marker, _Run, _set_step, _Stop, _take_marker, _write_open, open_brief, open_daily,
    running)
from maapilot.flows.daily.calls import (  # noqa: F401
    LEFTOVER_KILL_GRACE_S, TIMEOUT_LEFTOVER_WAIT_S, _leftover_maa, _run_group, _stop_leftover_maa, maapilot_call)
from maapilot.flows.daily.tasks import (  # noqa: F401
    SKLAND_TIMEOUT_S, _annihilation_failed, _annihilation_skip, _box_fresh, _box_from_skland, _depot_snapshot,
    _guard, _maa_task, depot_change)
from maapilot.flows.daily.farm import (  # noqa: F401
    DEFAULT_STAGE_COST, FARM_MARGIN_MIN, FARM_MIN_PER_FIGHT, SANITY_FRESH_MIN, _age_min, _sanity_estimate,
    _stage_cost, farm_timeout, read_sanity_for_farm)
from maapilot.flows.daily.preflight import (  # noqa: F401
    _game_version, _goals_change, _preflight, expire_goals, goals_diff, goals_snapshot, maintenance_now, refresh_news,
    version_info)
from maapilot.flows.daily.close import close_run, report_done  # noqa: F401
from maapilot.flows.daily.steps import _finish, _steps, next_hint, run  # noqa: F401
from maapilot.oscompat import procs  # noqa: F401 - 测试替换 daily.procs.kill_pids（就是 procs 模块本身）
from maapilot.flows.daily.cron import legacy_schedule, status, unschedule, unschedule_cmd  # noqa: F401


# --- 命令 ------------------------------------------------------------------------------------------------------------

def _split(v: str | None) -> list[str] | None:
    if not v:
        return None
    return [x.strip() for x in v.split(",") if x.strip()]


def register(sub) -> None:
    g = sub.add_parser("daily", help="日常")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")

    p = gs.add_parser("run", help="跑一次日常（20–60 分钟，用后台运行）；没收尾时 --only 接着跑同一次")
    p.add_argument("--only", help="只跑这几项（逗号分隔；刷理智写 farm、识别干员练度写 box）")
    p.add_argument("--trigger", choices=["manual", "schedule"], default="manual",
                   help="schedule：这次是定时叫醒的（只写进汇报，做法一样）；manual（默认）")
    p.set_defaults(func=lambda a: run(_split(a.only), a.trigger), command="daily run")

    p = gs.add_parser("done", help="收尾这次日常：总结写进汇报的「目标部分」")
    p.add_argument("--summary", help="给用户看的几句中文总结")
    p.add_argument("--summary-file", help="总结写在这个文件里（UTF-8）")
    p.add_argument("--result", choices=["完成", "没做成"], help="这次做成没有（写成总结最后一行「结果：…」）")
    p.set_defaults(func=lambda a: report_done(a.summary, a.summary_file, a.result), command="daily done")

    p = gs.add_parser("status", help="有没有在跑、有没有没收尾的日常、上次日常")
    p.set_defaults(func=lambda a: status(), command="daily status")

    p = gs.add_parser("unschedule", help="删掉 v0.1 留下的系统定时（crontab 行 / Windows 任务计划程序的任务）")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=lambda a: unschedule_cmd(a.dry_run), command="daily unschedule")
