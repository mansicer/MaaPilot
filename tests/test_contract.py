"""输出约定：stdout 只有一个 JSON 信封（钩子只有钩子的格式）；启动检查不写数据；配置里的服务器只能是支持的。"""
from __future__ import annotations

import json
import signal

import pytest

from maapilot import cli, cliutil, config
from maapilot.knowledge import shops
from maapilot.tools import doctor


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# --- 服务器 ----------------------------------------------------------------------------------

def _config(tmp_path, server) -> None:
    line = "" if server is None else f"server: {server}\n"
    (tmp_path / "config.yaml").write_text("emulator:\n  address: emu:1\n" + line, encoding="utf-8")


@pytest.mark.parametrize("server", ["Official", "Bilibili", None])
def test_supported_servers_load(tmp_path, server):
    _config(tmp_path, server)
    assert config.load(tmp_path / "config.yaml")["server"] == (server or "Official")


@pytest.mark.parametrize("server,says", [("YoStarEN", "只支持国服"), ("txwy", "只支持国服"), ("official", "认不出"),
                                         ("bilibili", "认不出"), ("CN", "认不出")])
def test_other_servers_are_refused_when_the_config_is_read(tmp_path, server, says):
    _config(tmp_path, server)
    with pytest.raises(cliutil.CliError) as e:
        config.load(tmp_path / "config.yaml")
    assert e.value.code == "unsupported_server" and e.value.exit == cliutil.BAD_REQUEST
    assert says in e.value.message and "Official" in e.value.hint and "Bilibili" in e.value.hint


def test_every_supported_server_has_what_the_code_derives_from_it():
    for srv in config.SUPPORTED_SERVERS:
        assert srv in config.PACKAGES and config.PENGUIN[srv] == "CN" and srv in config.SERVER_NAMES
    assert set(config.SERVER_NAMES) == set(config.PACKAGES)


def test_bilibili_uses_the_official_shop_rules():
    shops.offers.cache_clear()
    assert shops.offers("bilibili") == shops.offers("official") and shops.offers("official")
    assert shops.offers("yostaren") == {}


# --- 信封 ------------------------------------------------------------------------------------

def test_version_flag_wins_over_a_subcommand(capsys, monkeypatch):
    monkeypatch.setattr(doctor, "cmd", lambda args: pytest.fail("带了 --version 不该执行子命令"))
    code, env = run(capsys, "--version", "doctor")
    assert code == 0 and env["command"] == "version" and set(env["data"]) == {"version", "commit", "text"}
    code, env = run(capsys, "--version")
    assert code == 0 and env["command"] == "version"


def test_stopped_by_a_signal_still_prints_an_envelope(capsys, monkeypatch):
    def stopped(args):
        raise cliutil.Stopped(signal.SIGTERM)
    monkeypatch.setattr(doctor, "cmd", stopped)
    code, env = run(capsys, "doctor")
    assert code == 128 + signal.SIGTERM
    assert env["ok"] is False and env["error"]["code"] == "interrupted" and "SIGTERM" in env["error"]["message"]


def test_stopped_is_a_plain_exit_outside_the_cli():
    with pytest.raises(SystemExit) as e:
        raise cliutil.Stopped(signal.SIGINT)
    assert e.value.code == 128 + signal.SIGINT


def test_hook_keeps_its_format_when_the_check_fails(capsys, monkeypatch):
    def boom(probe=None):
        raise RuntimeError("读不了")
    monkeypatch.setattr(doctor, "brief", boom)
    code = cli.main(["doctor", "--hook"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and set(out) == {"hookSpecificOutput"}
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "SessionStart" and "读不了" in ctx["additionalContext"]
    assert "doctor --brief" in ctx["additionalContext"]


def test_brief_writes_nothing():
    from maapilot import paths
    from maapilot.flows import reset
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.SETUP.write_text("{}", encoding="utf-8")                   # 设置好了：启动检查会看用户偏好
    reset.memory_dir().mkdir(parents=True)
    (reset.memory_dir() / "a.md").write_text("x", encoding="utf-8")   # Claude Code 那边有能搬的偏好
    before = sorted(str(p.relative_to(paths.PRIVATE)) for p in paths.PRIVATE.rglob("*"))
    d = doctor.brief()
    assert d["memory"]["importable"] == ["a.md"]
    assert sorted(str(p.relative_to(paths.PRIVATE)) for p in paths.PRIVATE.rglob("*")) == before


def test_upgrade_progress_goes_to_stderr(capsys):
    from maapilot.crew import upgrade
    upgrade._log("[阿米娅] now 精1 Lv50/70")
    cap = capsys.readouterr()
    assert cap.out == "" and "阿米娅" in cap.err
