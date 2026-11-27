# -*- coding: utf-8 -*-
"""常驻提权 worker：让没有管理员权限的 harness 能操控以管理员权限运行的《明日方舟》PC 客户端。

为什么需要它：游戏是 High 完整性进程，普通权限的进程点击会被 UIPI 挡掉（error=5）。
harness 自己拿不到提权，但可以**让用户以管理员身份启动一次这个 worker**，
之后往 jobs 目录里丢命令、读结果，输入就由提权进程投递。

用法（用户执行一次）：
    worker.bat            # 右键 → 以管理员身份运行；窗口留着别关

协议：
    jobs/<id>.json        {"argv": ["screen","shot"], "timeout_s": 120, "cwd": "可选"}
                          argv 会交给 <root>/.venv/Scripts/python.exe -m maapilot.cli 后面，
                          也可以给脚本路径（.py）直接跑。
    jobs/<id>.done.json   {"id", "ok", "code", "stdout", "stderr", "argv", "started", "ended"}
    status.json           {"pid", "admin", "il", "heartbeat", "done"}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("MAAPILOT_ROOT", r"D:\salt\_mm\MaaPilot"))
HERE = Path(os.environ.get("MAA_WORKER_DIR", ROOT / "private" / "worker"))
JOBS = HERE / "jobs"
STATUS = HERE / "status.json"
LOG = HERE / "worker.log"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
POLL_S = 0.5
done_count = 0
running = True


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def token_state() -> tuple[bool, str]:
    """(是否提权, 完整性级别)。降过完整性的提权令牌 IsUserAnAdmin() 仍是 True，所以两个都看。"""
    try:
        sys.path.insert(0, str(ROOT))
        from maapilot.crew import win32
        return win32.token_state()
    except Exception:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin()), "?"
        except Exception:
            return False, "?"


def _unique_tmp(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.tmp")


def _replace_retry(tmp: Path, dst: Path, tries: int = 5) -> None:
    for i in range(tries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.2)


def write_status(admin: bool, extra: dict | None = None) -> None:
    data = {"pid": os.getpid(), "admin": admin,
            "heartbeat": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "done": done_count, **(extra or {})}
    tmp = _unique_tmp(STATUS)
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _replace_retry(tmp, STATUS)


def answer(path: Path, payload: dict) -> None:
    out = path.with_suffix(".done.json")
    tmp = _unique_tmp(out)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    _replace_retry(tmp, out)


def run_job(path: Path) -> bool:
    """跑一个任务；返回 False 表示要退出。"""
    global done_count
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        answer(path, {"id": path.stem, "ok": False, "code": -1, "stdout": "",
                      "stderr": f"bad job json: {e}", "argv": [],
                      "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      "ended": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return True
    argv = spec.get("argv") or []
    if argv == ["__stop__"]:
        log("收到 __stop__，退出")
        answer(path, {"id": path.stem, "ok": True, "code": 0, "stdout": "stopping",
                      "stderr": "", "argv": argv,
                      "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      "ended": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return False

    cwd = spec.get("cwd") or str(ROOT)
    timeout = float(spec.get("timeout_s") or 300)
    if argv and str(argv[0]).lower().endswith(".py"):
        cmd = [str(PY), *[str(a) for a in argv]]
    else:
        cmd = [str(PY), "-m", "maapilot.cli", *[str(a) for a in argv]]
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    log(f"跑 {cmd[1:] if len(cmd) > 2 else cmd}")

    # 输出走临时文件：管道会被孙进程占住，子进程被杀后 communicate() 可能永远等下去
    import tempfile
    fo = tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8", errors="replace", suffix=".out")
    fe = tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8", errors="replace", suffix=".err")
    code, out, err = -1, "", ""
    try:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=fo, stderr=fe,
                                stdin=subprocess.DEVNULL, creationflags=flags)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            code, err = -9, f"超时 {timeout}s"
    except Exception as e:
        code, err = -1, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    finally:
        fo.close()
        fe.close()
        try:
            out = Path(fo.name).read_text(encoding="utf-8", errors="replace")
            if not err:
                err = Path(fe.name).read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        for f in (fo.name, fe.name):
            try:
                Path(f).unlink()
            except OSError:
                pass

    answer(path, {"id": path.stem, "ok": code == 0, "code": code, "stdout": out,
                  "stderr": err, "argv": argv, "started": started,
                  "ended": time.strftime("%Y-%m-%dT%H:%M:%S")})
    done_count += 1
    log(f"完成 {path.stem} -> code={code}")
    return True


def heartbeat_thread(admin: bool, il: str) -> None:
    while running:
        try:
            write_status(admin, {"il": il})
        except Exception:
            pass
        time.sleep(5)


def main() -> int:
    admin, il = token_state()
    JOBS.mkdir(parents=True, exist_ok=True)
    log(f"启动 pid={os.getpid()} admin={admin} il={il}")
    if not admin:
        log("!! 不是管理员：能跑，但碰不到游戏窗口（UIPI）。右键 worker.bat -> 以管理员运行")
    if il == "Low":
        log("!! 完整性级别是 Low：提权了也点不动游戏（UIPI）。见 README 的「低完整性」一节")
    write_status(admin, {"il": il})

    import threading
    threading.Thread(target=heartbeat_thread, args=(admin, il), daemon=True).start()

    while running:
        try:
            for path in sorted(JOBS.glob("*.json")):
                if path.name.endswith(".done.json"):
                    continue
                if path.with_suffix(".done.json").exists():
                    continue
                if not run_job(path):
                    write_status(admin, {"il": il, "stopped": True})
                    return 0
            write_status(admin, {"il": il})
        except Exception as e:
            log(f"循环出错：{type(e).__name__}: {e}")
        time.sleep(POLL_S)
    return 0


if __name__ == "__main__":
    sys.exit(main())
