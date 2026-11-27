"""PC 客户端开跑前的提权检查：低权限下日常只会"跑完什么都没做"，必须拦住。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from maapilot.cliutil import CliError
from maapilot.flows.daily import steps


def _conn(kind: str):
    return SimpleNamespace(kind=kind)


def test_adb_not_checked(monkeypatch):
    monkeypatch.setattr("maapilot.config.connection", lambda: _conn("adb"))
    steps._require_clickable()                      # 不该抛


def test_win32_elevated_passes(monkeypatch):
    from maapilot.crew import win32
    monkeypatch.setattr("maapilot.config.connection", lambda: _conn("win32"))
    monkeypatch.setattr(win32, "is_admin", lambda: True)
    steps._require_clickable()


def test_win32_low_refuses(monkeypatch):
    from maapilot.crew import win32
    monkeypatch.setattr("maapilot.config.connection", lambda: _conn("win32"))
    monkeypatch.setattr(win32, "is_admin", lambda: False)
    monkeypatch.setattr(win32, "token_state", lambda: (True, "Low"))
    with pytest.raises(CliError) as e:
        steps._require_clickable()
    assert e.value.code == "not_elevated"
    assert "低完整性" in e.value.message


def test_win32_not_admin_refuses(monkeypatch):
    from maapilot.crew import win32
    monkeypatch.setattr("maapilot.config.connection", lambda: _conn("win32"))
    monkeypatch.setattr(win32, "is_admin", lambda: False)
    monkeypatch.setattr(win32, "token_state", lambda: (False, "Medium"))
    with pytest.raises(CliError) as e:
        steps._require_clickable()
    assert e.value.code == "not_elevated"
    assert "Medium" in e.value.message
