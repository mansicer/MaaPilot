"""v0.1 留下的系统定时（oscompat.sched：只认、只删）、`daily unschedule`、doctor 的 legacy_schedule、各系统的提示（oscompat.hints）、
doctor 的 .claude/skills 链接项。crontab 用假脚本（MAAPILOT_CRONTAB）；任务计划程序在各系统上都用假的 schtasks 脚本
（MAAPILOT_SCHTASKS）+ 改 WINDOWS 标志测；真的 schtasks 只在 Windows 上建一个测试用的任务（v0.1 的样子）再删掉。"""
import codecs
import json
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from maapilot import cli, paths
from maapilot.oscompat import hints, sched
from maapilot.tools import doctor
from tests.portable import WINDOWS, fake_program, needs_cron
from tests.test_daily import _no_real_crontab, fake_crontab, OTHER  # noqa: F401 - fixture


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def cfg():
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\nschedule: ['04:40', '16:40']\n", encoding="utf-8")


# v0.1 的 `daily schedule` 写出的行（启动器写法）和更早的写法（环境变量写在行里）：都是本项目的
V01 = (f"40 4 * * * {paths.ROOT}/.venv/bin/python -X utf8 -m maapilot.oscompat.launch --env PYTHONUTF8=1 "
       f"--log {paths.ROOT}/private/log/schedule.log -- daily run --trigger schedule # maapilot-v2 {paths.ROOT}\n")
V01_OLD = (f"40 16 * * * PATH=/x PYTHONUTF8=1 {paths.ROOT}/.venv/bin/maapilot daily run --trigger schedule "
           f">> {paths.ROOT}/private/log/cron.log 2>&1 # maapilot-v2 {paths.ROOT}\n")
ELSEWHERE = "40 5 * * * /elsewhere/maapilot daily run # maapilot-v2 /elsewhere\n"


# --- cron ------------------------------------------------------------------------------------------------------------

def test_cron_lines_recognized():
    b = sched.CronBackend()
    line = V01_OLD.strip().replace("40 16", "40 4,16")
    assert b.mine(V01) and b.mine(line) and b.times_of([line]) == ["04:40", "16:40"]
    assert not b.mine(ELSEWHERE) and not b.old(ELSEWHERE)
    assert b.old(OTHER.splitlines()[1]) and not b.old("# maapilot job tick") and not b.old(V01)


@needs_cron
def test_unschedule_removes_only_our_v01_lines(capsys, tmp_path, monkeypatch, cfg):
    store = tmp_path / "crontab.txt"
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, OTHER + V01 + ELSEWHERE + V01_OLD)))
    code, out = run(capsys, "daily", "unschedule", "--dry-run")
    assert code == 0 and out["data"]["removed"] == [V01.strip(), V01_OLD.strip()] and out["data"]["dry_run"] is True
    assert out["data"]["old_entries"] == [OTHER.splitlines()[1]] and "maapilot-v2 /elsewhere" in out["data"]["preview"]
    assert store.read_text(encoding="utf-8") == OTHER + V01 + ELSEWHERE + V01_OLD                  # 没写
    code, out = run(capsys, "daily", "unschedule")
    assert code == 0 and store.read_text(encoding="utf-8") == OTHER + ELSEWHERE    # 别的行（更旧的、别的目录的）一个字节都不动
    code, out = run(capsys, "daily", "unschedule")                                  # 再跑：没什么可删
    assert code == 0 and out["data"]["removed"] == [] and store.read_text(encoding="utf-8") == OTHER + ELSEWHERE


@needs_cron
def test_unschedule_empty_crontab(capsys, tmp_path, monkeypatch, cfg):
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, None)))
    code, out = run(capsys, "daily", "unschedule")
    assert code == 0 and out["data"]["removed"] == [] and not (tmp_path / "crontab.txt").exists()


def test_schedule_and_watch_commands_are_gone(capsys, cfg):
    code, out = run(capsys, "daily", "schedule")
    assert code == 2 and out["error"]["code"] == "bad_arguments"
    code, out = run(capsys, "daily", "watch")
    assert code == 2


@needs_cron
def test_doctor_legacy_schedule_via_probe():
    from tests.test_tools import FakeProbe
    line = V01 + "*/15 * * * * maapilot job tick\n"
    probe = FakeProbe({"crontab -l": (0, line.encode(), b"")})
    assert doctor.legacy_schedule(probe) == {"installed": True, "times": ["04:40"], "entries": [V01.strip()],
                                             "backend": "cron", "old_entries": ["*/15 * * * * maapilot job tick"]}
    item = doctor.check_legacy_schedule(doctor.Ctx(True, probe))
    assert not item["ok"] and item["required"] is False and "daily unschedule" in item["fix"]
    assert doctor.legacy_schedule_brief(probe) == {"times": ["04:40"], "backend": "cron"}
    probe = FakeProbe({"crontab -l": (1, b"", b"crontab: no crontab for x")})
    assert doctor.legacy_schedule(probe)["installed"] is False and doctor.check_legacy_schedule(doctor.Ctx(True, probe))["ok"]
    assert doctor.legacy_schedule_brief(probe) is None
    probe = FakeProbe({"crontab -l": (1, b"", b"permission denied")})
    info = doctor.legacy_schedule(probe)
    assert info["installed"] is None and "permission denied" in info["error"]
    item = doctor.check_legacy_schedule(doctor.Ctx(True, probe))
    assert not item["ok"] and item["required"] is False and doctor.legacy_schedule_brief(probe) is None


# --- 假后端 ---------------------------------------------------------------------------------------------------------

def test_fake_backend(capsys, tmp_path, monkeypatch, cfg):
    store = tmp_path / "sched.json"
    store.write_text(json.dumps([{"times": ["03:00"], "command": "x", "mine": False, "ident": "other", "old": True},
                                 {"times": ["04:40", "16:40"], "command": "y", "mine": True,
                                  "ident": f"maapilot-daily {paths.ROOT}"}]), encoding="utf-8")
    monkeypatch.setenv(sched.SCHEDULER_ENV, f"fake:{store}")
    paths.SETUP.write_text("{}", encoding="utf-8")
    code, out = run(capsys, "doctor", "--brief")
    assert out["data"]["legacy_schedule"] == {"times": ["04:40", "16:40"], "backend": "fake"}
    assert any("daily unschedule" in ln for ln in out["data"]["lines"])
    code, out = run(capsys, "daily", "unschedule")
    assert code == 0 and out["data"]["removed"] == [f"maapilot-daily {paths.ROOT}"] and out["data"]["old_entries"] == ["other"]
    assert [e["ident"] for e in json.loads(store.read_text(encoding="utf-8"))] == ["other"]
    code, out = run(capsys, "doctor", "--brief")
    assert out["data"]["legacy_schedule"] is None and not any("unschedule" in ln for ln in out["data"]["lines"])


# --- 任务计划程序：任务定义 -------------------------------------------------------------------------------------------

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def task_xml(times: list[str], description: str, *, command: str = r"C:\R\.venv\Scripts\pythonw.exe",
             arguments: str = "-X utf8 -m maapilot.oscompat.launch -- daily run --trigger schedule", workdir: str = "",
             enabled: bool = True, weekly: bool = False) -> str:
    """v0.1 的 `daily schedule` 建的那种任务定义（同它当时的 build_xml）：给读、删的测试用。"""
    trig = "".join(
        f"<CalendarTrigger><StartBoundary>2026-10-17T{t}:00</StartBoundary><Enabled>true</Enabled>"
        + ("<ScheduleByWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>" if weekly
           else "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>") + "</CalendarTrigger>" for t in times)
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            f'<Task version="1.2" xmlns="{NS}"><RegistrationInfo><Description>{escape(description)}</Description>'
            f"</RegistrationInfo><Triggers>{trig}</Triggers>"
            '<Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>'
            "</Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
            "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
            "<StartWhenAvailable>true</StartWhenAvailable><Enabled>" + ("true" if enabled else "false") + "</Enabled>"
            "<WakeToRun>false</WakeToRun><ExecutionTimeLimit>PT6H</ExecutionTimeLimit></Settings>"
            f'<Actions Context="Author"><Exec><Command>{escape(command)}</Command><Arguments>{escape(arguments)}</Arguments>'
            + (f"<WorkingDirectory>{escape(workdir)}</WorkingDirectory>" if workdir else "") + "</Exec></Actions></Task>\n")


def test_parse_xml_reads_v01_tasks():
    xml = task_xml(["04:40", "16:40"], r"maapilot-daily C:\My Repo", workdir=r"C:\My Repo")
    info = sched.parse_xml(xml)
    assert info["description"] == r"maapilot-daily C:\My Repo" and info["times"] == ["04:40", "16:40"]
    assert info["workdir"] == r"C:\My Repo" and info["enabled"] is True and info["run_if_missed"] is True
    assert sched.parse_xml(codecs.BOM_UTF16_LE + xml.encode("utf-16-le")) == info      # schtasks /XML 给的字节
    assert sched.parse_xml(xml.encode("utf-8")) == info
    assert sched.parse_xml(task_xml(["04:40"], "x", enabled=False))["enabled"] is False
    assert sched.parse_xml(task_xml(["04:40"], "x", weekly=True))["times"] == []
    with pytest.raises(sched.CliError):
        sched.parse_xml(b"not xml")


def test_task_names_and_decode():
    csv_text = ('"\\Microsoft\\Windows\\Defrag\\ScheduledDefrag","N/A","就绪"\r\n'
                '"\\MaaPilot\\daily-0123abcd","2026/10/17 4:40:00","就绪"\r\n'
                '"\\maapilot\\daily-ffffffff","N/A","已禁用"\r\n'
                '"\\MaaPilot\\daily-0123abcd","2026/10/17 4:40:00","就绪"\r\n')
    assert sched.task_names(csv_text) == ["\\MaaPilot\\daily-0123abcd", "\\maapilot\\daily-ffffffff"]
    assert sched.decode(codecs.BOM_UTF16_LE + "任务".encode("utf-16-le")) == "任务"
    assert sched.decode("ab任务".encode("utf-16-le")) == "ab任务"                    # 没有 BOM 的 UTF-16
    assert sched.decode("就绪".encode("utf-8")) == "就绪"
    n = sched.task_name(Path("/a/MaaPilot"))
    assert n.startswith("\\MaaPilot\\daily-") and len(n) == len("\\MaaPilot\\daily-") + 8
    assert n == sched.task_name("/a/MaaPilot/") and n != sched.task_name("/b/MaaPilot")


# --- 任务计划程序：经假的 schtasks 跑全套命令 ------------------------------------------------------------------------------

FAKE_SCHTASKS = r'''
import json, os, sys
store = os.environ["FAKE_SCHTASKS_DIR"]
os.makedirs(store, exist_ok=True)
a = sys.argv[1:]
with open(os.path.join(store, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(a) + "\n")
def path(n):
    return os.path.join(store, n.strip("\\").replace("\\", "__") + ".xml")
def names():
    return ["\\" + f[:-4].replace("__", "\\") for f in sorted(os.listdir(store)) if f.endswith(".xml")]
out = sys.stdout.buffer
if a == ["/Query", "/FO", "CSV", "/NH"]:
    rows = ['"\\Microsoft\\Windows\\Foo","N/A","就绪"'] + ['"%s","2026/10/17 4:40:00","就绪"' % n for n in names()]
    out.write(("\r\n".join(rows) + "\r\n").encode("gbk"))
elif a[0] == "/Query" and "/XML" in a:
    n = a[a.index("/TN") + 1]
    if not os.path.exists(path(n)):
        sys.stderr.buffer.write("错误: 系统找不到指定的文件。\r\n".encode("gbk")); sys.exit(1)
    out.write(open(path(n), "rb").read())
elif a[0] == "/Delete":
    os.remove(path(a[a.index("/TN") + 1]))
else:
    sys.exit(2)
'''


@pytest.fixture
def windows(tmp_path, monkeypatch, cfg):
    """当成 Windows：调度用任务计划程序，schtasks 是假脚本（任务定义存在一个目录里）。"""
    monkeypatch.setattr(sched, "WINDOWS", True)
    store = tmp_path / "schtasks"
    monkeypatch.setenv("FAKE_SCHTASKS_DIR", str(store))
    monkeypatch.setenv(sched.SCHTASKS_ENV, str(fake_program(tmp_path / "fake-schtasks", FAKE_SCHTASKS)))
    return store


def _calls(store: Path) -> list[list[str]]:
    return [json.loads(ln) for ln in (store / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


def _task(store: Path, root, times=("04:40", "16:40")) -> str:
    """v0.1 在 root 这个目录装的任务（root 是本仓库时就是"本项目的"）。"""
    name = sched.task_name(root)
    xml = task_xml(list(times), f"maapilot-daily {root}", workdir=str(root))
    store.mkdir(parents=True, exist_ok=True)
    (store / (name.strip("\\").replace("\\", "__") + ".xml")).write_bytes(codecs.BOM_UTF16_LE + xml.encode("utf-16-le"))
    return name


def test_windows_legacy_detect_and_unschedule(capsys, windows):
    other = _task(windows, r"D:\Other\MaaPilot", ("05:00",))
    mine = _task(windows, paths.ROOT)
    b = sched.backend()
    assert isinstance(b, sched.SchTasksBackend) and b.available() == (True, None)
    info = doctor.legacy_schedule(doctor.PROBE)
    assert info == {"installed": True, "times": ["04:40", "16:40"], "entries": [mine], "old_entries": [other],
                    "backend": "schtasks"}
    item = doctor.check_legacy_schedule(doctor.Ctx(True, doctor.PROBE))
    assert not item["ok"] and item["entries"] == [mine] and item["old_entries"] == [other]
    paths.SETUP.write_text("{}", encoding="utf-8")
    code, out = run(capsys, "doctor", "--brief")
    assert code == 0 and out["data"]["legacy_schedule"] == {"times": ["04:40", "16:40"], "backend": "schtasks"}
    code, out = run(capsys, "reset", "--dry-run")
    assert code == 0 and out["data"]["legacy_schedule"]["entries"] == [mine]
    # 取消：只删自己的（别的目录的 MaaPilot 的任务不动）
    code, out = run(capsys, "daily", "unschedule", "--dry-run")
    assert code == 0 and out["data"]["removed"] == [mine] and not [c for c in _calls(windows) if c[0] == "/Delete"]
    code, out = run(capsys, "daily", "unschedule")
    assert code == 0 and out["data"]["removed"] == [mine] and out["data"]["old_entries"] == [other]
    assert [c for c in _calls(windows) if c[0] == "/Delete"] == [["/Delete", "/TN", mine, "/F"]]
    assert doctor.legacy_schedule(doctor.PROBE)["installed"] is False
    assert doctor.check_legacy_schedule(doctor.Ctx(True, doctor.PROBE))["ok"]


def test_windows_schtasks_errors(capsys, windows, monkeypatch, tmp_path):
    monkeypatch.setenv(sched.SCHTASKS_ENV, str(fake_program(tmp_path / "broken", "import sys\nsys.exit(5)\n")))
    info = doctor.legacy_schedule(doctor.PROBE)
    assert info["installed"] is None and "退出码 5" in info["error"]
    code, out = run(capsys, "daily", "unschedule")
    assert code == 5 and out["error"]["code"] == "schtasks_failed"


def test_windows_without_schtasks(capsys, cfg, monkeypatch):
    """找不到 schtasks.exe：当作没有旧的定时，unschedule 也没什么可删。"""
    monkeypatch.setattr(sched, "WINDOWS", True)
    b = sched.backend()
    assert isinstance(b, sched.Unavailable) and "任务计划程序" in b.available()[1]
    code, out = run(capsys, "daily", "unschedule")
    assert code == 0 and out["data"]["removed"] == [] and out["data"]["backend"] == "none"
    assert doctor.check_legacy_schedule(doctor.Ctx(True, doctor.PROBE))["ok"]
    paths.SETUP.write_text("{}", encoding="utf-8")
    code, out = run(capsys, "doctor", "--brief")
    assert code == 0 and out["data"]["legacy_schedule"] is None


@pytest.mark.skipif(not WINDOWS, reason="真的任务计划程序只有 Windows 有")
def test_real_schtasks_read_and_remove(tmp_path, monkeypatch):
    """真的 schtasks：在单独的文件夹（\\MaaPilotTest\\）里按一个假的仓库路径建一个 v0.1 样子的任务、读回来、再删掉；不碰真装的。"""
    monkeypatch.delenv(sched.SCHTASKS_ENV)
    root = tmp_path / "fake-root"
    b = sched.SchTasksBackend(folder="\\MaaPilotTest\\", root=root)
    assert b.available() == (True, None)
    src = tmp_path / "task.xml"
    src.write_bytes(codecs.BOM_UTF16_LE + task_xml(["03:07", "15:07"], b.description(), command=sys.executable,
                                                   arguments="-c pass", workdir=str(tmp_path)).encode("utf-16-le"))
    try:
        p = subprocess.run([b.program(), "/Create", "/TN", b.task(), "/XML", str(src), "/F"], capture_output=True)
        assert p.returncode == 0, p.stderr
        mine = [e for e in b.read() if e.mine]
        assert len(mine) == 1 and mine[0].times == ["03:07", "15:07"] and mine[0].enabled is True
        assert b.remove()["removed"] == [b.task()]
        assert not [e for e in b.read() if e.mine]
    finally:
        subprocess.run([b.program(), "/Delete", "/TN", b.task(), "/F"], capture_output=True)


# --- 提示 -----------------------------------------------------------------------------------------------------------

def test_hints_per_os(monkeypatch):
    monkeypatch.setattr(hints, "WINDOWS", False)
    monkeypatch.setattr(hints, "MACOS", True)
    assert hints.install_hint("maa").startswith("maapilot tools install maa-cli") and 16384 in hints.common_ports()
    assert not any("brew" in hints.install_hint(t) for t in ("maa", "maa_upgrade", "adb", "uv"))
    monkeypatch.setattr(hints, "WINDOWS", True)
    monkeypatch.setattr(hints, "MACOS", False)
    assert "maapilot tools install" in hints.install_hint("maa") and "模拟器自带" in hints.install_hint("adb")
    assert {16384, 5555, 62001} <= set(hints.common_ports()) and "VCRedist" in hints.install_hint("vcredist")
    monkeypatch.setattr(hints, "WINDOWS", False)
    assert "brew" not in hints.install_hint("adb") and hints.install_hint("codex").startswith("npm ")


# --- .claude/skills 链接 ----------------------------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".agents" / "skills" / "maapilot-daily").mkdir(parents=True)
    (root / ".agents" / "skills" / "maapilot-daily" / "SKILL.md").write_text("x", encoding="utf-8")
    (root / ".claude").mkdir()
    return root


def test_skills_link_plain_file_fixed(repo, monkeypatch):
    link = repo / ".claude" / "skills"
    link.write_text("../.agents/skills", encoding="utf-8")         # Windows 上 core.symlinks=false 检出的样子
    assert doctor.skills_link(repo) == "file"
    monkeypatch.setattr(paths, "ROOT", repo)
    item = doctor.check_skills_link(None)
    assert not item["ok"] and item["state"] == "file" and item["fix"] == "maapilot doctor --fix" and item["required"] is False
    assert doctor.fix_skills_link(repo) == ("junction" if WINDOWS else "symlink")
    assert doctor.skills_link(repo) == "ok" and (link / "maapilot-daily" / "SKILL.md").exists()
    if not WINDOWS:                                                       # 目录联接只能写绝对路径
        assert os.readlink(link) == os.path.join("..", ".agents", "skills")    # 相对链接，仓库挪了也还能用
    assert doctor.check_skills_link(None)["ok"]


def test_skills_link_missing_and_broken(repo):
    assert doctor.skills_link(repo) == "missing"
    (repo / ".claude" / "skills").symlink_to(repo / "nowhere")
    assert doctor.skills_link(repo) == "broken"
    doctor.fix_skills_link(repo)
    assert doctor.skills_link(repo) == "ok"



@pytest.mark.skipif(not WINDOWS, reason="目录联接只有 Windows 有")
def test_skills_link_broken_junction_fixed(repo, tmp_path):
    """联接指向的目录没了（仓库挪过）：算 broken，--fix 先删掉旧联接再建。"""
    import _winapi
    gone = tmp_path / "gone"
    gone.mkdir()
    _winapi.CreateJunction(str(gone), str(repo / ".claude" / "skills"))
    gone.rmdir()
    assert doctor.skills_link(repo) == "broken"
    assert doctor.fix_skills_link(repo) == "junction" and doctor.skills_link(repo) == "ok"


def test_real_repo_skills_link_ok():
    assert doctor.skills_link() == "ok"


# --- Claude Code 的信任：按路径比较 ---------------------------------------------------------------------------------

def test_claude_trusted_compares_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    root = tmp_path / "MaaPilot"
    root.mkdir()
    f = tmp_path / ".claude.json"
    f.write_text(json.dumps({"projects": {str(root.resolve()) + "/": {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    assert doctor.claude_trusted(root) is True
    f.write_text(json.dumps({"projects": {str(tmp_path.resolve()): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    assert doctor.claude_trusted(root) is True                                  # 上级目录信任也算
    f.write_text(json.dumps({"projects": {str(root.resolve()): {"hasTrustDialogAccepted": False}}}), encoding="utf-8")
    assert doctor.claude_trusted(root) is False
    assert doctor.claude_trusted(Path(tmp_path / "missing")) is False
