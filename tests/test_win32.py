"""win32 设备层（crew/win32.py）和配置里的 kind: win32 —— 只用假数据，不碰真窗口。"""
from __future__ import annotations

import pytest

from maapilot.crew import device, win32


def _w(hwnd=1, cw=1280, ch=720, minimized=False, title="明日方舟", visible=True):
    return {"hwnd": hwnd, "pid": 100 + hwnd, "process": "Arknights.exe", "title": title,
            "visible": visible, "minimized": minimized, "window": (cw + 16, ch + 39), "client": (cw, ch)}


def test_pick_window_prefers_usable():
    fake = [_w(1, 160, 28, minimized=True), _w(2, 1280, 720), _w(3, 640, 360)]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(win32, "windows", lambda: fake)
    w = win32.pick_window()
    monkey.undo()
    assert w and w["hwnd"] == 2                                  # 最大且可用的


def test_pick_window_falls_back_to_minimized():
    fake = [_w(1, 0, 0, minimized=True)]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(win32, "windows", lambda: fake)
    w = win32.pick_window()
    monkey.undo()
    assert w and w["hwnd"] == 1                                  # 最小化的也返回，prepare() 负责还原


def test_pick_window_none_when_empty():
    monkey = pytest.MonkeyPatch()
    monkey.setattr(win32, "windows", lambda: [])
    assert win32.pick_window() is None
    monkey.undo()


def test_require_admin_raises_when_not_admin():
    monkey = pytest.MonkeyPatch()
    monkey.setattr(win32, "is_admin", lambda: False)
    with pytest.raises(RuntimeError) as e:
        win32.require_admin()
    monkey.undo()
    assert "管理员" in str(e.value)


def test_prepare_rejects_tiny_window():
    fake = [_w(1, 200, 100)]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(win32, "windows", lambda: fake)
    monkey.setattr(win32, "restore", lambda hwnd: None)
    monkey.setattr(win32, "foreground", lambda hwnd: True)
    with pytest.raises(RuntimeError) as e:
        win32.prepare()
    monkey.undo()
    assert "不像游戏主画面" in str(e.value)


def test_connection_defaults_to_adb():
    assert device.Connection().kind == "adb"
    assert device.Connection(address="127.0.0.1:16384").address == "127.0.0.1:16384"


def test_connection_win32_has_no_address():
    c = device.Connection(kind="win32", window="明日方舟")
    assert c.kind == "win32" and c.address == "" and c.window == "明日方舟"


def test_config_connection_win32(tmp_path, monkeypatch):
    """config 里写 kind: win32 时不要求 emulator.address。"""
    from maapilot import config, paths
    cfg = tmp_path / "config.yaml"
    cfg.write_text("emulator:\n  kind: win32\n  window: 明日方舟\nserver: Official\n", encoding="utf-8")
    monkeypatch.setattr(paths, "CONFIG", cfg)
    c = config.connection()
    assert c.kind == "win32" and c.window == "明日方舟" and c.address == ""
