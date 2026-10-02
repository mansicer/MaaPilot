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
    from maapilot.cliutil import ENVIRONMENT, CliError
    from maapilot.tools import common

    def _no_device(ocr=False):
        raise CliError("screen_failed", "测试里没有模拟器", exit=ENVIRONMENT)
    monkeypatch.setattr(common, "connect", _no_device)
    yield tmp_path / "private"
    paths.set_private(old)
