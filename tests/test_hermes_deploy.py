"""Hermes integration only: no model, network, real profiles or game in these tests."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hermes_deploy", ROOT / "templates/hermes/deploy.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class Native:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.send = (0, {"success": True, "message_id": "fake-1"})
        self.turns = []
        self.probe_faults = {}

    def __call__(self, cfg, profile, *args, **kwargs):
        self.calls.append((profile, args))
        if self.fail == args[:2]:
            self.fail = None
            raise bridge.Refused("simulated interruption")
        home = bridge.profile_home(Path(cfg["root"]), profile)
        stdout = ""
        code = 0
        if args[:3] == ("config", "get", "timezone"):
            stdout = json.dumps("UTC")
        elif args[:3] == ("config", "get", "gateway.multiplex_profiles"):
            stdout = json.dumps(True)
        elif args[:2] == ("profile", "create"):
            home = bridge.profile_home(Path(cfg["root"]), args[2])
            home.mkdir(parents=True)
            (home / "profile.yaml").write_text("description: " + args[-1], encoding="utf-8")
            (home / ".env").write_text("# blank\n", encoding="utf-8")
        elif args[:2] == ("gateway", "ensure"):
            stdout = json.dumps({"state": "ready", "endpoint": {"profile_id": str(home)}})
        elif args[:1] == ("cron",):
            path = home / "cron/jobs.json"
            jobs = bridge.read_json(path)["jobs"] if path.exists() else []
            action = args[1]
            if action == "create":
                opt = {}
                tokens = iter(args[4:])
                for token in tokens:
                    opt[token] = True if token in {"--paused", "--no-agent"} else next(tokens)
                jobs.append({"id": f"j{len(jobs)+1}", "name": opt["--name"], "prompt": args[3],
                             "workdir": opt["--workdir"], "deliver": opt["--deliver"],
                             "failure_deliver": opt["--failure-deliver"], "model": opt.get("--model"),
                             "provider": opt.get("--provider"), "schedule": {"kind": "cron", "expr": args[2]},
                             "script": opt.get("--script"), "no_agent": opt.get("--no-agent", False),
                             "enabled": False, "next_run_at": None})
            elif action == "run":
                job = next(j for j in jobs if j["id"] == args[2])
                if job.get("no_agent"):
                    tree = ast.parse((home / "scripts" / job["script"]).read_text(encoding="utf-8"))
                    argv = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)))
                    spec = bridge.read_json(argv[1])
                else:
                    nonce = re.search(r"--probe ([0-9a-f]{32})", job["prompt"])[1]
                    folder = Path(cfg["root"]) / "maapilot" / cfg["id"] / "relay-probes" / nonce
                    spec = bridge.read_json(folder / "spec.json")
                    bridge.write_json(folder / "result.json", {"exit_code": 0})
                    bridge.write_text(folder / "response.txt", spec["token"])
                    body = self.probe_faults.get("relay", {}).get("answer", spec["token"])
                    archive = (f"**Prompt Characters:** {len(job['prompt'])}\n## Prompt\n\n{job['prompt']}\n\n"
                               f"**Response Characters:** {len(body)}\n## Response\n\n{body}\n")
                    bridge.write_text(home / "cron/output" / job["id"] / "test.md", archive)
                lane = spec["lane"]
                expected = {**cfg["maa_runtime"], "MAA_CONFIG_DIR": str(Path(cfg["workdir"]) / "private/maa-config")}
                probe = {"nonce": spec["nonce"], "ok": True, "environment": expected,
                         "directories": {kind: expected[f"MAA_{kind.upper()}_DIR"] for kind in ("data", "cache", "state", "config")},
                         "resource_exists": True, "elapsed": spec.get("wait_seconds", 0)}
                fault = self.probe_faults.get(lane, {})
                probe.update(fault.get("result", {}))
                bridge.write_json(spec["result"], probe)
                job.update(last_run_at="now", last_status="error" if lane == "failure" else "ok",
                           last_delivery_error=None, last_delivery_unverified=None)
                job.update(fault.get("job", {}))
            elif action in {"pause", "resume", "edit", "remove"}:
                job = next(j for j in jobs if j["id"] == args[2])
                if action == "remove":
                    jobs.remove(job)
                elif action == "pause":
                    job["enabled"] = False
                elif action == "resume":
                    job["enabled"] = True
                    job["next_run_at"] = "2999-01-01T00:00:00+00:00"
                else:
                    opt = dict(zip(args[3::2], args[4::2]))
                    for key, value in opt.items():
                        if key == "--schedule":
                            job["schedule"]["expr"] = value
                            job["next_run_at"] = "2999-01-01T00:00:00+00:00"
                        else:
                            job[key[2:].replace("-", "_")] = value
            bridge.write_json(path, {"jobs": jobs})
        elif args[0] == "chat":
            self.turns.append(args)
            stdout = "Maa agent 追问：选哪个？\n"
        elif args[0] == "send":
            if "--list" in args:
                stdout = json.dumps({"platforms": {"telegram": []}})
            else:
                code, value = self.send
                if isinstance(value, Exception):
                    raise value
                stdout = value if isinstance(value, str) else json.dumps(value)
        return subprocess.CompletedProcess(args, code, stdout, "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    root, work = tmp_path / "hermes", tmp_path / "repo"
    root.mkdir()
    work.mkdir()
    (work / "AGENTS.md").write_text("isolated", encoding="utf-8")
    (root / "SOUL.md").write_text("用户主身份\n", encoding="utf-8")
    (root / "config.yaml").write_text("existing: true\n", encoding="utf-8")
    settings = tmp_path / "settings.json"
    value = {"root": str(root), "workdir": str(work), "profile": "maa", "timezone": "UTC",
             "model": "test", "provider": "custom", "times": ["04:40", "16:40"]}
    bridge.write_json(settings, value)
    args = argparse.Namespace(settings=settings, hermes="fake-hermes", credential_file=None,
                              credential_key=[], replace_schedule=False, enable=False, confirm_exclusive=False)
    fake = Native()
    monkeypatch.setattr(bridge, "native", fake)
    return root, work, settings, args, fake


def installed(env):
    bridge.install(env[3])
    path = next((env[0] / "maapilot").glob("*/deployment.json"))
    return path, bridge.load_manifest(path)


@pytest.mark.parametrize("relative_executable", [False, True])
def test_runtime_exec_uses_project_environment_and_literal_arguments(env, monkeypatch, capfd, relative_executable):
    path, cfg = installed(env)
    capfd.readouterr()
    bin_dir = env[1] / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    (bin_dir / ("python.exe" if os.name == "nt" else "python")).touch()
    monkeypatch.setenv("MAAPILOT_PRIVATE", "wrong-private")
    monkeypatch.setenv("MAA_DATA_DIR", "wrong-data")
    home = os.environ.get("HOME")
    literal = "user text; echo must-not-run $(example)"
    script = ("import json,os,sys; print(json.dumps({'cwd':os.getcwd(), 'args':sys.argv[1:], "
              "'env':{k:os.environ.get(k) for k in ['MAAPILOT_PRIVATE','MAA_DATA_DIR',"
              "'MAA_CACHE_DIR','MAA_STATE_DIR','MAA_CONFIG_DIR','VIRTUAL_ENV','HOME','PATH']}}))")
    executable = "tools/python" if relative_executable else sys.executable
    if relative_executable:
        def resolve(candidate, *, path):
            assert candidate == str(env[1] / "tools/python")
            assert path.split(os.pathsep)[0] == str(bin_dir)
            return sys.executable
        monkeypatch.setattr(bridge.shutil, "which", resolve)
    code = bridge.main(["runtime-exec", "--manifest", str(path), "--", executable, "-c", script, literal])
    assert code == 0
    out = json.loads(capfd.readouterr().out)
    assert Path(out["cwd"]) == env[1]
    assert out["args"] == [literal]
    for key, value in cfg["maa_runtime"].items():
        assert out["env"][key] == value
    assert out["env"]["MAAPILOT_PRIVATE"] == str(env[1] / "private")
    assert out["env"]["MAA_CONFIG_DIR"] == str(env[1] / "private/maa-config")
    assert out["env"]["VIRTUAL_ENV"] == str(env[1] / ".venv")
    assert out["env"]["PATH"].split(os.pathsep)[0] == str(bin_dir)
    assert out["env"]["HOME"] == home
    assert os.environ["MAAPILOT_PRIVATE"] == "wrong-private"
    assert not (env[1] / "private/setup.json").exists()


def test_runtime_exec_preserves_failure_exit_and_does_not_relaunch(env, capfd):
    path, cfg = installed(env)
    bin_dir = env[1] / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    (bin_dir / ("python.exe" if os.name == "nt" else "python")).touch()
    marker = env[1] / "attempts.txt"
    script = "import pathlib,sys; p=pathlib.Path('attempts.txt'); p.open('a').write('once\\n'); sys.exit(7)"
    assert bridge.main(["runtime-exec", "--manifest", str(path), "--", sys.executable, "-c", script]) == 7
    assert marker.read_text() == "once\n"
    assert not env[4].turns


def test_runtime_exec_requires_command_and_project_venv(env):
    path, cfg = installed(env)
    with pytest.raises(bridge.Refused, match="完整命令"):
        bridge.runtime_exec(argparse.Namespace(manifest=path, argv=["--"]))
    with pytest.raises(bridge.Refused, match="虚拟环境"):
        bridge.runtime_exec(argparse.Namespace(manifest=path, argv=["--", "maapilot", "doctor"]))


@pytest.mark.parametrize("output,code", [("null", 0), ("false", 0), ('"true"', 0), ("invalid", 0), ("true", 1)])
def test_install_refuses_implicit_multiplex_before_profile_mutation(env, monkeypatch, output, code):
    original = env[4]
    def native(cfg, profile, *args, **kwargs):
        if args[:3] == ("config", "get", "gateway.multiplex_profiles"):
            return subprocess.CompletedProcess(args, code, output, "")
        return original(cfg, profile, *args, **kwargs)
    monkeypatch.setattr(bridge, "native", native)
    with pytest.raises(bridge.Refused, match="multiplex_profiles"):
        bridge.install(env[3])
    assert original.calls == []
    assert not (env[0] / "profiles/maa").exists()
    assert not list((env[0] / "maapilot").glob("*/deployment.json"))


def test_deployment_keeps_runtime_settings_inside_hermes_profile(env):
    path, cfg = installed(env)
    expected = {f"MAA_{kind.upper()}_DIR": str(env[0] / ".maa-runtime" / kind)
                for kind in ("data", "cache", "state")}
    assert cfg["maa_runtime"] == expected
    assert not (env[1] / "private").exists()
    target = env[0] / "profiles/maa/.env"
    contents = target.read_text(encoding="utf-8")
    for key, value in expected.items():
        assert f"{key}={json.dumps(value, ensure_ascii=False)}" in contents
    assert "MAA_CONFIG_DIR=" in contents
    assert ("maa", ("config", "set", "terminal.env_passthrough",
                    json.dumps(sorted(bridge.RUNTIME_KEYS | {"MAA_CONFIG_DIR"})))) in env[4].calls
    repaired = {key: str(env[0] / "repaired" / key) for key in expected}
    cfg["maa_runtime"] = repaired
    bridge.write_json(path, cfg)
    _, upgraded = installed(env)
    assert upgraded["maa_runtime"] == repaired
    assert not (env[1] / "private").exists()


@pytest.mark.parametrize("script_kind", ["discovery", "probe"])
@pytest.mark.parametrize("layout", ["valid", "relative", "unexpected"])
def test_runtime_queries_effective_log_parent_not_state_env(tmp_path, monkeypatch, capsys, script_kind, layout):
    from maapilot.crew import maa
    from maapilot.oscompat import procs

    data = tmp_path / "data"
    (data / "resource").mkdir(parents=True)
    state = tmp_path / "actual-state"
    log = {"valid": str(state / "debug"), "relative": "relative/debug",
           "unexpected": str(state / "unknown-layout")}[layout]
    paths = {"data": str(data), "cache": str(tmp_path / "cache"),
             "config": str(tmp_path / "config"), "resource": str(data / "resource"), "log": log}
    queried = []

    def query(argv, **kwargs):
        kind = argv[-1]
        queried.append(kind)
        # Model the real 0.7.x CLI: unsupported directory names fail.
        return subprocess.CompletedProcess(argv, 0 if kind in paths else 2, paths.get(kind, ""), "")

    monkeypatch.setattr(maa, "maa_bin", lambda: "fake-maa")
    monkeypatch.setattr(maa, "env", lambda: {})
    monkeypatch.setattr(procs, "run_text", query)
    monkeypatch.setenv("MAA_STATE_DIR", str(tmp_path / "requested-but-not-effective"))
    if script_kind == "discovery":
        tree = ast.parse((ROOT / "templates/hermes/deploy.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "prepare_maa_runtime")
        script = next(n.value.value for n in ast.walk(function) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "script" for t in n.targets))
    else:
        tree = ast.parse((ROOT / "templates/hermes/runtime-probe.py").read_text(encoding="utf-8"))
        script = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "QUERY" for t in n.targets))
    if layout != "valid":
        with pytest.raises(RuntimeError, match="log directory layout"):
            exec(script, {})
    else:
        exec(script, {})
        result = json.loads(capsys.readouterr().out)
        actual = result["MAA_STATE_DIR"] if script_kind == "discovery" else result["directories"]["state"]
        assert actual == str(state)
    assert "state" not in queried
    assert "log" in queried


def test_deployment_rejects_invalid_runtime_before_native_mutation(env):
    path, cfg = installed(env)
    cfg["maa_runtime"] = {"MAA_DATA_DIR": "relative"}
    bridge.write_json(path, cfg)
    env[4].calls.clear()
    with pytest.raises(bridge.Refused, match="运行目录"):
        bridge.install(env[3])
    assert env[4].calls == []


def test_runtime_discovery_reuses_installation_without_project_writes(env, monkeypatch):
    work = env[1]
    python = work / (".venv/Scripts/python.exe" if bridge.os.name == "nt" else ".venv/bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    runtime = {key: str(env[0] / "existing" / key) for key in bridge.RUNTIME_KEYS}
    def discover(argv, **kw):
        assert "bind_runtime" not in argv[2]
        return subprocess.CompletedProcess(argv, 0, json.dumps(runtime), "")
    monkeypatch.setattr(bridge.subprocess, "run", discover)
    assert bridge.prepare_maa_runtime(bridge.read_json(env[2])) == runtime
    assert not (work / "private").exists()


@pytest.mark.parametrize("venv_exists", [True, False])
def test_completed_setup_never_falls_back_to_empty_runtime(env, monkeypatch, venv_exists):
    bridge.write_json(env[1] / "private/setup.json", {"done_at": "2026-10-27T14:00:00+08:00", "version": 1})
    if venv_exists:
        python = env[1] / (".venv/Scripts/python.exe" if bridge.os.name == "nt" else ".venv/bin/python")
        python.parent.mkdir(parents=True)
        python.touch()
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "missing resources"))
    with pytest.raises(bridge.Refused, match="setup|MAA"):
        bridge.install(env[3])
    assert env[4].calls == []


def test_native_notifications_do_not_depend_on_daily_done_or_sender_credentials(env):
    value = bridge.read_json(env[2])
    value["target"] = "telegram:test-chat"
    bridge.write_json(env[2], value)
    path, cfg = installed(env)
    for job in bridge.jobs(cfg):
        assert job["deliver"] == job["failure_deliver"] == value["target"]
        assert "send-report --manifest" not in job["prompt"]
    bridge.verify(argparse.Namespace(manifest=path, delivery=True))
    assert not (env[1] / "private/reports").exists()
    evidence = bridge.load_manifest(path)["verification"]
    assert all(evidence[key]["ok"] for key in ("runtime", "success", "failure"))
    assert len(bridge.jobs(cfg)) == 2  # Only our three harmless probes were removed.
    assert not list((env[0] / "profiles/maa/scripts").glob("maapilot-probe-*.py"))
    assert not any(a[0] == "send" for _, a in env[4].calls)


@pytest.mark.parametrize("lane,fault", [
    ("runtime", {"result": {"environment": {"HOME": "wrong"}}}),
    ("runtime", {"result": {"resource_exists": False}}),
    ("success", {"job": {"last_delivery_error": "not configured"}}),
    ("success", {"job": {"last_delivery_queued": ["pending"]}}),
    ("failure", {"job": {"last_delivery_unverified": ["telegram:test-chat"]}}),
])
def test_failed_or_queued_native_probe_never_enables_delivery(env, lane, fault):
    value = bridge.read_json(env[2])
    value["target"] = "telegram:test-chat"
    bridge.write_json(env[2], value)
    path, cfg = installed(env)
    env[4].probe_faults[lane] = fault
    with pytest.raises(bridge.Refused):
        bridge.verify(argparse.Namespace(manifest=path, delivery=True))
    assert not bridge.load_manifest(path)["verification"].get(lane, {}).get("ok")
    assert all(not job["enabled"] for job in bridge.jobs(cfg))
    assert len(bridge.jobs(cfg)) == 2


def test_profile_environment_change_invalidates_native_verification(env):
    path, cfg = installed(env)
    bridge.verify(argparse.Namespace(manifest=path, delivery=False))
    before = bridge.verification_fingerprint(cfg)
    cron = env[0] / "cron/jobs.json"
    records = bridge.read_json(cron)
    for job in records["jobs"]:
        job["enabled"] = True
    bridge.write_json(cron, records)
    target = env[0] / "profiles/maa/.env"
    target.write_text(target.read_text(encoding="utf-8") + "OTHER_TOOL_SETTING=changed\n", encoding="utf-8")
    assert bridge.verification_fingerprint(cfg) != before
    bridge.install(env[3])
    assert all(not job["enabled"] for job in bridge.jobs(cfg))


def test_legacy_local_delivery_upgrade_pauses_only_owned_jobs(env):
    value = bridge.read_json(env[2])
    value["target"] = "telegram:test-chat"
    bridge.write_json(env[2], value)
    _, cfg = installed(env)
    cron = env[0] / "cron/jobs.json"
    records = bridge.read_json(cron)
    for job in records["jobs"]:
        job.update(enabled=True, deliver="local", failure_deliver="local")
    bridge.write_json(cron, records)
    bridge.install(env[3])
    assert all(not job["enabled"] and job["deliver"] == value["target"] for job in bridge.jobs(cfg))


def test_probe_cleanup_failure_is_not_recorded_as_verified(env, monkeypatch):
    path, cfg = installed(env)
    original = env[4]
    def native(cfg, profile, *args, **kwargs):
        if args[:2] == ("cron", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "simulated cleanup failure")
        return original(cfg, profile, *args, **kwargs)
    monkeypatch.setattr(bridge, "native", native)
    with pytest.raises(bridge.Refused, match="删除"):
        bridge.verify(argparse.Namespace(manifest=path, delivery=False))
    assert not bridge.load_manifest(path)["verification"].get("runtime")
    assert all(not job["enabled"] for job in bridge.jobs(cfg))


def test_redeploy_preserves_active_job_and_main_config(env):
    path, cfg = installed(env)
    cron = env[0] / "cron/jobs.json"
    content = bridge.read_json(cron)
    for job in content["jobs"]:
        job.update(enabled=True, next_run_at="2999-01-01T00:00:00+00:00")
    bridge.write_json(cron, content)
    before = cron.read_bytes()
    soul = (env[0] / "SOUL.md").read_bytes()
    bridge.install(env[3])
    assert cron.read_bytes() == before
    assert (env[0] / "SOUL.md").read_bytes() == soul
    assert (env[0] / "config.yaml").read_text() == "existing: true\n"
    assert len(bridge.jobs(cfg)) == 2
    assert (env[0] / "profiles/maa/.env").read_text().startswith("# blank\n")


@pytest.mark.parametrize("where", [("config", "set"), ("skills", "trust"), ("cron", "create")])
def test_resume_after_partial_deployment(env, where):
    env[4].fail = where
    with pytest.raises(bridge.Refused):
        bridge.install(env[3])
    _, cfg = installed(env)
    assert len(bridge.jobs(cfg)) == 2
    assert sum(a[:2] == ("profile", "create") for _, a in env[4].calls) == 1


def test_recover_after_native_profile_created_before_owner_write(env):
    # Simulate kill in native create AFTER atomic profile publication.
    fake = env[4]
    original = fake.__call__
    def interrupted(cfg, profile, *args, **kwargs):
        result = original(cfg, profile, *args, **kwargs)
        if args[:3] == ("config", "get", "timezone"):
            stdout = json.dumps("UTC")
        elif args[:2] == ("profile", "create"):
            raise bridge.Refused("killed")
        return result
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bridge, "native", interrupted)
        with pytest.raises(bridge.Refused):
            bridge.install(env[3])
    _, cfg = installed(env)
    assert len(bridge.jobs(cfg)) == 2


def test_refuse_unrelated_profile_even_with_same_name(env):
    home = env[0] / "profiles/maa"
    home.mkdir(parents=True)
    (home / "SOUL.md").write_text("unrelated")
    with pytest.raises(bridge.Refused, match="非受管"):
        bridge.install(env[3])
    assert (home / "SOUL.md").read_text() == "unrelated"
    assert env[4].calls == []


def test_multiple_deployments_preserve_routes_and_existing_skill(env):
    unrelated = env[0] / "skills/unrelated/SKILL.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("untouched")
    path, cfg = installed(env)
    value = bridge.read_json(env[2])
    value["profile"] = "maa-two"
    bridge.write_json(env[2], value)
    bridge.install(env[3])
    text = (env[0] / "SOUL.md").read_text(encoding="utf-8")
    assert text.startswith("用户主身份\n")
    assert text.count(":begin -->") == 2
    assert len(list((env[0] / "skills").glob("maapilot-dispatch-*"))) == 2
    assert unrelated.read_text() == "untouched"


def test_route_preserves_main_soul_original_bytes(env):
    original = "用户主身份\r\n自定义第二行\r\n".encode("utf-8")
    (env[0] / "SOUL.md").write_bytes(original)
    installed(env)
    assert (env[0] / "SOUL.md").read_bytes().startswith(original)


def test_template_upgrade_keeps_enabled_and_next_run(env):
    _, cfg = installed(env)
    cron = env[0] / "cron/jobs.json"
    content = bridge.read_json(cron)
    for job in content["jobs"]:
        job.update(prompt="previous template", enabled=True, next_run_at="2999-01-01T00:00:00+00:00")
    bridge.write_json(cron, content)
    count = len(env[4].calls)
    bridge.install(env[3])
    calls = env[4].calls[count:]
    assert not any(a[:2] in {("cron", "pause"), ("cron", "resume")} for _, a in calls)
    assert all(j["enabled"] and j["next_run_at"] == "2999-01-01T00:00:00+00:00" for j in bridge.jobs(cfg))
    assert all(j["prompt"] != "previous template" for j in bridge.jobs(cfg))


def test_changed_schedule_requires_flag_and_pauses_only_owned(env):
    _, cfg = installed(env)
    value = bridge.read_json(env[2])
    value["times"] = ["05:40"]
    bridge.write_json(env[2], value)
    with pytest.raises(bridge.Refused, match="replace-schedule"):
        bridge.install(env[3])
    env[3].replace_schedule = True
    bridge.install(env[3])
    assert len(bridge.jobs(cfg)) == 1
    assert bridge.jobs(cfg)[0]["enabled"] is False


def test_removing_base_url_uses_native_unset(env):
    value = bridge.read_json(env[2])
    value["base_url"] = "https://example.invalid/v1"
    bridge.write_json(env[2], value)
    installed(env)
    del value["base_url"]
    bridge.write_json(env[2], value)
    env[3].replace_schedule = True
    bridge.install(env[3])
    assert ("maa", ("config", "unset", "model.base_url")) in env[4].calls


def test_only_explicit_credential_keys_copied(env, tmp_path):
    source = tmp_path / "model.env"
    source.write_text("OPENCODE_GO_API_KEY='fake-key'\nTELEGRAM_BOT_TOKEN='do-not-copy'\n")
    env[3].credential_file = source
    env[3].credential_key = ["OPENCODE_GO_API_KEY"]
    installed(env)
    content = (env[0] / "profiles/maa/.env").read_text()
    assert "fake-key" in content and "TELEGRAM" not in content
    env[3].credential_key = ["TELEGRAM_BOT_TOKEN"]
    with pytest.raises(bridge.Refused, match="模型 API"):
        bridge.install(env[3])


@pytest.mark.parametrize("target", ["telegram", "origin", "local", "telegram:", "telegram:has space", "bot-chat:default"])
def test_reject_ambiguous_report_targets(target):
    with pytest.raises(bridge.Refused):
        bridge.exact_target(target)


def test_dispatch_is_source_scoped_and_continues_with_literal_user_text(env, tmp_path, capsys):
    path, _ = installed(env)
    source, request = tmp_path / "source.json", tmp_path / "request.txt"
    info = {"platform": "cli", "account": "test", "chat_id": "conversation-a", "sender_id": "user-a"}
    bridge.write_json(source, info)
    request.write_text("合成材料？ $(danger) `command` 中文", encoding="utf-8")
    args = argparse.Namespace(manifest=path, source_file=source, request_file=request)
    bridge.dispatch(args)
    first = env[4].turns[-1]
    request.write_text("选第二个", encoding="utf-8")
    bridge.dispatch(args)
    second = env[4].turns[-1]
    assert first[first.index("--continue") + 1] == second[second.index("--continue") + 1]
    info["chat_id"] = "conversation-b"
    bridge.write_json(source, info)
    bridge.dispatch(args)
    third = env[4].turns[-1]
    assert third[third.index("--continue") + 1] != first[first.index("--continue") + 1]
    assert all("--oneshot" in t and "--create-if-missing" in t for t in env[4].turns)
    assert "$(danger)" in Path(first[first.index("--query-file") + 1]).read_text(encoding="utf-8")
    assert "Maa agent 追问" in capsys.readouterr().out


def report_ready(env, rid="20261027-0440"):
    value = bridge.read_json(env[2])
    value["target"] = "telegram:test-chat"
    bridge.write_json(env[2], value)
    path, cfg = installed(env)
    report = env[1] / "private/reports/2026-10-27-0440.json"
    bridge.write_json(report, {"run_id": rid, "open": False, "closed_by": "agent", "closed_at": "now", "goals_summary": {"text": "测试简报"}})
    report.with_suffix(".md").write_text("测试报告", encoding="utf-8")
    args = argparse.Namespace(manifest=path, run_id=rid, retry_failed=False)
    return path, cfg, args


def test_send_success_deduplicates_per_run_and_selects_main_profile(env):
    path, cfg, args = report_ready(env)
    bridge.send_report(args)
    bridge.send_report(args)
    calls = [(p, a) for p, a in env[4].calls if a[0] == "send"]
    assert len(calls) == 1 and calls[0][0] == "default"
    assert calls[0][1][1:3] == ("--to", "telegram:test-chat")
    receipt = bridge.read_json(path.parent / "outbox" / args.run_id / "receipt.json")
    assert receipt["state"] == "sent"


def test_summary_extraction_and_manual_recovery_never_send_full_program_report(env):
    path, cfg, args = report_ready(env)
    report = env[1] / "private/reports/2026-10-27-0440.md"
    report.write_text("内部程序报告：领奖励留给 agent 做", encoding="utf-8")
    assert bridge.report_summary(cfg, args.run_id) == "测试简报"
    bridge.send_report(args)
    assert (path.parent / "outbox" / args.run_id / "report.md").read_text(encoding="utf-8") == "测试简报"
    report.unlink()
    assert bridge.report_summary(cfg, args.run_id) == "测试简报"


@pytest.mark.parametrize("code,payload,state", [
    (0, {"skipped": True}, "failed"),
    (0, {"success": True, "skipped": True}, "unknown"),
    (0, {"success": True, "error": "partial"}, "unknown"),
    (1, {"success": True}, "unknown"),
    (1, {"error": "not configured"}, "unknown"),
    (0, "broken JSON", "unknown"),
    (1, subprocess.TimeoutExpired("send", 120), "unknown"),
])
def test_send_requires_both_success_and_exit_zero_no_unknown_retry(env, code, payload, state):
    path, _, args = report_ready(env)
    env[4].send = code, payload
    with pytest.raises(bridge.Refused):
        bridge.send_report(args)
    receipt = path.parent / "outbox" / args.run_id / "receipt.json"
    assert bridge.read_json(receipt)["state"] == state
    args.retry_failed = state == "unknown"
    with pytest.raises(bridge.Refused):
        bridge.send_report(args)
    assert sum(a[0] == "send" for _, a in env[4].calls) == 1


def test_send_interrupted_claim_unknown_and_never_retried(env):
    path, _, args = report_ready(env)
    receipt = path.parent / "outbox" / args.run_id / "receipt.json"
    bridge.write_json(receipt, {"state": "sending"})
    args.retry_failed = True
    with pytest.raises(bridge.Refused, match="不明"):
        bridge.send_report(args)
    assert bridge.read_json(receipt)["state"] == "unknown"
    assert not any(a[0] == "send" for _, a in env[4].calls)


def test_send_refuses_unfinished_daily(env):
    path, _, args = report_ready(env)
    report = env[1] / "private/reports/2026-10-27-0440.json"
    value = bridge.read_json(report)
    value["open"] = True
    bridge.write_json(report, value)
    with pytest.raises(bridge.Refused, match="daily done"):
        bridge.send_report(args)
    assert not any(a[0] == "send" for _, a in env[4].calls)


def test_send_refuses_unconfigured_channel(env):
    path, _ = installed(env)
    with pytest.raises(bridge.Refused, match="待配置"):
        bridge.send_report(argparse.Namespace(manifest=path, run_id="20261027-0440", retry_failed=False))


def test_enable_refuses_without_setup_confirmation_and_target(env):
    path, cfg = installed(env)
    with pytest.raises(bridge.Refused, match="confirm-exclusive"):
        bridge.enable(cfg, path, False)
    cfg["target"] = "telegram:test-chat"
    bridge.write_json(path, cfg)
    with pytest.raises(bridge.Refused, match="setup"):
        bridge.enable(cfg, path, True)


def test_native_pins_root_profile_cwd_and_timezone(env, monkeypatch):
    _, cfg = installed(env)
    monkeypatch.undo()
    calls = []
    def run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, "ok", "")
    monkeypatch.setattr(bridge.subprocess, "run", run)
    monkeypatch.setenv("HERMES_HOME", str(env[0] / "profiles/maa"))
    bridge.native(cfg, "default", "send", "--to", "telegram:test-chat", "--file", "report.md", "--json")
    argv, kw = calls[0]
    assert argv[1:3] == ["--profile", "default"]
    assert kw["env"]["HERMES_HOME"] == str(env[0])
    assert kw["env"]["HERMES_TIMEZONE"] == "UTC"
    assert kw["cwd"] == str(env[1])
    for key in cfg["maa_runtime"]:
        monkeypatch.delenv(key, raising=False)
    for tool_home in (env[0] / "home", env[0] / "profiles/maa/home"):
        monkeypatch.setenv("HOME", str(tool_home))
        bridge.native(cfg, "maa", "chat", "--oneshot")
        child = calls[-1][1]["env"]
        assert child["HOME"] == str(tool_home)
        assert {key: child[key] for key in bridge.RUNTIME_KEYS} == cfg["maa_runtime"]
        bridge.native(cfg, "default", "send", "--list")
        assert not (bridge.RUNTIME_KEYS & calls[-1][1]["env"].keys())


def test_daily_prompt_waits_through_process_manage_timeout():
    prompt = (ROOT / "templates/hermes/daily-prompt.txt").read_text(encoding="utf-8")
    harness = (ROOT / "docs/harnesses/hermes.md").read_text(encoding="utf-8")
    assert "timeout 只表示等待窗口结束" in prompt
    assert "status=exited" in prompt
    assert "空闲看门狗" in harness
    assert "空闲看门狗" in harness
    assert "不是整轮总时长" in harness


def test_enable_refuses_missing_job_after_setup(env, monkeypatch):
    path, cfg = installed(env)
    cfg["target"] = "telegram:test-chat"
    bridge.write_json(path, cfg)
    bridge.write_json(env[1] / "private/setup.json", {"done_at": "2026-10-27T14:00:00+08:00", "version": 1})
    python = env[1] / (".venv/Scripts/python.exe" if bridge.os.name == "nt" else ".venv/bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, json.dumps(cfg["times"]), ""))
    bridge.write_json(env[0] / "cron/jobs.json", {"jobs": []})
    with pytest.raises(bridge.Refused, match="cron 缺失"):
        bridge.enable(cfg, path, True)
    assert "exclusive_confirmed_at" not in bridge.read_json(path)


def test_enable_reanchors_overdue_job_before_resume(env, monkeypatch):
    value = bridge.read_json(env[2])
    value["target"] = "telegram:test-chat"
    bridge.write_json(env[2], value)
    path, cfg = installed(env)
    bridge.write_json(env[1] / "private/setup.json", {"done_at": "2026-10-27T14:00:00+08:00", "version": 1})
    python = env[1] / (".venv/Scripts/python.exe" if bridge.os.name == "nt" else ".venv/bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, json.dumps(cfg["times"]), ""))
    cron = env[0] / "cron/jobs.json"
    content = bridge.read_json(cron)
    for job in content["jobs"]:
        job["next_run_at"] = "2000-01-01T00:00:00+00:00"
    bridge.write_json(cron, content)
    bridge.verify(argparse.Namespace(manifest=path, delivery=True, relay=True))
    cfg = bridge.load_manifest(path)
    bridge.enable(cfg, path, True)
    calls = [a for p, a in env[4].calls if p == bridge.scheduler_profile(cfg)
             and a[:2] in {("cron", "resume"), ("cron", "edit")}
             and a[2] in cfg["jobs"].values()]
    assert len(calls) == 4
    assert calls[0][1] == "edit" and calls[1][1] == "resume"
    assert all(j["next_run_at"].startswith("2999") for j in bridge.jobs(cfg))


def test_enable_refuses_unconfigured_send_platform(env, monkeypatch):
    value = bridge.read_json(env[2])
    value["target"] = "discord:test-chat"
    bridge.write_json(env[2], value)
    path, cfg = installed(env)
    bridge.write_json(env[1] / "private/setup.json", {"done_at": "2026-10-27T14:00:00+08:00", "version": 1})
    python = env[1] / (".venv/Scripts/python.exe" if bridge.os.name == "nt" else ".venv/bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, json.dumps(cfg["times"]), ""))
    with pytest.raises(bridge.Refused, match="verify"):
        bridge.enable(cfg, path, True)
    assert not any(a[:2] == ("cron", "resume") for _, a in env[4].calls)


def test_run_receipts_are_isolated(env):
    path, _, args = report_ready(env)
    bridge.send_report(args)
    rid = "20261027-1640"
    report = env[1] / "private/reports/2026-10-27-1640.json"
    bridge.write_json(report, {"run_id": rid, "open": False, "closed_by": "agent", "closed_at": "now", "goals_summary": {"text": "测试简报"}})
    report.with_suffix(".md").write_text("第二次测试报告", encoding="utf-8")
    args.run_id = rid
    bridge.send_report(args)
    assert sum(a[0] == "send" for _, a in env[4].calls) == 2
    assert len(list((path.parent / "outbox").glob("*/receipt.json"))) == 2


def test_main_profile_owns_relay_and_both_delivery_lanes(env):
    path, cfg = installed(env)
    assert bridge.scheduler_profile(cfg) == "default"
    assert not bridge.jobs(cfg, "maa")
    for job in bridge.jobs(cfg):
        assert "run-daily" in job["prompt"]
        assert "status=exited" in job["prompt"]
        assert job["model"] is None and job["provider"] is None
    skill = next((env[0] / "skills").glob("maapilot-dispatch-*/SKILL.md")).read_text(encoding="utf-8")
    soul = (env[0] / "profiles/maa/SOUL.md").read_text(encoding="utf-8")
    assert "相同来源" in skill and "message_agent" in skill and "timeout" in skill
    assert "最终答复就是 CLI 返回通路" in soul
    assert "不要在 Maa 内等待用户直接回答" in soul
    assert "不接续任何交互来源" in soul


def legacy_deployment(env):
    path, cfg = installed(env)
    records = bridge.read_json(env[0] / "cron/jobs.json")
    for job in records["jobs"]:
        job.update(enabled=True, model=cfg["model"], provider=cfg["provider"], prompt="legacy daily")
    records["jobs"].append({"id": "unrelated", "name": "other user's job", "enabled": True})
    bridge.write_json(env[0] / "profiles/maa/cron/jobs.json", records)
    bridge.write_json(env[0] / "cron/jobs.json", {"jobs": []})
    del cfg["scheduler_profile"]
    bridge.write_json(path, cfg)
    return path, cfg


def test_upgrade_moves_cron_to_sender_without_rearming_old_jobs(env):
    path, old = legacy_deployment(env)
    _, cfg = installed(env)
    assert bridge.scheduler_profile(cfg) == "default"
    assert len(cfg["retired_jobs"]) == 2
    assert all(not j["enabled"] for j in bridge.jobs(cfg))
    old_jobs = bridge.jobs(cfg, "maa")
    assert all(not j["enabled"] for j in old_jobs if j["id"] != "unrelated")
    assert next(j for j in old_jobs if j["id"] == "unrelated")["enabled"]
    assert "run-daily" in bridge.jobs(cfg)[0]["prompt"]
    assert cfg["verification"] == {}
    installed(env)
    assert len(bridge.jobs(cfg)) == 2
    assert len(bridge.load_manifest(path)["retired_jobs"]) == 2


def test_migration_interruption_recovers_without_duplicate_schedules(env):
    path, _ = legacy_deployment(env)
    env[4].fail = ("cron", "create")
    with pytest.raises(bridge.Refused):
        installed(env)
    _, cfg = installed(env)
    assert len(bridge.jobs(cfg)) == 2
    assert len(cfg["retired_jobs"]) == 2
    assert all(not j["enabled"] for j in bridge.jobs(cfg, "maa") if j["id"] != "unrelated")


def test_migration_blocks_inflight_daily_and_retired_reactivation(env):
    path, _ = legacy_deployment(env)
    cron = env[0] / "profiles/maa/cron/jobs.json"
    records = bridge.read_json(cron)
    records["jobs"][0]["fire_claim"] = {"owner": "still-running"}
    bridge.write_json(cron, records)
    with pytest.raises(bridge.Refused, match="仍在运行"):
        installed(env)
    assert not bridge.read_json(env[0] / "cron/jobs.json")["jobs"]
    records["jobs"][0].pop("fire_claim")
    bridge.write_json(cron, records)
    _, cfg = installed(env)
    records = bridge.read_json(cron)
    records["jobs"][0]["enabled"] = True
    bridge.write_json(cron, records)
    with pytest.raises(bridge.Refused, match="双重调度"):
        bridge.check_retired_jobs(cfg)


@pytest.mark.parametrize("code,body", [(0, "日常成功：测试简报"), (0, "部分失败：未收尾，等待修复")])
def test_run_daily_returns_success_or_failure_summary_without_done(env, monkeypatch, capsys, code, body):
    path, cfg = installed(env)
    bridge.write_json(env[1] / "private/setup.json", {"done_at": "test", "version": 1})
    capsys.readouterr()
    calls = []
    def chat(cfg, profile, *args, **kwargs):
        calls.append((profile, args, kwargs))
        return subprocess.CompletedProcess(args, code, body, "")
    monkeypatch.setattr(bridge, "native", chat)
    bridge.run_daily(argparse.Namespace(manifest=path))
    assert capsys.readouterr().out.strip() == body
    profile, args, kwargs = calls[0]
    assert profile == "maa" and "--continue" not in args
    assert "--oneshot" in args and kwargs["timeout"] is None
    assert "无人值守" in Path(args[args.index("--query-file") + 1]).read_text(encoding="utf-8")
    assert not (env[1] / "private/reports").exists()
    assert len(list((path.parent / "daily-requests").glob("*/result.json"))) == 1


@pytest.mark.parametrize("outcome", ["nonzero", "empty", "launch"])
def test_run_daily_failure_is_visible_without_retry_or_secret_output(env, monkeypatch, capsys, outcome):
    path, cfg = installed(env)
    bridge.write_json(env[1] / "private/setup.json", {"done_at": "test", "version": 1})
    capsys.readouterr()
    calls = []
    def chat(*a, **kw):
        calls.append(a)
        if outcome == "launch":
            raise OSError("secret-provider-details")
        return subprocess.CompletedProcess(a, 7 if outcome == "nonzero" else 0,
                                           "secret-provider-details" if outcome == "nonzero" else "", "secret")
    monkeypatch.setattr(bridge, "native", chat)
    assert bridge.main(["run-daily", "--manifest", str(path)]) == 1
    output = capsys.readouterr()
    assert "失败" in output.err or "未成功返回" in output.err
    assert "secret" not in output.err + output.out
    assert len(calls) == 1


def test_selected_sender_probe_and_fingerprint_cover_main_configuration(env):
    value = bridge.read_json(env[2])
    value["target"] = "telegram:test-chat"
    bridge.write_json(env[2], value)
    path, cfg = installed(env)
    env[4].calls.clear()
    bridge.verify(argparse.Namespace(manifest=path, delivery=True))
    creates = [(p, a) for p, a in env[4].calls if a[:2] == ("cron", "create")]
    assert [p for p, a in creates] == ["maa", "default", "default"]
    fingerprint = bridge.verification_fingerprint(cfg)
    (env[0] / "config.yaml").write_text("timezone: UTC\nchanged: true\n")
    assert bridge.verification_fingerprint(cfg) != fingerprint


def test_sender_timezone_mismatch_refuses_without_changing_main_config(env, monkeypatch):
    original = env[4]
    def native(cfg, profile, *args, **kwargs):
        if args[:3] == ("config", "get", "timezone"):
            return subprocess.CompletedProcess(args, 0, '"Asia/Shanghai"', "")
        return original(cfg, profile, *args, **kwargs)
    monkeypatch.setattr(bridge, "native", native)
    with pytest.raises(bridge.Refused, match="timezone"):
        installed(env)
    assert not (env[0] / "cron/jobs.json").exists()
    assert (env[0] / "config.yaml").read_text() == "existing: true\n"


def test_direct_maa_delivery_remains_available_without_main_cron(env):
    value = bridge.read_json(env[2])
    value['send_profile'] = 'maa'
    bridge.write_json(env[2], value)
    path, cfg = installed(env)
    assert bridge.scheduler_profile(cfg) == 'maa'
    assert not bridge.jobs(cfg, 'default')
    for job in bridge.jobs(cfg):
        assert 'run-daily' not in job['prompt']
        assert job['model'] == 'test' and job['provider'] == 'custom'


def test_run_daily_rejects_concurrent_call_before_starting_child(env):
    path, cfg = installed(env)
    bridge.write_json(env[1] / "private/setup.json", {"done_at": "test", "version": 1})
    count = len(env[4].turns)
    with bridge.lock(path.parent / '.daily.lock'):
        with pytest.raises(bridge.Refused, match='正在执行'):
            bridge.run_daily(argparse.Namespace(manifest=path))
    assert len(env[4].turns) == count


def test_verify_and_install_share_lock_and_preserve_latest_manifest(env, monkeypatch):
    path, cfg = installed(env)
    original = bridge.run_probe
    def probe(cfg, manifest, lane):
        before = Path(manifest).read_bytes()
        with pytest.raises(bridge.Refused, match='正在执行'):
            bridge.install(env[3])
        assert Path(manifest).read_bytes() == before
        return original(cfg, manifest, lane)
    monkeypatch.setattr(bridge, 'run_probe', probe)
    bridge.verify(argparse.Namespace(manifest=path, delivery=False))
    before = path.read_bytes()
    with bridge.lock(path.parent.parent / '.install.lock'):
        with pytest.raises(bridge.Refused, match='正在执行'):
            bridge.verify(argparse.Namespace(manifest=path, delivery=False))
        with pytest.raises(bridge.Refused, match='正在执行'):
            bridge.enable(cfg, path, True)
    assert path.read_bytes() == before


def test_verify_relay_requires_both_agents_tool_wait_and_final_reply(env):
    path, cfg = installed(env)
    bridge.verify(argparse.Namespace(manifest=path, delivery=False, relay=True))
    proof = bridge.load_manifest(path)['verification']['relay']
    assert proof['ok'] and proof['wait_seconds'] == 190
    creates = [(p, a) for p, a in env[4].calls if a[:2] == ('cron', 'create') and a[a.index('--name') + 1].startswith('MaaPilot-relay-probe:')]
    assert len(creates) == 1 and creates[0][0] == 'default'
    assert '--no-agent' not in creates[0][1]
    spec = bridge.read_json(path.parent / 'relay-probes' / proof['nonce'] / 'spec.json')
    assert spec['active'] is False
    assert len(bridge.jobs(cfg)) == 2
    # Independent environment-only rechecks must not erase valid relay proof.
    bridge.verify(argparse.Namespace(manifest=path, delivery=False))
    assert bridge.load_manifest(path)['verification']['relay'] == proof


@pytest.mark.parametrize('fault', [
    {'answer': 'I started it; wait for a later turn'},
    {'result': {'elapsed': 1}},
    {'result': {'environment': {}}},
    {'job': {'last_status': 'error'}},
])
def test_relay_probe_rejects_early_return_wrong_environment_and_failed_parent(env, fault):
    path, cfg = installed(env)
    env[4].probe_faults['relay'] = fault
    with pytest.raises(bridge.Refused):
        bridge.verify(argparse.Namespace(manifest=path, delivery=False, relay=True))
    assert not bridge.load_manifest(path)['verification'].get('relay')
    assert len(bridge.jobs(cfg)) == 2


def test_response_parser_never_accepts_token_from_prompt():
    prompt, answer = 'MAAPILOT_RELAY_OK_test', 'not executed'
    archive = (f'**Prompt Characters:** {len(prompt)}\n## Prompt\n\n{prompt}\n\n'
               f'**Response Characters:** {len(answer)}\n## Response\n\n{answer}\n')
    assert bridge.cron_response(archive) == answer
    with pytest.raises(bridge.Refused):
        bridge.cron_response(archive[:-3])
    with pytest.raises(bridge.Refused):
        bridge.cron_response(prompt)


def test_daily_setup_missing_reports_blocker_without_starting_agent(env):
    path, cfg = installed(env)
    with pytest.raises(bridge.Refused, match='setup'):
        bridge.run_daily(argparse.Namespace(manifest=path))
    assert not env[4].turns
    assert not (env[1] / 'private/setup.json').exists()


def test_daily_interruption_persists_unknown_until_explicit_reconciliation(env, monkeypatch, capsys):
    path, cfg = installed(env)
    bridge.write_json(env[1] / 'private/setup.json', {'done_at': 'test', 'version': 1})
    def interrupted(*a, **kw):
        raise KeyboardInterrupt()
    monkeypatch.setattr(bridge, 'native', interrupted)
    with pytest.raises(KeyboardInterrupt):
        bridge.run_daily(argparse.Namespace(manifest=path))
    assert bridge.read_json(path.parent / 'daily-active.json')['state'] == 'unknown'
    with pytest.raises(bridge.Refused, match='状态未确认'):
        bridge.run_daily(argparse.Namespace(manifest=path))
    capsys.readouterr()
    bridge.daily_status(argparse.Namespace(manifest=path))
    assert json.loads(capsys.readouterr().out)['state'] == 'unknown'
    with pytest.raises(bridge.Refused, match='人工核对'):
        bridge.resolve_daily(argparse.Namespace(manifest=path, confirm_stopped=False))
    bridge.resolve_daily(argparse.Namespace(manifest=path, confirm_stopped=True))
    assert bridge.read_json(path.parent / 'daily-active.json')['state'] == 'resolved'


def test_stale_running_record_is_not_treated_as_stopped_or_replayed(env, capsys):
    path, cfg = installed(env)
    bridge.write_json(env[1] / 'private/setup.json', {'done_at': 'test', 'version': 1})
    bridge.write_json(path.parent / 'daily-active.json', {'state': 'running', 'child_pid': 999999})
    capsys.readouterr()
    bridge.daily_status(argparse.Namespace(manifest=path))
    assert json.loads(capsys.readouterr().out)['state'] == 'unknown'
    with pytest.raises(bridge.Refused, match='状态未确认'):
        bridge.run_daily(argparse.Namespace(manifest=path))
    assert not env[4].turns


@pytest.mark.parametrize('stderr,category,summary', [
    ('401 Unauthorized Bearer sk-secret', 'authentication', '认证'),
    ('429 too many requests sk-secret', 'rate_limit', '限流'),
    ('Connection refused https://secret.invalid', 'network', '网络'),
    ('Permission denied /private/secret', 'permission', '权限'),
    ('ModuleNotFoundError: missing secret', 'environment', '环境'),
])
def test_failure_diagnostic_is_local_and_user_output_is_allowlisted(env, monkeypatch, capsys, stderr, category, summary):
    path, cfg = installed(env)
    bridge.write_json(env[1] / 'private/setup.json', {'done_at': 'test', 'version': 1})
    monkeypatch.setattr(bridge, 'native', lambda *a, **kw: subprocess.CompletedProcess(a, 1, '', stderr))
    capsys.readouterr()
    assert bridge.main(['run-daily', '--manifest', str(path)]) == 1
    output = capsys.readouterr()
    assert summary in output.err and 'secret' not in output.err + output.out
    diagnostic = next((path.parent / 'daily-requests').glob('*/diagnostic.json'))
    assert bridge.read_json(diagnostic)['category'] == category
    assert diagnostic.with_name('stderr.txt').read_text(encoding='utf-8') == stderr


def test_real_child_pid_is_recorded_before_wait_and_finishes_cleanly(env, monkeypatch):
    path, cfg = installed(env)
    bridge.write_json(env[1] / 'private/setup.json', {'done_at': 'test', 'version': 1})
    monkeypatch.undo()
    observed = []
    real_popen = subprocess.Popen
    def popen(argv, **kw):
        # Substitute only the executable with a harmless real child; exercise native's pipes/state.
        child = real_popen([sys.executable, '-c', 'import time; time.sleep(0.1); print("test result")'], **kw)
        real_communicate = child.communicate
        def communicate(*a, **kwargs):
            state = bridge.read_json(path.parent / 'daily-active.json')
            observed.append(state['child_pid'] == child.pid and state['state'] == 'running')
            return real_communicate(*a, **kwargs)
        child.communicate = communicate
        return child
    monkeypatch.setattr(bridge.subprocess, 'Popen', popen)
    bridge.run_daily(argparse.Namespace(manifest=path))
    assert observed == [True]
    state = bridge.read_json(path.parent / 'daily-active.json')
    assert state['state'] == 'exited' and state['exit_code'] == 0


def test_enable_rejects_script_only_evidence_for_relay_mode(env, monkeypatch):
    value = bridge.read_json(env[2]); value['target'] = 'telegram:test-chat'
    bridge.write_json(env[2], value)
    path, cfg = installed(env)
    bridge.write_json(env[1] / 'private/setup.json', {'done_at': 'test', 'version': 1})
    python = env[1] / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    python.parent.mkdir(parents=True); python.touch()
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(cfg['times']), ''))
    bridge.verify(argparse.Namespace(manifest=path, delivery=True))
    with pytest.raises(bridge.Refused, match='relay'):
        bridge.enable(cfg, path, True)
    assert all(not j['enabled'] for j in bridge.jobs(cfg))


def test_enable_ignores_stale_caller_snapshot(env):
    path, cfg = installed(env)
    cfg['target'] = 'telegram:stale-recipient'
    cfg['verification'] = {'fake': True}
    with pytest.raises(bridge.Refused, match='confirm-exclusive'):
        bridge.enable(cfg, path, True)
    assert bridge.load_manifest(path)['target'] is None


def test_relay_probe_entry_never_uses_daily_prompt_or_setup(env, capsys):
    path, cfg = installed(env)
    nonce = 'a' * 32
    folder = path.parent / 'relay-probes' / nonce
    bridge.write_json(folder / 'spec.json', {'nonce': nonce, 'active': True})
    bridge.write_text(folder / 'maa-prompt.txt', 'Harmless probe only')
    bridge.run_daily(argparse.Namespace(manifest=path, probe=nonce))
    call = env[4].turns[-1]
    request = Path(call[call.index('--query-file') + 1]).read_text(encoding='utf-8')
    assert request == 'Harmless probe only'
    assert not (env[1] / 'private/setup.json').exists()
    bridge.write_json(folder / 'spec.json', {'nonce': nonce, 'active': False})
    with pytest.raises(bridge.Refused, match='重放'):
        bridge.run_daily(argparse.Namespace(manifest=path, probe=nonce))


def test_probe_runtime_waits_and_records_actual_tool_environment(tmp_path, monkeypatch, capsys):
    module_spec = importlib.util.spec_from_file_location('runtime_probe_test', ROOT / 'templates/hermes/runtime-probe.py')
    probe = importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(probe)
    clock = iter([10.0, 200.0])
    sleeps = []
    monkeypatch.setattr(probe.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(probe.time, 'sleep', sleeps.append)
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0,
                        json.dumps({'environment': {'MAA_DATA_DIR': 'fixture'}, 'resource_exists': True}), ''))
    path = tmp_path / 'result.json'
    probe.run({'nonce': 'test', 'lane': 'relay', 'python': sys.executable, 'workdir': str(tmp_path),
               'result': str(path), 'wait_seconds': 190, 'token': 'test-token'})
    assert sleeps == [190]
    assert bridge.read_json(path)['elapsed'] == 190
    assert capsys.readouterr().out.strip() == 'test-token'


def test_interrupted_wrapper_marks_live_child_unknown_without_killing_or_relaunch(env, monkeypatch, capsys):
    path, cfg = installed(env)
    bridge.write_json(env[1] / 'private/setup.json', {'done_at': 'test', 'version': 1})
    monkeypatch.undo()
    children = []
    real_popen = subprocess.Popen
    def popen(argv, **kw):
        child = real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kw)
        children.append(child)
        def interrupted_wait(*a, **kwargs):
            raise KeyboardInterrupt()
        child.communicate = interrupted_wait
        return child
    monkeypatch.setattr(bridge.subprocess, 'Popen', popen)
    try:
        with pytest.raises(KeyboardInterrupt):
            bridge.run_daily(argparse.Namespace(manifest=path))
        assert children[0].poll() is None
        state = bridge.read_json(path.parent / 'daily-active.json')
        assert state['state'] == 'unknown' and state['child_pid'] == children[0].pid
        with pytest.raises(bridge.Refused, match='状态未确认'):
            bridge.run_daily(argparse.Namespace(manifest=path))
        assert len(children) == 1
        capsys.readouterr()
        bridge.daily_status(argparse.Namespace(manifest=path))
        assert json.loads(capsys.readouterr().out)['state'] == 'unknown'
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=10)
            child.stdout.close()
            child.stderr.close()
