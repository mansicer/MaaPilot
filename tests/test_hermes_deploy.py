"""Hermes integration only: no model, network, real profiles or game in these tests."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
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
        if args[:2] == ("profile", "create"):
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
                tree = ast.parse((home / "scripts" / job["script"]).read_text(encoding="utf-8"))
                argv = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)))
                spec = bridge.read_json(argv[1])
                lane = spec["lane"]
                expected = {**cfg["maa_runtime"], "MAA_CONFIG_DIR": str(Path(cfg["workdir"]) / "private/maa-config")}
                probe = {"nonce": spec["nonce"], "ok": True, "environment": expected,
                         "directories": {kind: expected[f"MAA_{kind.upper()}_DIR"] for kind in ("data", "cache", "state", "config")},
                         "resource_exists": True}
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
    cron = env[0] / "profiles/maa/cron/jobs.json"
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
    cron = env[0] / "profiles/maa/cron/jobs.json"
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
    cron = env[0] / "profiles/maa/cron/jobs.json"
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
        if args[:2] == ("profile", "create"):
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
    cron = env[0] / "profiles/maa/cron/jobs.json"
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
    bridge.write_json(env[1] / "private/setup.json", {"done_at": "2026-10-27T14:00:00+08:00", "version": 1})
    python = env[1] / (".venv/Scripts/python.exe" if bridge.os.name == "nt" else ".venv/bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, json.dumps(cfg["times"]), ""))
    bridge.write_json(env[0] / "profiles/maa/cron/jobs.json", {"jobs": []})
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
    cron = env[0] / "profiles/maa/cron/jobs.json"
    content = bridge.read_json(cron)
    for job in content["jobs"]:
        job["next_run_at"] = "2000-01-01T00:00:00+00:00"
    bridge.write_json(cron, content)
    bridge.verify(argparse.Namespace(manifest=path, delivery=True))
    cfg = bridge.load_manifest(path)
    bridge.enable(cfg, path, True)
    calls = [a for _, a in env[4].calls if a[:2] in {("cron", "resume"), ("cron", "edit")}
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
