"""测试不碰真实的 private/：每个测试把 private/ 指到临时目录。"""
import pytest

from maapilot import paths


@pytest.fixture(autouse=True)
def _tmp_private(tmp_path, monkeypatch):
    old = paths.PRIVATE
    paths.set_private(tmp_path / "private")
    monkeypatch.setenv("MAAPILOT_PRIVATE", str(tmp_path / "private"))
    monkeypatch.delenv("MAAPILOT_LOCK_TOKEN", raising=False)
    yield tmp_path / "private"
    paths.set_private(old)
