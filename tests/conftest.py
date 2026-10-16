"""测试不碰真实的 private/：每个测试把 private/ 指到临时目录。"""
import pytest

from maapilot import paths


@pytest.fixture(autouse=True)
def _tmp_private(tmp_path, monkeypatch):
    old = paths.PRIVATE
    paths.set_private(tmp_path / "private")
    monkeypatch.setenv("MAAPILOT_PRIVATE", str(tmp_path / "private"))
    monkeypatch.delenv("MAAPILOT_LOCK_TOKEN", raising=False)
    # 失败取证不碰真的模拟器和本机 MAA 的日志目录（要测的测试自己换掉）
    monkeypatch.setenv("MAAPILOT_MAA_LOG_DIR", str(tmp_path / "no-maa-log"))
    # 用户偏好搬家不读真的 ~/.claude（要测的测试自己指定）
    monkeypatch.setenv("MAAPILOT_CLAUDE_PROJECTS_DIR", str(tmp_path / "no-claude-projects"))
    # Codex 的用户配置不读写真的 ~/.codex；启动检查不把跑测试的会话当成用户的会话
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    # 不碰真的 Windows 任务计划程序：schtasks 指到不存在的程序（Windows 上当作设不了定时；要测的测试自己换掉）
    monkeypatch.setenv("MAAPILOT_SCHTASKS", str(tmp_path / "no-schtasks.exe"))
    for k in ("CODEX_THREAD_ID", "CLAUDECODE", "PYTHONUTF8"):      # PYTHONUTF8：cli.main 会设，别带到后面的测试
        monkeypatch.delenv(k, raising=False)
    # 活动关卡不读本机 maa-cli 缓存里真的活动列表（要测的测试自己指定）
    monkeypatch.setenv("MAAPILOT_MAA_ACTIVITY_FILE", str(tmp_path / "no-maa-activity.json"))
    monkeypatch.setenv("MAAPILOT_MAA_TASKS_FILE", str(tmp_path / "no-maa-tasks.json"))
    from maapilot.cliutil import ENVIRONMENT, CliError
    from maapilot.tools import common

    def _no_device(ocr=False, readonly=False):
        raise CliError("screen_failed", "测试里没有模拟器", exit=ENVIRONMENT)
    monkeypatch.setattr(common, "connect", _no_device)
    # 本机正好有人在用 maa（用户的 agent 在打作业）时，拿锁的测试不该跟着失败（要测的测试自己换掉）
    from maapilot.crew import lock
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    yield tmp_path / "private"
    paths.set_private(old)
