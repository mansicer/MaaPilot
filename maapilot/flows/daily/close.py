"""收尾：agent 做完这次日常（目标部分、领奖励、对账）后用 `daily done --summary …` 收尾。总结写进汇报的「目标部分」，
清掉"没收尾"的记录（base.open_daily），重新生成汇报。超过 OPEN_STALE_H 小时没收尾的，下次 `daily run` 开始时程序自动收尾（close_run(by="auto")）。"""
from __future__ import annotations

from pathlib import Path

from maapilot.cliutil import BAD_REQUEST, BUSY, CliError
from maapilot.flows import report as reportmod
from maapilot.flows._common import now
from maapilot.oscompat import fs
from maapilot.flows import daily          # 测试替换的名字经包取（daily.X），替换才生效

SUMMARY_MAX = 20000
RESULTS = ("完成", "没做成")


def close_run(run_id: str, summary: str | None, *, alert: str | None = None, by: str = "agent",
              result: str | None = None) -> dict:
    """收尾一次日常：summary 进汇报的「目标部分」，alert 进「要注意」，清掉"没收尾"的记录。汇报读不了也照样清掉。→ 汇报的路径或 None。"""
    report = None
    try:
        d = reportmod.load(run_id)
    except CliError:
        d = None
    if d is not None:
        at = now().isoformat(timespec="seconds")
        if summary:
            d["goals_summary"] = {"at": at, "text": summary, "by": by, "result": result}
        if alert:
            d.setdefault("alerts", []).append(alert)
        d.update(open=False, closed_at=at, closed_by=by)
        d.pop("next", None)
        d["lines"] = reportmod.summary_lines(d)
        try:
            report = reportmod.save(d)
        except OSError:
            report = None
    daily._clear_open(run_id)
    return {"report": report, "found": d is not None}


def _summary_text(summary: str | None, summary_file: str | None) -> str:
    if (summary is None) == (summary_file is None):
        raise CliError("bad_arguments", "--summary 和 --summary-file 要写一个（只写一个）", exit=BAD_REQUEST)
    if summary_file is not None:
        try:
            summary = fs.read_text_utf8(Path(summary_file))
        except fs.BadEncoding as e:
            raise CliError("bad_arguments", f"{summary_file} 不是 UTF-8：{e}", exit=BAD_REQUEST) from None
        except OSError as e:
            raise CliError("bad_arguments", f"读不了 {summary_file}：{e}", exit=BAD_REQUEST) from None
    text = (summary or "").strip()
    if not text:
        raise CliError("bad_arguments", "总结是空的", hint="用几句中文写做了什么、目标进度、花了什么、要用户决定的事",
                       exit=BAD_REQUEST)
    return text


def report_done(summary: str | None, summary_file: str | None, result: str | None) -> dict:
    """`daily done`：agent 收尾没收尾的那次日常。没有 → 退出码 2 not_in_daily；daily run 还在跑 → 退出码 4。"""
    op = daily.open_daily()
    if not op:
        raise CliError("not_in_daily", "没有要收尾的日常（没跑过，或者已经收尾了）", exit=BAD_REQUEST,
                       hint="maapilot daily status 看看；要补一段说明用 maapilot report write <run_id> --append …")
    cur = daily.running()
    if cur:
        raise CliError("daily_running", f"日常 {cur.get('run_id')} 还在跑，跑完再收尾", exit=BUSY,
                       hint="等 daily run 结束", data={"running": cur})
    text = _summary_text(summary, summary_file)
    if result:                                          # 最后一行写成「结果：…」（原来写了就换掉；汇报里不显示这一行）
        text = reportmod.agent_text({"summary": text})
        text = (text + "\n" if text else "") + f"结果：{result}"
    truncated = len(text) > SUMMARY_MAX
    if truncated:                                       # 太长：留开头和最后一行
        text = text[:SUMMARY_MAX - 500].rstrip() + "\n……（太长，后面截掉了）\n" + text.splitlines()[-1][-400:]
    out = close_run(str(op["run_id"]), text, by="agent", result=result)
    return {"run_id": op["run_id"], "closed": True, "chars": len(text), "truncated": truncated,
            "report": out["report"]}
