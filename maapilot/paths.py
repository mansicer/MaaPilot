"""安装目录下的位置：游戏数据表、OCR 资源、用户数据 private/ 及其下的日志、截图、快照、模拟器锁。

都作为模块属性在调用时读取（`paths.DATA / ...`），测试可以 monkeypatch。
`MAAPILOT_PRIVATE` 环境变量可以把 private/ 指到别处（测试用临时目录）；private/ 由程序运行时按需创建。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"                        # 游戏数据表（data/*.json，`maapilot data sync`；不入库）
RESOURCE = ROOT / "resource"                # MaaFramework 资源包（OCR 模型在 resource/model/ocr/，不入库）
OCR_MODEL = RESOURCE / "model" / "ocr"      # det.onnx、rec.onnx、keys.txt（从 maa-cli 资源的 PaddleOCR 复制）
TEMPLATES = ROOT / "templates"              # 配置模板、maa-cli 配置模板

PRIVATE = Path(os.environ["MAAPILOT_PRIVATE"]).expanduser() if os.environ.get("MAAPILOT_PRIVATE") else ROOT / "private"
CONFIG = PRIVATE / "config.yaml"
SETUP = PRIVATE / "setup.json"
GOALS = PRIVATE / "goals.md"
GOALS_DONE = PRIVATE / "goals-done.md"
STATE = PRIVATE / "state"                   # 仓库、干员、理智快照
LOGS = PRIVATE / "log"                      # 运行记录、maa-cli 日志
REPORTS = PRIVATE / "reports"               # 每次日常的汇报
SHOTS = PRIVATE / "shots"                   # screen shot 的截图（agent 用读图看）
DEBUG_DIR = PRIVATE / "debug"               # MaaFramework 调试输出、快捷操作失败时的截图
GUARD = PRIVATE / "guard"                   # 货币对账：快照与用户同意的花费
LOCK_FILE = PRIVATE / "lock.json"           # 模拟器锁（maapilot.crew.lock）


def set_private(path: Path) -> None:
    """把 private/ 整体挪到 path（测试用）：重算上面所有 private 下的路径。"""
    global PRIVATE, CONFIG, SETUP, GOALS, GOALS_DONE, STATE, LOGS, REPORTS, SHOTS, DEBUG_DIR, GUARD, LOCK_FILE
    PRIVATE = Path(path)
    CONFIG = PRIVATE / "config.yaml"
    SETUP = PRIVATE / "setup.json"
    GOALS = PRIVATE / "goals.md"
    GOALS_DONE = PRIVATE / "goals-done.md"
    STATE = PRIVATE / "state"
    LOGS = PRIVATE / "log"
    REPORTS = PRIVATE / "reports"
    SHOTS = PRIVATE / "shots"
    DEBUG_DIR = PRIVATE / "debug"
    GUARD = PRIVATE / "guard"
    LOCK_FILE = PRIVATE / "lock.json"
