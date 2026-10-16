"""`maapilot daily run / status / schedule / unschedule`：日常与定时。契约见 docs/cli.md「daily」，设计见 docs/design.md 第 6 节。

daily run 只做编排，每一步都调别的命令（子进程 `python -m maapilot.cli …`，继承本进程的模拟器锁）：
  开始前：限时目标到期、官方公告（旧了重拉；停机维护中 → MAA 项、对账都不跑，status maintenance；
  定时运行叫一个不碰模拟器的 agent 看看对目标的影响）、
  游戏版本（旧了：定时运行叫无头 agent 更新，会话里交给在场的 agent）
  → startup（配了的话）→ guard snapshot（上次之后有人动过就改做对账）→ 其余 MAA 日常项（award 留到最后）
  → 刷理智（有进行中的目标交给 agent，没有就刷默认关）→ award → guard check → 汇报。
定时运行（--trigger schedule）里某项失败，当场起一个无头 `claude -p`（skill maapilot-recovery）接管这一项，
等它结束（超时杀掉整个进程组）再接着跑；每个 agent 结束后换一次锁的 token（它留下的命令不能再进来）、对一次账。
手动运行（默认，agent 在会话里）不起无头 agent，失败记下交回在场的 agent；有进行中的目标时刷理智和 award
都交给在场的 agent（goals_pending），它做完自己领奖励、对账、补汇报。

定时：crontab 里本项目自己的行带标记 `# maapilot-v2 <仓库路径>`，只增删这些行，别的行一个字节都不动；Windows 上是任务计划程序里的一个任务。

按阶段分成几个模块，这里是门面：别的模块和测试都 `from maapilot.flows import daily` 再用 `daily.X`。
  base       常量、运行标记、一次运行的记录（_Run / _Stop）
  agent      子进程（maapilot_call）、无头 agent（run_agent）、agent 留下的 maa
  prompts    给无头 agent 的提示
  preflight  开始前：目标到期、官方公告与维护、游戏版本
  tasks      对账、仓库差、识别干员练度、跑一个 MAA 项、剿灭
  farm       刷理智：读理智、算时限
  steps      daily run 的编排
  cron       定时（crontab / 任务计划程序）与 daily status
测试替换的名字（maapilot_call、run_agent、refresh_news、maintenance_now、version_info、running、schedule_info、
_set_step、push_session、_leftover_maa、procs.kill_pids、LEFTOVER_WAIT_S、TIMEOUT_LEFTOVER_WAIT_S、_stage_cost、now）子模块都经 `daily.X` 取，
替换才生效；子模块里新用到这些名字也要这样取。
"""
from __future__ import annotations


from maapilot.flows._common import now  # noqa: F401 - 测试替换 daily.now（_box_fresh 经它取）
from maapilot.flows.daily.base import (  # noqa: F401
    AGENT_TIMEOUT_MIN, BOX, BOX_TIMEOUT_MIN, DAILY_ENV, EXPIRED_AGENT_MIN, FARM, MAINTENANCE_AGENT_MIN,
    RECOVERY_TIMEOUT_MIN, TASK_TIMEOUT_MIN, UPDATE, _drop_marker, _err, _marker, _new_run_id, _read_marker, _Run,
    _set_step, _Stop, _take_marker, running)
from maapilot.flows.daily.agent import (  # noqa: F401
    _OUTCOME, LEFTOVER_KILL_GRACE_S, LEFTOVER_WAIT_S, TIMEOUT_LEFTOVER_WAIT_S, _after_agent, _claude_bin,
    _leftover_maa, _run_group, _stop_leftover_maa, agent_command, maapilot_call, parse_transcript, run_agent)
from maapilot.flows.daily.prompts import (  # noqa: F401
    REPORT_RULES, _titles, _ver, evidence_lines, expired_line, expired_prompt, goals_prompt, long_command_rule,
    maintenance_prompt, recovery_prompt, update_prompt)
from maapilot.flows.daily.tasks import (  # noqa: F401
    SKLAND_TIMEOUT_S, _annihilation_failed, _annihilation_skip, _box_fresh, _box_from_skland, _depot_snapshot,
    _guard, _maa_task, depot_change)
from maapilot.flows.daily.farm import (  # noqa: F401
    DEFAULT_STAGE_COST, FARM_MARGIN_MIN, FARM_MIN_PER_FIGHT, SANITY_FRESH_MIN, _age_min, _sanity_estimate,
    _stage_cost, farm_timeout, read_sanity_for_farm)
from maapilot.flows.daily.preflight import (  # noqa: F401
    _expired_agent, _game_version, _goals_change, _maintenance_agent, _preflight, expire_goals, goals_diff,
    goals_snapshot, maintenance_now, refresh_news, version_info)
from maapilot.flows.daily.steps import _finish, _steps, run, session_message  # noqa: F401
from maapilot.hosts import push as push_session  # noqa: F401 - 测试替换 daily.push_session
from maapilot.oscompat import procs  # noqa: F401 - 测试替换 daily.procs.kill_pids（就是 procs 模块本身）
from maapilot.flows.daily.cron import (  # noqa: F401
    _CRON_HM, _OLD_MARKS, _TIME, TAG, _crontab, _entries, _mine, _next_at, _old, _parse_times, _path_dirs,
    _times_of, cron_lines, read_crontab, schedule, schedule_cmd, schedule_info, status,
    unschedule, unschedule_cmd, write_crontab)


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

    p = gs.add_parser("watch", help="等下一次日常跑完再输出结果（只读；会话里一直在后台挂着一个）")
    p.add_argument("--timeout-min", type=float, help="最多等几分钟（默认 110：后台 Bash 最长 2 小时）")

    def _watch(a):
        from maapilot.flows import watch
        return watch.watch(a.timeout_min)
    p.set_defaults(func=_watch, command="daily watch")

    p = gs.add_parser("schedule", help="设定时任务（crontab 或 Windows 任务计划程序，只动本项目的）")
    p.add_argument("--times", help="HH:MM[,HH:MM]；默认 config.yaml 的 schedule")
    p.add_argument("--replace-old", action="store_true", help="同时删掉旧版 MaaPilot 的定时任务")
    p.add_argument("--dry-run", action="store_true", help="只给出改完的样子（crontab 全文或任务定义），不写")
    p.set_defaults(func=lambda a: schedule_cmd(a.times, a.replace_old, a.dry_run), command="daily schedule")

    p = gs.add_parser("unschedule", help="取消本项目的定时任务")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=lambda a: unschedule_cmd(a.dry_run), command="daily unschedule")
