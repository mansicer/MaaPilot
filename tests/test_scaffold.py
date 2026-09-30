import importlib
import json

import pytest

from maapilot import cli, cliutil


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_package_modules_import():
    for m in ["maapilot.crew.core", "maapilot.crew.lock", "maapilot.crew.maa", "maapilot.crew.cost",
              "maapilot.crew.upgrade", "maapilot.crew.mastery", "maapilot.crew.craft", "maapilot.crew.manufacture",
              "maapilot.crew.shop", "maapilot.crew.infrast_scan", "maapilot.crew.clears", "maapilot.knowledge",
              "maapilot.knowledge.datasync", "maapilot.knowledge.live", "maapilot.config"]:
        importlib.import_module(m)


def test_every_group_registers():
    p = cli.build_parser()
    names = set(p._subparsers._group_actions[0].choices)
    assert {"doctor", "screen", "input", "maa", "state", "data", "calc", "lock", "guard",
            "goals", "daily", "op", "craft", "report"} <= names


def test_envelope_shape_on_error(capsys):
    code, env = run(capsys, "no-such-command")
    assert code == cliutil.BAD_REQUEST
    assert set(env) == {"ok", "command", "data", "error"}
    assert env["ok"] is False and env["error"]["code"] == "bad_arguments"


def test_missing_subcommand(capsys):
    code, env = run(capsys)
    assert code == 2 and env["error"]["code"] == "bad_arguments"


def test_handler_success_and_clierror(capsys, monkeypatch):
    def ok(sub):
        g = sub.add_parser("zz-ok")
        g.set_defaults(func=lambda a: {"x": 1}, command="zz-ok")
        g2 = sub.add_parser("zz-busy")

        def busy(a):
            raise cliutil.CliError("busy", "占用", exit=cliutil.BUSY, data={"holder": "x"})
        g2.set_defaults(func=busy, command="zz-busy")

    real = cli.build_parser

    def build():
        p = real()
        ok(p._subparsers._group_actions[0])
        return p
    monkeypatch.setattr(cli, "build_parser", build)
    code, env = run(capsys, "zz-ok")
    assert code == 0 and env == {"ok": True, "command": "zz-ok", "data": {"x": 1}, "error": None}
    code, env = run(capsys, "zz-busy")
    assert code == 4 and env["data"] == {"holder": "x"} and env["error"]["code"] == "busy"


def test_config_missing_is_environment_error():
    from maapilot import config
    with pytest.raises(cliutil.CliError) as e:
        config.connection()
    assert e.value.exit == cliutil.ENVIRONMENT


def test_lock_child_inherits(monkeypatch):
    from maapilot.crew import lock
    monkeypatch.setattr(lock, "external_maa_pids", lambda: [])
    lk = lock.acquire("parent")
    try:
        env = lk.child_env()
        # 模拟另一个进程：换 pid 后没有 token 就是占用，有 token 就是嵌套锁
        monkeypatch.setattr(lock.os, "getpid", lambda: 999999)
        with pytest.raises(lock.LockBusy):
            lock.acquire("child")
        monkeypatch.setenv(lock.TOKEN_ENV, env[lock.TOKEN_ENV])
        child = lock.acquire("child")
        assert child.nested
        child.release()
        assert lock.current() is not None
    finally:
        monkeypatch.undo()
        lk.release()
    assert lock.current() is None
