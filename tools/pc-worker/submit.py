# -*- coding: utf-8 -*-
"""把一条命令交给提权 worker，等它跑完，打印输出。

用法：
    python submit.py [--timeout 秒] <maapilot 参数...>
    python submit.py --timeout 600 maa copilot prts://73344
    python submit.py screen shot

退出码：0 worker 正常返回且命令成功；1 命令失败；2 等不到结果（会删掉任务，免得重启后重跑）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(os.environ.get("MAAPILOT_ROOT", r"D:\salt\_mm\MaaPilot"))
HERE = Path(os.environ.get("MAA_WORKER_DIR", ROOT / "private" / "worker"))
JOBS = HERE / "jobs"
STATUS = HERE / "status.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=180.0, help="任务超时（秒）")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("argv", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    argv = [a for a in args.argv if a != "--"]
    if not argv:
        print("没有命令", file=sys.stderr)
        return 2

    st = {}
    try:
        st = json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        print("worker 没在跑：请以管理员身份启动 run-worker.bat", file=sys.stderr)
        return 2
    if st.get("il") != "High":
        print(f"worker 的完整性级别是 {st.get('il')}，点不动游戏（UIPI）。"
              f"请关掉后右键 run-worker.bat → 以管理员身份运行", file=sys.stderr)
        return 2

    jid = "me-" + uuid.uuid4().hex[:10]
    job = {"argv": argv, "timeout_s": max(10.0, args.timeout)}
    if args.cwd:
        job["cwd"] = args.cwd
    (JOBS / f"{jid}.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

    done = JOBS / f"{jid}.done.json"
    deadline = time.time() + job["timeout_s"] + 30
    while time.time() < deadline:
        if done.exists():
            d = json.loads(done.read_text(encoding="utf-8"))
            sys.stdout.write(d.get("stdout") or "")
            err = (d.get("stderr") or "").strip()
            if err:
                print("ERR: " + err[-600:], file=sys.stderr)
            return 0 if d.get("code") == 0 else 1
        time.sleep(0.6)

    try:                                        # 超时不留死任务：重启后会被重跑
        (JOBS / f"{jid}.json").unlink()
    except OSError:
        pass
    print("timeout：worker 没回结果", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
