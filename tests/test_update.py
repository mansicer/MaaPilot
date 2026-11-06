"""更新：查新版本的几条路、告诉过用户之后的提醒、MaaPilot 用真的 git 仓库走一遍更新（不联网、不碰模拟器）。"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from maapilot import __version__, cli, paths, toolchain
from maapilot.cliutil import BUSY, NEEDS_USER, CliError
from maapilot.knowledge import sources, updates
from maapilot.oscompat import fs
from maapilot.tools import doctor, update_cmd

FIX = Path(__file__).parent / "fixtures" / "upstream"
NEWER = "99.0.0"


def release(version=NEWER, **kw) -> dict:
    d = json.loads((FIX / "github_release.json").read_text(encoding="utf-8"))
    return {**d, "tag_name": f"v{version}", **kw}


def online(monkeypatch, maapilot=NEWER, core="6.21.4"):
    def fake(url):
        return release(maapilot if url == updates.MAAPILOT_API else core)
    monkeypatch.setattr(updates, "http_json", fake)


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def days_ago(n: float) -> str:
    return (datetime.now().astimezone() - timedelta(days=n)).isoformat(timespec="seconds")


# --- 查新版本 ---------------------------------------------------------------------------------------------------------

def test_release_found_and_due(monkeypatch):
    online(monkeypatch)
    monkeypatch.setattr(updates, "local_versions", lambda: {"maa_core": "6.21.1"})
    st = updates.status(updates.refresh())
    assert st["maapilot"]["update"] and st["maapilot"]["latest"] == NEWER
    assert st["maa-core"]["update"] and st["due"] == ["maapilot", "maa-core"] and not st["lost"]


def test_same_version_is_no_update(monkeypatch):
    online(monkeypatch, maapilot=__version__, core="6.21.1")
    monkeypatch.setattr(updates, "local_versions", lambda: {"maa_core": "6.22.0-beta.1"})
    st = updates.status(updates.refresh())
    assert not st["maapilot"]["update"] and not st["maa-core"]["update"] and st["due"] == []


def test_github_down_keeps_last_result_and_says_so(monkeypatch):
    online(monkeypatch)
    updates.refresh()
    d = updates.load()
    d["at"] = days_ago(5)
    (paths.STATE / "update.json").write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(updates, "http_json", lambda url: (_ for _ in ()).throw(OSError("连不上")))
    st = updates.status(updates.refresh())
    assert st["maapilot"]["latest"] == NEWER and st["lost"] and st["age_days"] == 5
    assert "连不上" in st["errors"]["maapilot"]
    assert any("5 天没" in l for l in updates.lines(st))


def test_maa_core_lookup_tracked_on_its_own(monkeypatch):
    online(monkeypatch)
    updates.refresh()
    d = updates.load()
    d["maa_core_at"] = days_ago(updates.CHECK_LOST_DAYS)
    (paths.STATE / "update.json").write_text(json.dumps(d), encoding="utf-8")

    def only_maapilot(url):
        if url == updates.MAA_API:
            raise OSError("连不上")
        return release(__version__)
    monkeypatch.setattr(updates, "http_json", only_maapilot)
    st = updates.status(updates.refresh())
    assert not st["lost"] and st["maa-core"]["lost"] and st["maa-core"]["latest"] == "6.21.4"
    assert any("MAA 的新版本信息" in l and "连不上" in l for l in updates.lines(st))
    assert doctor.update_brief()["core_lost"]


def test_never_checked_counts_as_lost():
    assert updates.status()["lost"] and "从没" in updates.lines()[0]


def test_bad_release_format_is_recorded(monkeypatch):
    monkeypatch.setattr(updates, "http_json", lambda url: {"name": "没有 tag_name"})
    d = updates.refresh()
    assert "maapilot" in d["errors"] and "maapilot_release" in {u["source"] for u in sources.unhealthy()}


def test_told_quiets_until_new_version_or_remind(monkeypatch):
    online(monkeypatch)
    updates.refresh()
    assert updates.told(["maapilot"])["due"] == []
    d = updates.load()
    d["told"]["maapilot"]["at"] = days_ago(updates.REMIND_DAYS)
    (paths.STATE / "update.json").write_text(json.dumps(d), encoding="utf-8")
    assert updates.status()["due"] == ["maapilot"]
    updates.told(["maapilot"])
    online(monkeypatch, maapilot="99.0.1")
    assert updates.status(updates.refresh())["due"] == ["maapilot"]


def test_maa_cli_only_up_to_tested_and_only_ours(monkeypatch):
    hi = toolchain.MAA_CLI_TESTED[1]
    ours = Path("/x/.tools/maa-cli/maa")
    monkeypatch.setattr(toolchain, "managed", lambda n: ours)
    monkeypatch.setattr(toolchain, "installed_version", lambda n: "0.1.0")
    monkeypatch.setattr("maapilot.config.tool", lambda n, cfg=None: str(ours))
    assert updates.status({})["maa-cli"] == {"current": "0.1.0", "latest": hi, "update": True}
    monkeypatch.setattr(toolchain, "installed_version", lambda n: hi)
    assert not updates.status({})["maa-cli"]["update"]
    monkeypatch.setattr(toolchain, "installed_version", lambda n: "0.1.0")
    monkeypatch.setattr("maapilot.config.tool", lambda n, cfg=None: "/opt/homebrew/bin/maa")
    e = updates.status({})["maa-cli"]
    assert not e["update"] and "别处装的" in e["note"]


def test_brief_shows_update(monkeypatch):
    online(monkeypatch)
    updates.refresh()
    u = doctor.update_brief()
    assert u["due"] == ["maapilot"] and NEWER in u["lines"][0]
    assert any("maapilot-update" in l for l in doctor.brief_lines(
        {"setup_done": True, "guard_stop": None, "goals": None, "goals_active": 0, "last_daily": None, "update": u}))
    updates.told(["maapilot"])
    assert doctor.update_brief() is None


def test_check_command_uses_cache_unless_refresh(capsys, monkeypatch):
    online(monkeypatch)
    calls = []
    real = updates.refresh
    monkeypatch.setattr(updates, "refresh", lambda: calls.append(1) or real())
    monkeypatch.setattr(update_cmd, "blocked", lambda: None)
    assert run(capsys, "update", "check")[0] == 0 and len(calls) == 1
    assert run(capsys, "update", "check")[0] == 0 and len(calls) == 1
    code, out = run(capsys, "update", "check", "--refresh")
    assert code == 0 and len(calls) == 2 and out["data"]["maapilot"]["blocked"] is None


def test_daily_refreshes_update_when_stale(monkeypatch):
    from maapilot.flows import daily
    calls = []
    monkeypatch.setattr(updates, "refresh", lambda: calls.append(1))
    monkeypatch.setattr(updates, "stale", lambda: False)
    assert daily.refresh_update() is False and calls == []
    monkeypatch.setattr(updates, "stale", lambda: True)
    assert daily.refresh_update() is True and calls == [1]


def test_daily_protects_skills_link_without_new_version(monkeypatch):
    from maapilot.flows import daily
    calls = []
    monkeypatch.setattr(doctor, "keep_skills_link", lambda: calls.append(1))
    monkeypatch.setattr(updates, "stale", lambda: False)
    daily.refresh_update()
    assert calls == [1]


# --- 更新 MaaPilot：真的 git 仓库 ---------------------------------------------------------------------------------------

def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """发布的仓库 origin、用户 clone 的 local；origin 上有比本地新的 v99.0.0。"""
    origin, local = tmp_path / "origin", tmp_path / "local"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "a.txt").write_text("1\n", encoding="utf-8")
    (origin / "pyproject.toml").write_text("v1\n", encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "one")
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True)
    (origin / "a.txt").write_text("2\n", encoding="utf-8")
    git(origin, "commit", "-qam", "two")
    git(origin, "tag", f"v{NEWER}")
    monkeypatch.setattr(paths, "ROOT", local)
    monkeypatch.setattr(updates, "MAAPILOT_GIT", str(origin))
    online(monkeypatch)
    monkeypatch.setattr(update_cmd, "_verify", lambda want: None)
    monkeypatch.setattr(update_cmd, "_sync_env", lambda: None)
    return local


def text(repo) -> str:
    return (repo / "a.txt").read_text(encoding="utf-8")


def test_apply_fast_forwards_to_tag(repo):
    r = update_cmd.apply_maapilot()
    assert r["updated"] and r["to"] == NEWER and r["from"] == __version__ and text(repo) == "2\n"
    assert not r["env_synced"] and r["reread"]
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert updates.load()["told"]["maapilot"]["version"] == NEWER


def test_apply_syncs_env_when_dependencies_change(repo, tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    (origin / "pyproject.toml").write_text("v2\n", encoding="utf-8")
    git(origin, "commit", "-qam", "deps")
    git(origin, "tag", "-f", f"v{NEWER}")
    calls = []
    monkeypatch.setattr(update_cmd, "_sync_env", lambda: calls.append(1))
    assert update_cmd.apply_maapilot()["env_synced"] and calls == [1]


def test_apply_rolls_back_when_new_code_is_broken(repo, monkeypatch):
    before = git(repo, "rev-parse", "HEAD")

    def broken(want):
        raise OSError("新版本跑不起来")
    monkeypatch.setattr(update_cmd, "_verify", broken)
    with pytest.raises(CliError) as e:
        update_cmd.apply_maapilot()
    assert e.value.code == "update_failed" and "退回" in e.value.message
    assert git(repo, "rev-parse", "HEAD") == before and text(repo) == "1\n"


def test_apply_never_overwrites_user_edits(repo):
    (repo / "a.txt").write_text("用户改的\n", encoding="utf-8")
    with pytest.raises(CliError) as e:
        update_cmd.apply_maapilot()
    assert e.value.code == "dirty" and e.value.exit == NEEDS_USER and "a.txt" in e.value.message and e.value.hint
    assert text(repo) == "用户改的\n"


def test_apply_blocked_on_another_branch(repo):
    git(repo, "switch", "-qc", "mine")
    with pytest.raises(CliError) as e:
        update_cmd.apply_maapilot()
    assert e.value.code == "not_on_main" and "git switch main" in e.value.hint


def test_apply_blocked_on_local_commits(repo):
    (repo / "b.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "local")
    with pytest.raises(CliError) as e:
        update_cmd.apply_maapilot()
    assert e.value.code == "diverged" and (repo / "b.txt").exists() and text(repo) == "1\n"


def test_apply_without_git_folder_says_how_to_recover(tmp_path, monkeypatch):
    plain = tmp_path / "zip"
    plain.mkdir()
    monkeypatch.setattr(paths, "ROOT", plain)
    b = update_cmd.blocked()
    assert b["code"] == "not_git" and "git clone" in b["hint"]


def test_fetch_ignores_a_changed_origin(repo, tmp_path):
    git(repo, "remote", "set-url", "origin", str(tmp_path / "gone"))
    assert update_cmd.apply_maapilot()["updated"] and text(repo) == "2\n"


def test_already_latest_is_not_an_error(repo, monkeypatch):
    online(monkeypatch, maapilot=__version__)
    assert update_cmd.apply_maapilot()["updated"] is False and text(repo) == "1\n"


def test_apply_refused_during_daily(capsys, monkeypatch):
    from maapilot.flows import daily
    monkeypatch.setattr(daily, "running", lambda: None)
    monkeypatch.setattr(daily, "open_daily", lambda: {"run_id": "20261105-0440"})
    code, out = run(capsys, "update", "apply", "maapilot")
    assert code == BUSY and out["error"]["code"] == "daily_open"


def test_apply_says_busy_when_emulator_is_taken(capsys, monkeypatch):
    from maapilot.crew import lock

    def taken(*a, **kw):
        raise lock.LockBusy("被占用")
    monkeypatch.setattr(lock, "acquire", taken)
    monkeypatch.setattr(lock, "holder", lambda: {"pid": 1, "token": "x"})
    for what in ("maapilot", "maa-resource"):
        code, out = run(capsys, "update", "apply", what)
        assert code == BUSY and out["error"]["code"] == "busy" and out["data"]["holder"] == {"pid": 1}


def link_tracked_but_a_folder(repo):
    """Windows 上 doctor --fix 之后的样子：git 里是符号链接，本地是目录（联接）。"""
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="../.agents/skills",
                          check=True, capture_output=True, encoding="utf-8").stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},.claude/skills")
    git(repo, "commit", "-qm", "link")
    (repo / ".claude" / "skills").mkdir(parents=True)
    (repo / ".claude" / "skills" / "SKILL.md").write_text("x", encoding="utf-8")


def test_skills_link_folder_does_not_block_update(repo):
    link_tracked_but_a_folder(repo)
    assert "D .claude/skills" in git(repo, "status", "--porcelain")
    assert update_cmd.blocked() is None
    assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""


@pytest.mark.parametrize("args", [("restore", ".claude/skills"), ("reset", "--hard"), ("stash",), ("checkout", "-f", "main")])
def test_skills_link_folder_survives_git_cleanups(repo, args):
    link_tracked_but_a_folder(repo)
    doctor.keep_skills_link()
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True)   # 拒绝也行
    assert (repo / ".claude" / "skills" / "SKILL.md").read_text(encoding="utf-8") == "x"


@pytest.mark.skipif(update_cmd.WINDOWS, reason="建符号链接要开发者模式")
def test_real_skills_symlink_left_alone(repo):
    link = repo / ".claude" / "skills"
    link.parent.mkdir()
    link.symlink_to(repo, target_is_directory=True)
    doctor.keep_skills_link()
    assert not git(repo, "ls-files", "-t", "-v", ".claude").startswith("S")


def env_sync(monkeypatch, tmp_path, uv=None, pip=True, ensurepip=True):
    """假的 uv / pip：记下 _sync_env 跑了什么。"""
    calls = []

    def fake(argv, **kw):
        calls.append(argv)
        ok = {"--version": pip, "ensurepip": ensurepip}
        code = 0 if ok.get(argv[-1], True) else 1
        return subprocess.CompletedProcess(argv, code, "", "")
    monkeypatch.setattr(update_cmd.procs, "run_text", fake)
    monkeypatch.setattr("shutil.which", lambda name: uv)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return calls


def test_sync_env_prefers_uv(monkeypatch, tmp_path):
    calls = env_sync(monkeypatch, tmp_path, uv="/bin/uv")
    update_cmd._sync_env()
    assert [c[0] for c in calls] == ["/bin/uv"]


def test_sync_env_finds_uv_outside_path(monkeypatch, tmp_path):
    calls = env_sync(monkeypatch, tmp_path)
    home = tmp_path / ".local" / "bin" / ("uv.exe" if update_cmd.WINDOWS else "uv")
    home.parent.mkdir(parents=True)
    home.write_text("", encoding="utf-8")
    update_cmd._sync_env()
    assert [c[0] for c in calls] == [str(home)]


def test_sync_env_bootstraps_missing_pip(monkeypatch, tmp_path):
    calls = env_sync(monkeypatch, tmp_path, pip=False)
    update_cmd._sync_env()
    assert ["ensurepip" in c for c in calls] == [False, True, False] and calls[-1][-3:] == ["install", "-e", "."]


def test_sync_env_says_why_without_uv_or_pip(monkeypatch, tmp_path):
    env_sync(monkeypatch, tmp_path, pip=False, ensurepip=False)
    with pytest.raises(OSError, match="先装 uv"):
        update_cmd._sync_env()


def test_running_exe_moved_aside_and_copied_back(monkeypatch, tmp_path):
    monkeypatch.setattr(fs, "WINDOWS", True)
    exe = tmp_path / "maapilot.exe"
    exe.write_text("old", encoding="utf-8")
    stale = tmp_path / "maapilot.exe.old1"
    stale.write_text("stale", encoding="utf-8")
    fs.release_running_exe(exe)
    assert exe.read_text(encoding="utf-8") == "old" and not stale.exists()
    assert len(list(tmp_path.glob("maapilot.exe.old*"))) == 1
    exe.write_text("new", encoding="utf-8")                    # 回滚时再来一次：挪开的还是原来跑着的那个，不动它
    fs.release_running_exe(exe)
    assert exe.read_text(encoding="utf-8") == "new" and len(list(tmp_path.glob("maapilot.exe.old*"))) == 1


def test_running_exe_untouched_elsewhere(monkeypatch, tmp_path):
    monkeypatch.setattr(fs, "WINDOWS", False)
    exe = tmp_path / "maapilot.exe"
    exe.write_text("old", encoding="utf-8")
    fs.release_running_exe(exe)
    assert [f.name for f in tmp_path.iterdir()] == ["maapilot.exe"]


def test_verify_loads_real_code():
    update_cmd._verify(__version__)
    with pytest.raises(OSError):
        update_cmd._verify("0.0.0")


# --- MaaCore -----------------------------------------------------------------------------------------------------------

def test_apply_core_runs_maa_update(monkeypatch):
    online(monkeypatch, core="6.21.4")
    versions = iter([{"maa_core": "6.21.1"}, {"maa_core": "6.21.4"}])
    monkeypatch.setattr(updates, "local_versions", lambda: next(versions))
    argvs = []

    class Done:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(update_cmd.procs, "run_text", lambda argv, **kw: argvs.append(argv) or Done())
    r = update_cmd.apply_maa_core()
    assert r["updated"] and (r["from"], r["to"]) == ("6.21.1", "6.21.4") and argvs[-1][-1] == "update"


# --- MAA 资源 ----------------------------------------------------------------------------------------------------------

class Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def fake_maa(monkeypatch, code=0):
    def run(argv, **kw):
        return Run(code, stderr="连不上") if "hot-update" in argv else Run(stdout="abc1234 2026-11-05T10:00:00+08:00\n")
    monkeypatch.setattr(updates.procs, "run_text", run)
    from maapilot.crew import maacore
    monkeypatch.setattr(maacore, "hot_update_dir", lambda: Path("/x/MaaResource"))


def test_resource_update_recorded_and_not_asked(monkeypatch):
    fake_maa(monkeypatch)
    r = updates.hot_update()
    assert r["commit"] == "abc1234" and r["error"] is None and r["at"]
    st = updates.status()
    assert not st["maa-resource"]["lost"] and "maa-resource" not in st["due"]
    assert not updates.resource_stale()


def test_resource_failure_stays_quiet_then_tells_user(monkeypatch):
    fake_maa(monkeypatch)
    updates.hot_update()
    fake_maa(monkeypatch, code=1)
    r = updates.hot_update()
    assert "连不上" in r["error"] and not updates.status()["maa-resource"]["lost"]
    d = updates.load()
    d["resource"]["at"] = days_ago(updates.CHECK_LOST_DAYS)
    (paths.STATE / "update.json").write_text(json.dumps(d), encoding="utf-8")
    st = updates.status()
    assert st["maa-resource"]["lost"] and any("MAA 资源" in l and "连不上" in l for l in updates.lines(st))
    assert doctor.update_brief()["resource_lost"]


def test_resource_never_worked_is_lost(monkeypatch):
    fake_maa(monkeypatch, code=1)
    updates.hot_update()
    assert updates.status()["maa-resource"]["lost"]


def test_daily_updates_resource_once_an_hour(monkeypatch):
    from maapilot.flows import daily
    fake_maa(monkeypatch)
    assert daily.refresh_resource()["commit"] == "abc1234"
    assert daily.refresh_resource() is None


def test_apply_resource_failure_is_reported(capsys, monkeypatch):
    fake_maa(monkeypatch, code=1)
    code, out = run(capsys, "update", "apply", "maa-resource")
    assert code == 1 and out["error"]["code"] == "resource_update_failed"


@pytest.mark.parametrize("before, after", [
    ("[resource]\nauto_update = false\nbackend = \"git\"\n", "[resource]\nauto_update = true\nbackend = \"git\"\n"),
    ("[core]\nchannel = \"stable\"\n\n[resource]\nbackend = \"git\"\n",
     "[core]\nchannel = \"stable\"\n\n[resource]\nauto_update = true\nbackend = \"git\"\n"),
    ("[core]\nchannel = \"stable\"\n", "[core]\nchannel = \"stable\"\n\n[resource]\nauto_update = true\n"),
    ("[resource]\nauto_update = true # 开\n", "[resource]\nauto_update = true # 开\n"),
    ("[other]\nauto_update = true\n\n[resource]\nauto_update = false\n",
     "[other]\nauto_update = true\n\n[resource]\nauto_update = true\n"),
    ("[resource]\nauto_update = false\n\n[other]\nauto_update = false\n",
     "[resource]\nauto_update = true\n\n[other]\nauto_update = false\n"),
    ("[other]\nauto_update = false\n\n[resource]\nbackend = \"git\"\n",
     "[other]\nauto_update = false\n\n[resource]\nauto_update = true\nbackend = \"git\"\n"),
])
def test_auto_update_turned_on_in_existing_config(before, after):
    from maapilot.crew import maa
    p = maa.config_dir() / "cli.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(before, encoding="utf-8")
    maa.ensure_auto_update()
    assert p.read_text(encoding="utf-8") == after
