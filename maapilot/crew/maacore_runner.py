"""直接加载 MaaCore 跑一个任务的子进程（`python -m maapilot.crew.maacore_runner`），由 maapilot.crew.maacore 启动。

为什么单独一个进程：MaaCore 是 C++ 动态库，崩溃、卡死会带走整个 Python 进程；放在子进程里，超时或被叫停时
父进程能整组结束它，maapilot 自己照常收尾（记日志、放锁）。

stdin：一个 JSON 对象 {lib, resources: [目录…], user_dir, adb, address, task, params, instance_options: {键: 值}}。
stdout：每行一个 JSON。{"event": ...} 是这里的进度（version / loaded / connected / appended / done），
{"msg": <AsstMsg>, "details": {...}} 是 MaaCore 的回调原样转出。
退出码：0 跑完；10 库或资源加载失败；11 连不上模拟器；12 任务参数被拒；13 启动失败；14 被叫停。
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import sys
import time

from maapilot.oscompat.procs import STOP_SIGNALS

CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_int32, ctypes.c_char_p, ctypes.c_void_p)

# AsstInstanceOptionKey
INSTANCE_OPTION_KEYS = {"touch_mode": 2, "deployment_with_pause": 3, "adb_lite_enabled": 4, "kill_adb_on_exit": 5}
STATIC_CPU_OCR = 1


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load(path: str):
    if hasattr(os, "add_dll_directory"):                # Windows：MaaCore.dll 依赖的 DLL 在它旁边
        os.add_dll_directory(os.path.dirname(os.path.abspath(path)))
    lib = ctypes.CDLL(path)
    lib.AsstGetVersion.restype = ctypes.c_char_p
    lib.AsstSetUserDir.argtypes = [ctypes.c_char_p]
    lib.AsstSetStaticOption.argtypes = [ctypes.c_int32, ctypes.c_char_p]
    lib.AsstLoadResource.argtypes = [ctypes.c_char_p]
    lib.AsstCreateEx.restype = ctypes.c_void_p
    lib.AsstCreateEx.argtypes = [CALLBACK, ctypes.c_void_p]
    lib.AsstSetInstanceOption.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_char_p]
    lib.AsstConnect.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.AsstAppendTask.restype = ctypes.c_int32
    lib.AsstAppendTask.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    for f in ("AsstStart", "AsstStop", "AsstRunning"):
        getattr(lib, f).argtypes = [ctypes.c_void_p]
    lib.AsstDestroy.argtypes = [ctypes.c_void_p]
    return lib


def main() -> int:
    for f in (sys.stdin, sys.stdout):                   # 父进程按 UTF-8 读写；Windows 上默认是系统代码页
        f.reconfigure(encoding="utf-8", errors="replace")
    spec = json.loads(sys.stdin.read())
    try:
        lib = load(spec["lib"])
    except OSError as e:
        emit({"event": "error", "stage": "load", "error": str(e)})
        return 10
    emit({"event": "version", "version": lib.AsstGetVersion().decode()})
    if not lib.AsstSetUserDir(spec["user_dir"].encode()):
        emit({"event": "error", "stage": "user_dir"})
        return 10
    lib.AsstSetStaticOption(STATIC_CPU_OCR, b"")
    for i, res in enumerate(spec["resources"]):
        if not lib.AsstLoadResource(res.encode()):
            if i == 0:                                  # 本体自带的资源加载失败才算失败；热更新的叠加失败只记一下
                emit({"event": "error", "stage": "resource", "dir": res})
                return 10
            emit({"event": "warning", "stage": "resource", "dir": res})
    emit({"event": "loaded"})

    def on_message(msg, details, _arg):
        try:
            d = json.loads(details.decode("utf-8", "replace")) if details else {}
        except ValueError:
            d = {"raw": details.decode("utf-8", "replace")}
        emit({"msg": int(msg), "details": d})

    cb = CALLBACK(on_message)                           # 要一直引用着，否则会被回收
    handle = lib.AsstCreateEx(cb, None)
    stopped = []

    def on_signal(signum, _frame):
        stopped.append(signum)
        lib.AsstStop(handle)

    try:
        for sig in STOP_SIGNALS:                        # 在 try 里：注册失败也照样 AsstDestroy
            signal.signal(sig, on_signal)
        for key, value in (spec.get("instance_options") or {}).items():
            lib.AsstSetInstanceOption(handle, INSTANCE_OPTION_KEYS[key], str(value).encode())
        if not lib.AsstConnect(handle, spec["adb"].encode(), spec["address"].encode(), b"General"):
            emit({"event": "error", "stage": "connect"})
            return 11
        emit({"event": "connected"})
        params = json.dumps(spec["params"], ensure_ascii=False).encode()
        task_id = lib.AsstAppendTask(handle, spec["task"].encode(), params)
        if task_id <= 0:
            emit({"event": "error", "stage": "append"})
            return 12
        emit({"event": "appended", "task_id": task_id})
        if not lib.AsstStart(handle):
            emit({"event": "error", "stage": "start"})
            return 13
        while lib.AsstRunning(handle):
            time.sleep(0.5)
        emit({"event": "done", "stopped": bool(stopped)})
        return 14 if stopped else 0
    finally:
        lib.AsstDestroy(handle)


if __name__ == "__main__":
    sys.exit(main())
