"""更新：查新版本的几条路、告诉过用户之后的提醒、MaaPilot 用真的 git 仓库走一遍更新（不联网、不碰模拟器）。"""
from __future__ import annotations

import json
import shutil
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


def test_never_checked_is_not_lost():
    st = updates.status()
    assert not st["lost"] and not st["maa-core"]["lost"] and updates.lines() == [] and doctor.update_brief() is None


def test_first_failures_wait_three_days(monkeypatch):
    def down(url):
        raise OSError("连不上")
    monkeypatch.setattr(updates, "http_json", down)
    d = updates.refresh()
    assert not updates.status(d)["lost"] and d["tried_at"]
    first = d["tried_at"]
    assert updates.refresh()["tried_at"] == first                # 再查不重新计时
    d["tried_at"] = days_ago(3)
    st = updates.status(d)
    assert st["lost"] and st["maa-core"]["lost"] and "从没" in updates.lines(st)[0]


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
    before = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CliError) as e:
        update_cmd.apply_maapilot()
    assert e.value.code == "dirty" and e.value.exit == NEEDS_USER and "a.txt" in e.value.message and e.value.hint
    assert text(repo) == "用户改的\n" and git(repo, "rev-parse", "HEAD") == before


def test_edits_the_update_does_not_touch_are_kept(repo):
    (repo / "pyproject.toml").write_text("用户改的\n", encoding="utf-8")
    assert update_cmd.blocked() is None
    r = update_cmd.apply_maapilot()
    assert r["updated"] and text(repo) == "2\n"
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == "用户改的\n"


def test_edits_kept_when_rolling_back(repo, monkeypatch):
    (repo / "pyproject.toml").write_text("用户改的\n", encoding="utf-8")

    def broken(want):
        raise OSError("跑不起来")
    monkeypatch.setattr(update_cmd, "_verify", broken)
    with pytest.raises(CliError):
        update_cmd.apply_maapilot()
    assert text(repo) == "1\n" and (repo / "pyproject.toml").read_text(encoding="utf-8") == "用户改的\n"


def test_other_branch_without_own_commits_switches_to_main(repo):
    git(repo, "switch", "-qc", "mine")
    r = update_cmd.apply_maapilot()
    assert r["updated"] and r["switched_from"] == "mine" and text(repo) == "2\n"
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"


def test_detached_head_switches_to_main(repo):
    git(repo, "checkout", "-q", "--detach")
    r = update_cmd.apply_maapilot()
    assert r["updated"] and git(repo, "symbolic-ref", "--short", "HEAD") == "main"


def test_missing_main_is_created(repo):
    git(repo, "switch", "-qc", "mine")
    git(repo, "branch", "-qD", "main")
    assert update_cmd.apply_maapilot()["updated"] and git(repo, "symbolic-ref", "--short", "HEAD") == "main"


def test_rollback_returns_to_the_branch(repo, monkeypatch):
    git(repo, "switch", "-qc", "mine")
    before = git(repo, "rev-parse", "main")

    def broken(want):
        raise OSError("跑不起来")
    monkeypatch.setattr(update_cmd, "_verify", broken)
    with pytest.raises(CliError):
        update_cmd.apply_maapilot()
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "mine" and git(repo, "rev-parse", "main") == before and text(repo) == "1\n"


def test_branch_with_own_commits_is_left_alone(repo):
    git(repo, "switch", "-qc", "mine")
    (repo / "b.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "local")
    with pytest.raises(CliError) as e:
        update_cmd.apply_maapilot()
    assert e.value.code == "not_on_main" and e.value.exit == NEEDS_USER
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "mine" and (repo / "b.txt").exists()


def test_diverged_main_is_left_alone_from_another_branch(repo):
    (repo / "b.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "local")
    git(repo, "switch", "-qc", "mine", "HEAD~1")
    with pytest.raises(CliError) as e:
        update_cmd.apply_maapilot()
    assert e.value.code == "diverged" and git(repo, "symbolic-ref", "--short", "HEAD") == "mine"


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


def test_refused_branch_leaves_index_alone(repo):
    link_tracked_but_a_folder(repo)
    (repo / ".claude" / "skills").rename(repo / "skills-aside")
    (repo / ".claude" / "skills").write_text("../.agents/skills", encoding="utf-8")
    assert update_cmd.blocked() is None                          # 真链接或文本文件：不用打标记
    assert git(repo, "ls-files", "-t", ".claude/skills").startswith("H")


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

@pytest.fixture
def core(monkeypatch, tmp_path):
    """假的 maa-cli 本体目录：lib 里有 MaaCore 就读得出版本；run 换成测试里的 maa update。"""
    from maapilot.crew import maacore
    online(monkeypatch, core="6.21.4")
    lib, res = tmp_path / "lib", tmp_path / "resource"
    lib.mkdir()
    res.mkdir()
    (lib / "MaaCore.dll").write_text("6.21.1", encoding="utf-8")
    (res / "tasks.json").write_text("old", encoding="utf-8")

    def installed():
        f = lib / "MaaCore.dll"
        return f.read_text(encoding="utf-8") if f.exists() else None
    monkeypatch.setattr(maacore, "_maa_dir", lambda which: {"library": lib, "resource": res}[which])
    monkeypatch.setattr(maacore, "cli_core", lambda: (f"v{installed()}", lib) if installed() else None)
    monkeypatch.setattr(updates, "local_versions", lambda: {"maa_core": installed()})
    update_cmd.check(refresh=True)
    return lib, res, maacore


def maa_update(monkeypatch, fn):
    def run(argv, **kw):
        if argv[-1] != "update":
            return Run()
        fn()
        return Run(0 if fn.__name__ != "wiped" else 1, stderr="Failed to remove dir (os error 4395)")
    monkeypatch.setattr(update_cmd.procs, "run_text", run)


def test_apply_core_updates_and_drops_backup(core, monkeypatch):
    lib, res, maacore = core

    def installs():
        assert (maacore._backup_root() / "library" / "MaaCore.dll").exists()    # 先备份
        (lib / "MaaCore.dll").write_text("6.21.4", encoding="utf-8")
    maa_update(monkeypatch, installs)
    r = update_cmd.apply_maa_core()
    assert r["updated"] and (r["from"], r["to"]) == ("6.21.1", "6.21.4") and not maacore._backup_root().exists()


def test_failed_core_update_restores_old_version(core, monkeypatch):
    """maa update 先删后装：删到一半失败，MaaCore 没了（11-06 Windows 上 os error 4395）。"""
    lib, res, maacore = core

    def wiped():
        (lib / "MaaCore.dll").unlink()
    maa_update(monkeypatch, wiped)
    with pytest.raises(CliError) as e:
        update_cmd.apply_maa_core()
    assert e.value.code == "maa_update_failed" and "4395" in e.value.message and "6.21.1" in e.value.message
    assert (lib / "MaaCore.dll").read_text(encoding="utf-8") == "6.21.1" and "网络能上" not in e.value.hint
    assert updates.status()["maa-core"]["current"] == "6.21.1" and not maacore._backup_root().exists()


def test_core_update_without_a_working_core_counts_as_failed(core, monkeypatch):
    lib, res, maacore = core

    def installs_nothing():
        (lib / "MaaCore.dll").unlink()
    maa_update(monkeypatch, installs_nothing)
    with pytest.raises(CliError) as e:
        update_cmd.apply_maa_core()
    assert e.value.code == "maa_update_failed" and (lib / "MaaCore.dll").exists()


def test_says_so_when_core_cannot_be_restored(core, monkeypatch):
    lib, res, maacore = core

    def wiped():
        (lib / "MaaCore.dll").unlink()
    maa_update(monkeypatch, wiped)
    monkeypatch.setattr(maacore, "restore_core", lambda saved: False)
    with pytest.raises(CliError) as e:
        update_cmd.apply_maa_core()
    assert e.value.code == "maa_core_broken" and e.value.data["backup"]
    assert maacore._backup_root().exists() and updates.status()["maa-core"]["current"] is None   # 不再报旧版本


def test_restore_removes_files_the_failed_update_left(core, monkeypatch):
    lib, res, maacore = core

    def wiped():
        (lib / "MaaCore.dll").unlink()
        (lib / "new.dll").write_text("new", encoding="utf-8")
    maa_update(monkeypatch, wiped)
    with pytest.raises(CliError) as e:
        update_cmd.apply_maa_core()
    assert e.value.code == "maa_update_failed" and [f.name for f in lib.iterdir()] == ["MaaCore.dll"]


def test_backup_kept_when_leftovers_cannot_be_removed(core, monkeypatch):
    lib, res, maacore = core

    def wiped():
        (lib / "MaaCore.dll").unlink()
        (lib / "new.dll").write_text("new", encoding="utf-8")
    maa_update(monkeypatch, wiped)
    real = Path.unlink

    def stuck(self, *a, **kw):
        if self.name == "new.dll":
            raise PermissionError("占用中")
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", stuck)
    with pytest.raises(CliError) as e:
        update_cmd.apply_maa_core()
    assert e.value.code == "maa_core_broken" and maacore._backup_root().exists()


@pytest.mark.parametrize("why", ["copy", "no_dir"])
def test_core_not_touched_when_backup_fails(core, monkeypatch, why):
    lib, res, maacore = core
    if why == "copy":
        def full(*a, **kw):
            raise shutil.Error([("a", "b", "读不了")])
        monkeypatch.setattr(maacore.shutil, "copytree", full)
    else:
        monkeypatch.setattr(maacore, "_maa_dir", lambda which: lib if which == "library" else None)
    maa_update(monkeypatch, lambda: pytest.fail("不该跑 maa update"))
    with pytest.raises(CliError) as e:
        update_cmd.apply_maa_core()
    assert e.value.code == "maa_update_failed" and "没有更新" in e.value.message and e.value.data == {"version": "6.21.1"}


def windows_package(tmp_path, monkeypatch, maacore, lib, res, *, tag="v6.21.4"):
    """假的发布：这个系统的包放在 maa-cli 的缓存目录里（不下载）。"""
    import zipfile
    cache = tmp_path / "cache"
    cache.mkdir()
    name = f"MAA-{tag}-win-x64.zip"
    with zipfile.ZipFile(cache / name, "w") as z:
        z.writestr("MaaCore.dll", tag.removeprefix("v"))
        z.writestr("start.bat", "x")
        z.writestr("filelist.txt", "x")
        z.writestr("externals/onnx.dll", "x")
        z.writestr("resource/tasks.json", "new")
    monkeypatch.setattr(update_cmd, "WINDOWS", True)
    monkeypatch.setattr(maacore, "WINDOWS", True)
    monkeypatch.setattr(maacore, "_asset_pattern", lambda: __import__("re").compile(r"^MAA-v[\d.]+-win-x64\.zip$"))
    monkeypatch.setattr(maacore, "_maa_dir", lambda which: {"library": lib, "resource": res, "cache": cache}[which])

    class Rel:
        def __enter__(self):
            return __import__("io").BytesIO(json.dumps({"tag_name": tag, "assets": [{"name": name}]}).encode())

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(maacore.urllib.request, "urlopen", lambda *a, **kw: Rel())


def test_windows_falls_back_to_the_package(core, monkeypatch, tmp_path):
    lib, res, maacore = core

    def wiped():
        (lib / "MaaCore.dll").unlink()
    maa_update(monkeypatch, wiped)
    windows_package(tmp_path, monkeypatch, maacore, lib, res)
    r = update_cmd.apply_maa_core()
    assert r["updated"] and r["to"] == "6.21.4" and r["via"] == "安装包"
    assert sorted(f.name for f in lib.iterdir()) == ["MaaCore.dll", "onnx.dll"]
    assert (res / "tasks.json").read_text(encoding="utf-8") == "new" and not maacore._backup_root().exists()


def test_package_failure_restores_old_version(core, monkeypatch, tmp_path):
    lib, res, maacore = core

    def wiped():
        (lib / "MaaCore.dll").unlink()
    maa_update(monkeypatch, wiped)
    monkeypatch.setattr(update_cmd, "WINDOWS", True)

    def fails():
        (lib / "half.dll").write_text("x", encoding="utf-8")
        raise OSError("下载失败")
    monkeypatch.setattr(maacore, "install_into_cli", fails)
    with pytest.raises(CliError) as e:
        update_cmd.apply_maa_core()
    assert e.value.code == "maa_update_failed" and [f.name for f in lib.iterdir()] == ["MaaCore.dll"]
    assert (lib / "MaaCore.dll").read_text(encoding="utf-8") == "6.21.1"


def test_startup_asks_for_a_check_when_stale():
    u = doctor.update_brief(startup=True)
    assert u["check_needed"] and "update check" in u["lines"][-1]
    assert doctor.update_brief() is None


def test_core_notes_shown(monkeypatch):
    online(monkeypatch)
    assert updates.status(updates.refresh())["maa-core"]["notes"]


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
