"""定时后端（oscompat.sched）、定时入口（oscompat.launch）、各系统的提示（oscompat.hints）、doctor 的 .claude/skills 链接项。
crontab 用假脚本（MAAPILOT_CRONTAB）；任务计划程序在各系统上都用假的 schtasks 脚本（MAAPILOT_SCHTASKS）+ 改 WINDOWS 标志测，
任务定义的生成 / 解析是纯函数；真的 schtasks 只在 Windows 上建一个测试用的任务再删掉。"""
import codecs
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from maapilot import cli, config, paths
from maapilot.flows import daily
from maapilot.flows.daily import cron as dcron
from maapilot.oscompat import hints, launch, sched, venv
from maapilot.tools import doctor
from tests.portable import WINDOWS, fake_program, needs_cron, sched_ready  # noqa: F401 - fixture
from tests.test_daily import _no_real_crontab, fake_crontab, OTHER  # noqa: F401 - fixture


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def cfg():
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\nschedule: ['04:40', '16:40']\n", encoding="utf-8")


# --- cron 后端 -------------------------------------------------------------------------------------------------------

@needs_cron
def test_cron_backend_round_trip(tmp_path, monkeypatch, cfg, sched_ready):
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, OTHER)))
    b = sched.backend()
    assert isinstance(b, sched.CronBackend) and b.available() == (True, None)
    cmd = dcron.command({"emulator": {"address": "emu:1"}})
    r = b.install([(4, 40), (16, 40)], cmd)
    assert r["changed"] and len(r["installed"]) == 2 and r["old_entries"] == [OTHER.splitlines()[1]]
    entries = b.read()
    mine = [e for e in entries if e.mine]
    assert [e.times for e in mine] == [["04:40"], ["16:40"]] and [e.ident for e in entries if e.old] == r["old_entries"]
    line = mine[0].ident
    assert line.startswith(f"40 4 * * * {venv.venv_exe('python')} -X utf8 -m maapilot.oscompat.launch --env PATH+=")
    assert line.endswith(f"--log {paths.LOGS / 'schedule.log'} -- daily run --trigger schedule # maapilot-v2 {paths.ROOT}")
    assert ">>" not in line                                               # 日志由启动器写
    assert (paths.LOGS).is_dir()
    assert b.install([(4, 40), (16, 40)], cmd)["changed"] is False        # 重复执行结果相同
    assert b.remove()["removed"] == [e.ident for e in mine]
    assert (tmp_path / "crontab.txt").read_text(encoding="utf-8") == OTHER


def test_launch_path_uses_venv_and_system_dirs(cfg):
    argv = dcron.command({"emulator": {"address": "emu:1"}}).argv
    env = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
    assert env[0].startswith("PATH+=") and env[-1] == "PYTHONUTF8=1"
    dirs = env[0][len("PATH+="):].split(os.pathsep)
    assert dirs[0] == str(venv.venv_bin()) and dirs[-len(venv.sys_path_dirs()):] == venv.sys_path_dirs()
    assert len(dirs) == len(set(dirs))
    assert argv[:4] == [str(venv.venv_exe("python")), "-X", "utf8", "-m"]


def test_cron_old_format_lines_still_ours():
    # 旧版写出的行（环境变量写在行里、.venv/bin/maapilot、>> cron.log）、一行里多个小时：照样认成本项目的，重设时被换掉
    line = (f"40 4,16 * * * PATH=/x PYTHONUTF8=1 {paths.ROOT}/.venv/bin/maapilot daily run --trigger schedule "
            f">> {paths.ROOT}/private/log/cron.log 2>&1 # maapilot-v2 {paths.ROOT}")
    b = sched.CronBackend()
    assert b.mine(line) and b.times_of([line]) == ["04:40", "16:40"]
    assert not b.mine(line.replace(f"# maapilot-v2 {paths.ROOT}", "# maapilot-v2 /elsewhere"))


@needs_cron
def test_reschedule_replaces_old_format_line(capsys, tmp_path, monkeypatch, cfg, sched_ready):
    old = f"40 4 * * * PATH=/x {paths.ROOT}/.venv/bin/maapilot daily run --trigger schedule # maapilot-v2 {paths.ROOT}\n"
    monkeypatch.setenv("MAAPILOT_CRONTAB", str(fake_crontab(tmp_path, monkeypatch, OTHER + old)))
    code, out = run(capsys, "daily", "schedule")
    assert code == 0, out
    text = (tmp_path / "crontab.txt").read_text(encoding="utf-8")
    assert out["data"]["replaced"] == [old.strip()] and old not in text and text.count("maapilot-v2") == 2


@needs_cron
def test_doctor_uses_probe_for_crontab():
    from tests.test_tools import FakeProbe
    line = f"40 4 * * * x daily run --trigger schedule # maapilot-v2 {paths.ROOT}\n*/15 * * * * maapilot job tick\n"
    probe = FakeProbe({"crontab -l": (0, line.encode(), b"")})
    assert doctor.schedule_info(probe) == {"installed": True, "times": ["04:40"], "backend": "cron",
                                           "old_entries": ["*/15 * * * * maapilot job tick"]}
    probe = FakeProbe({"crontab -l": (1, b"", b"crontab: no crontab for x")})
    assert doctor.schedule_info(probe)["installed"] is False
    probe = FakeProbe({"crontab -l": (1, b"", b"permission denied")})
    info = doctor.schedule_info(probe)
    assert info["installed"] is None and "permission denied" in info["error"]
    item = doctor.check_schedule(doctor.Ctx(True, probe))
    assert not item["ok"] and item["required"] is False


# --- 假后端 ---------------------------------------------------------------------------------------------------------

def test_fake_backend(capsys, tmp_path, monkeypatch, cfg, sched_ready):
    store = tmp_path / "sched.json"
    store.write_text(json.dumps([{"times": ["03:00"], "command": "x", "mine": False, "ident": "other", "old": False}]),
                     encoding="utf-8")
    monkeypatch.setenv(sched.SCHEDULER_ENV, f"fake:{store}")
    code, out = run(capsys, "daily", "schedule")
    assert code == 0 and out["data"]["changed"] and out["data"]["times"] == ["04:40", "16:40"]
    assert out["data"]["schedule"]["times"] == ["04:40", "16:40"] and out["data"]["backend"] == "fake"
    code, out = run(capsys, "daily", "status")
    assert out["data"]["schedule"] == {"installed": True, "times": ["04:40", "16:40"], "old_entries": [], "backend": "fake"}
    code, out = run(capsys, "daily", "unschedule")
    assert code == 0 and out["data"]["removed"] == [f"maapilot-daily {paths.ROOT}"]
    assert [e["ident"] for e in json.loads(store.read_text(encoding="utf-8"))] == ["other"]


def test_schedule_fails_loudly_when_read_back_differs(capsys, tmp_path, monkeypatch, cfg, sched_ready):
    """装完读回来的时间不对（系统没记上）：环境错误，不假装设好了。"""
    monkeypatch.setenv(sched.SCHEDULER_ENV, f"fake:{tmp_path / 'sched.json'}")
    monkeypatch.setattr(daily, "schedule_info", lambda: {"installed": True, "times": ["04:40"], "old_entries": []})
    code, out = run(capsys, "daily", "schedule")
    assert code == 5 and out["error"]["code"] == "schedule_not_applied" and "16:40" in out["error"]["message"]
    monkeypatch.setattr(daily, "schedule_info", lambda: {"installed": True, "times": ["04:40", "16:40"], "old_entries": [],
                                                         "enabled": False})
    code, out = run(capsys, "daily", "schedule")
    assert code == 5 and "停用" in out["error"]["message"]


# --- 设定时之前查要执行的程序 -------------------------------------------------------------------------------------------

def test_isolated_paths():
    assert dcron.isolated(r"C:\Users\me\AppData\Local\Packages\OpenAI.Codex_8wekyb3d8bbwe\LocalCache\Local\uv\python.exe")
    assert dcron.isolated("c:/users/me/appdata/local/packages/x/localcache/roaming/npm/claude.cmd")
    assert not dcron.isolated(r"C:\Users\me\AppData\Local\Programs\maa\maa.exe")
    assert not dcron.isolated(r"C:\Users\me\Packages\foo.exe")


def test_schedule_refuses_isolated_or_missing_programs(capsys, tmp_path, monkeypatch, cfg, sched_ready):
    monkeypatch.setenv(sched.SCHEDULER_ENV, f"fake:{tmp_path / 'sched.json'}")
    iso = r"C:\Users\me\AppData\Local\Packages\OpenAI.Codex_x\LocalCache\Local\maa\maa.exe"
    real = dcron.shutil.which
    monkeypatch.setattr(dcron.shutil, "which", lambda p, *a, **k: iso if p == "maa" else real(p, *a, **k))
    code, out = run(capsys, "daily", "schedule")
    assert code == 5 and out["error"]["code"] == "isolated_install" and "maa" in out["error"]["message"]
    assert out["data"]["problems"] == [{"what": "maa", "path": iso, "problem": "isolated"}]
    assert not (tmp_path / "sched.json").exists()                          # 什么都没装
    code, out = run(capsys, "daily", "schedule", "--dry-run")              # 只看：照样给，带上问题
    assert code == 0 and out["data"]["problems"][0]["problem"] == "isolated"
    monkeypatch.setattr(dcron.shutil, "which", lambda p, *a, **k: None if p == "adb" else real(p, *a, **k))
    code, out = run(capsys, "daily", "schedule")
    assert code == 5 and out["error"]["code"] == "program_missing" and "adb" in out["error"]["message"]
    monkeypatch.setattr(dcron.paths, "ROOT", Path(r"C:\Users\me\AppData\Local\Packages\P\LocalCache\Local\MaaPilot"))
    monkeypatch.setattr(dcron.shutil, "which", real)
    probs = dcron.preflight(config.load(), dcron.command(config.load()))
    assert probs[-1]["what"] == "项目文件夹" and probs[-1]["problem"] == "isolated"


def test_doctor_schedule_item_reports_isolated_programs(tmp_path, monkeypatch, cfg, sched_ready):
    from tests.test_tools import FakeProbe
    monkeypatch.setenv(sched.SCHEDULER_ENV, f"fake:{tmp_path / 'sched.json'}")
    sched.FakeBackend(tmp_path / "sched.json").install([(4, 40)], sched.Command(["x"]))
    ctx = doctor.Ctx(True, FakeProbe({}))
    ctx.cfg = config.load()
    assert doctor.check_schedule(ctx)["ok"]
    iso = r"C:\Users\me\AppData\Local\Packages\P\LocalCache\Roaming\npm\claude.cmd"
    ctx = doctor.Ctx(True, FakeProbe({}, which={"claude": iso}))
    ctx.cfg = config.load()
    item = doctor.check_schedule(ctx)
    assert not item["ok"] and item["problems"][0]["what"] == "claude" and "隔离目录" in item["detail"]


# --- 设置：补跑、唤醒 -----------------------------------------------------------------------------------------------

def test_schedule_options(capsys, tmp_path, monkeypatch, sched_ready):
    assert config.schedule_options({}) == {"run_if_missed": True, "wake_computer": False}
    assert config.schedule_options({"schedule_options": {"wake_computer": True}}) == {"run_if_missed": True,
                                                                                    "wake_computer": True}
    paths.PRIVATE.mkdir(parents=True, exist_ok=True)
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\nschedule_options: {run_if_missed: 'yes'}\n", encoding="utf-8")
    monkeypatch.setenv(sched.SCHEDULER_ENV, f"fake:{tmp_path / 'sched.json'}")
    code, out = run(capsys, "daily", "schedule")
    assert code == 2 and out["error"]["code"] == "invalid_config" and "run_if_missed" in out["error"]["message"]
    paths.CONFIG.write_text("emulator: {address: 'emu:1'}\nschedule_options: {run_if_missed: false, wake_computer: true}\n",
                            encoding="utf-8")
    code, out = run(capsys, "daily", "schedule")
    assert code == 0 and out["data"]["options"] == {"run_if_missed": False, "wake_computer": True}


# --- 任务计划程序：任务定义（纯函数） ------------------------------------------------------------------------------------

ARGV = [r"C:\My Repo\.venv\Scripts\pythonw.exe", "-X", "utf8", "-m", "maapilot.oscompat.launch",
        "--env", r"PATH+=C:\My Repo\.venv\Scripts;C:\Program Files\maa", "--env", 'CODEX_HOME=C:\\x "y"',
        "--log", r"C:\My Repo\private\log\schedule.log", "--", "daily", "run", "--trigger", "schedule"]
OPTS = {"description": r"maapilot-daily C:\My Repo", "workdir": r"C:\My Repo", "run_if_missed": True, "wake": False,
        "user": r"PC\me", "now": datetime(2026, 10, 16, 5, 0)}


def test_build_parse_round_trip():
    xml = sched.build_xml([(4, 40), (16, 40)], ARGV, OPTS)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    assert "<StartBoundary>2026-10-17T04:40:00</StartBoundary>" in xml          # 04:40 今天已经过了：从明天起
    assert "<StartBoundary>2026-10-16T16:40:00</StartBoundary>" in xml
    for s in ("<LogonType>InteractiveToken</LogonType>", "<RunLevel>LeastPrivilege</RunLevel>", r"<UserId>PC\me</UserId>",
              "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", "<ExecutionTimeLimit>PT6H</ExecutionTimeLimit>",
              "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
              "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>", "<StartWhenAvailable>true</StartWhenAvailable>",
              "<WakeToRun>false</WakeToRun>", "<DaysInterval>1</DaysInterval>"):
        assert s in xml, s
    info = sched.parse_xml(xml)
    assert info == {"description": OPTS["description"], "times": ["04:40", "16:40"], "command": ARGV[0],
                    "arguments": subprocess.list2cmdline(ARGV[1:]), "workdir": r"C:\My Repo", "enabled": True,
                    "run_if_missed": True, "wake": False}
    assert '"CODEX_HOME=C:\\x \\"y\\""' in info["arguments"] and '"PATH+=C:\\My Repo' in info["arguments"]
    # schtasks /Query /XML 给的字节：UTF-16（带 BOM）、或者 UTF-8 都读得懂
    assert sched.parse_xml(codecs.BOM_UTF16_LE + xml.encode("utf-16-le")) == info
    assert sched.parse_xml(xml.encode("utf-8")) == info
    other = sched.parse_xml(sched.build_xml([(3, 0)], ["a.exe"], {**OPTS, "run_if_missed": False, "wake": True}))
    assert other["run_if_missed"] is False and other["wake"] is True and other["arguments"] == ""


def test_parse_xml_skips_disabled_and_non_daily_triggers():
    xml = sched.build_xml([(4, 40), (16, 40)], ARGV, OPTS)
    xml = xml.replace("<Enabled>true</Enabled>\n      <ScheduleByDay>", "<Enabled>false</Enabled>\n      <ScheduleByDay>", 1)
    xml = xml.replace("<Enabled>true</Enabled>\n    <Hidden>", "<Enabled>false</Enabled>\n    <Hidden>")
    info = sched.parse_xml(xml)
    assert info["times"] == ["16:40"] and info["enabled"] is False
    weekly = xml.replace("<ScheduleByDay>", "<ScheduleByWeek>").replace("</ScheduleByDay>", "</ScheduleByWeek>")
    assert sched.parse_xml(weekly)["times"] == []
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


def test_gui_python(tmp_path):
    (tmp_path / "python.exe").write_bytes(b"")
    assert sched.SchTasksBackend.gui_python(str(tmp_path / "python.exe")) == str(tmp_path / "python.exe")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert sched.SchTasksBackend.gui_python(str(tmp_path / "python.exe")) == str(tmp_path / "pythonw.exe")
    assert sched.SchTasksBackend.gui_python("/usr/bin/python3") == "/usr/bin/python3"


# --- 任务计划程序：经假的 schtasks 跑全套命令 ------------------------------------------------------------------------------

FAKE_SCHTASKS = r'''
import codecs, json, os, sys
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
elif a[0] == "/Create":
    n, src = a[a.index("/TN") + 1], a[a.index("/XML") + 1]
    data = open(src, "rb").read()
    if not data.startswith(codecs.BOM_UTF16_LE) or "/F" not in a:
        sys.exit(3)
    open(path(n), "wb").write(data)
    out.write("成功: 成功创建计划任务。\r\n".encode("gbk"))
elif a[0] == "/Delete":
    os.remove(path(a[a.index("/TN") + 1]))
else:
    sys.exit(2)
'''


@pytest.fixture
def windows(tmp_path, monkeypatch, cfg, sched_ready):
    """当成 Windows：调度用任务计划程序，schtasks 是假脚本（任务定义存在一个目录里）。"""
    monkeypatch.setattr(sched, "WINDOWS", True)
    store = tmp_path / "schtasks"
    monkeypatch.setenv("FAKE_SCHTASKS_DIR", str(store))
    monkeypatch.setenv(sched.SCHTASKS_ENV, str(fake_program(tmp_path / "fake-schtasks", FAKE_SCHTASKS)))
    return store


def _calls(store: Path) -> list[list[str]]:
    return [json.loads(ln) for ln in (store / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


def _foreign(store: Path, root: str = r"D:\Other\MaaPilot") -> str:
    """别的目录的 MaaPilot 装的任务。"""
    name = sched.task_name(root)
    xml = sched.build_xml([(5, 0)], ["x.exe", "-m", "y"], {"description": f"maapilot-daily {root}", "workdir": root})
    store.mkdir(parents=True, exist_ok=True)
    (store / (name.strip("\\").replace("\\", "__") + ".xml")).write_bytes(codecs.BOM_UTF16_LE + xml.encode("utf-16-le"))
    return name


def test_windows_schedule_status_unschedule(capsys, windows):
    other = _foreign(windows)
    b = sched.backend()
    assert isinstance(b, sched.SchTasksBackend) and b.available() == (True, None)
    code, out = run(capsys, "daily", "schedule", "--dry-run")
    assert code == 0 and out["data"]["backend"] == "schtasks" and "<Exec>" in out["data"]["preview"]
    assert not [c for c in _calls(windows) if c[0] in ("/Create", "/Delete")]
    code, out = run(capsys, "daily", "schedule")
    assert code == 0, out
    d = out["data"]
    name = sched.task_name(paths.ROOT)
    assert d["installed"] == [name] and d["changed"] and d["old_entries"] == [other] and d["removed_old"] == []
    assert d["schedule"] == {"installed": True, "times": ["04:40", "16:40"], "old_entries": [other], "backend": "schtasks",
                             "task": name, "settings": {"run_if_missed": True, "wake": False}}
    assert d["next_at"] and set(d["exec"]) == {"maa", "claude", "adb"}
    create = [c for c in _calls(windows) if c[0] == "/Create"]
    assert create == [["/Create", "/TN", name, "/XML", create[0][4], "/F"]] and not Path(create[0][4]).exists()  # 临时文件删了
    info = sched.parse_xml((windows / (name.strip("\\").replace("\\", "__") + ".xml")).read_bytes())
    assert info["workdir"] == str(paths.ROOT) and info["description"] == f"maapilot-daily {paths.ROOT}"
    assert "maapilot.oscompat.launch" in info["arguments"] and "--log" in info["arguments"]
    # 再跑一次：没变就不重建
    n = len(_calls(windows))
    code, out = run(capsys, "daily", "schedule")
    assert code == 0 and out["data"]["changed"] is False
    assert not [c for c in _calls(windows)[n:] if c[0] in ("/Create", "/Delete")]
    # 换时间：重建同一个任务，不多出一个
    code, out = run(capsys, "daily", "schedule", "--times", "05:10")
    assert code == 0 and out["data"]["replaced"] == [name] and out["data"]["schedule"]["times"] == ["05:10"]
    assert sorted(p.name for p in windows.glob("*.xml")) == sorted(
        x.strip("\\").replace("\\", "__") + ".xml" for x in (name, other))
    code, out = run(capsys, "daily", "status")
    assert code == 0 and out["data"]["schedule"]["times"] == ["05:10"] and out["data"]["next_at"]
    # doctor（经 Probe 跑同一个假 schtasks）、启动检查
    paths.SETUP.write_text("{}", encoding="utf-8")
    code, out = run(capsys, "doctor", "--brief")
    assert code == 0 and out["data"]["schedule"] == {"installed": True, "times": ["05:10"]}
    item = doctor.check_schedule(doctor.Ctx(True, doctor.PROBE))
    assert item["ok"] and "保持登录" in item["detail"] and item["old_entries"] == [other]
    # 取消：只删自己的
    code, out = run(capsys, "daily", "unschedule", "--dry-run")
    assert code == 0 and out["data"]["removed"] == [name] and (windows / (name.strip("\\").replace("\\", "__") + ".xml")).exists()
    code, out = run(capsys, "daily", "unschedule")
    assert code == 0 and out["data"]["removed"] == [name] and out["data"]["old_entries"] == [other]
    deletes = [c for c in _calls(windows) if c[0] == "/Delete"]
    assert deletes == [["/Delete", "/TN", name, "/F"]]
    code, out = run(capsys, "daily", "status")
    assert out["data"]["schedule"]["installed"] is False and out["data"]["next_at"] is None


def test_windows_replace_old_and_reset(capsys, windows):
    other = _foreign(windows)
    code, out = run(capsys, "daily", "schedule", "--replace-old")
    assert code == 0 and out["data"]["removed_old"] == [other] and out["data"]["schedule"]["old_entries"] == []
    code, out = run(capsys, "reset", "--dry-run")
    assert code == 0 and out["data"]["schedule"]["remove"] == [sched.task_name(paths.ROOT)]
    code, out = run(capsys, "reset")
    assert code == 0 and out["data"]["schedule"]["removed"] == [sched.task_name(paths.ROOT)]
    assert not list(windows.glob("*.xml"))


def test_windows_disabled_task_reported(capsys, windows):
    code, out = run(capsys, "daily", "schedule")
    assert code == 0
    f = next(windows.glob("*.xml"))
    xml = sched.decode(f.read_bytes()).lstrip("\ufeff").replace("<Enabled>true</Enabled>\n    <Hidden>",
                                                               "<Enabled>false</Enabled>\n    <Hidden>")
    f.write_bytes(codecs.BOM_UTF16_LE + xml.encode("utf-16-le"))
    code, out = run(capsys, "daily", "status")
    assert out["data"]["schedule"]["enabled"] is False and out["data"]["next_at"] is None
    item = doctor.check_schedule(doctor.Ctx(True, doctor.PROBE))
    assert not item["ok"] and "停用" in item["detail"]
    paths.SETUP.write_text("{}", encoding="utf-8")
    code, out = run(capsys, "doctor", "--brief")
    assert out["data"]["schedule"]["enabled"] is False and any("停用" in l for l in out["data"]["lines"])
    code, out = run(capsys, "daily", "schedule")                         # 重设：启用回来
    assert code == 0 and out["data"]["changed"] and "enabled" not in out["data"]["schedule"]


def test_windows_schtasks_errors(capsys, windows, monkeypatch, tmp_path):
    monkeypatch.setenv(sched.SCHTASKS_ENV, str(fake_program(tmp_path / "broken", "import sys\nsys.exit(5)\n")))
    code, out = run(capsys, "daily", "status")
    assert code == 0 and out["data"]["schedule"]["installed"] is None and "退出码 5" in out["data"]["schedule"]["error"]
    code, out = run(capsys, "daily", "schedule")
    assert code == 5 and out["error"]["code"] == "schtasks_failed"


def test_windows_without_schtasks(capsys, cfg, monkeypatch, tmp_path):
    """找不到 schtasks.exe：设不了定时（环境错误），别的命令照常，当作没设定时。"""
    monkeypatch.setattr(sched, "WINDOWS", True)
    b = sched.backend()
    assert isinstance(b, sched.Unavailable) and "任务计划程序" in b.available()[1]
    for argv in (("daily", "schedule"), ("daily", "unschedule")):
        code, out = run(capsys, *argv)
        assert code == 5 and out["error"]["code"] == "no_scheduler"
    code, out = run(capsys, "daily", "status")
    assert code == 0 and out["data"]["schedule"]["installed"] is False and out["data"]["next_at"] is None
    paths.SETUP.write_text("{}", encoding="utf-8")
    code, out = run(capsys, "doctor", "--brief")
    assert code == 0 and out["data"]["schedule"]["unavailable"]
    assert any(l.startswith("定时日常这台电脑上设不了") for l in out["data"]["lines"])
    item = doctor.check_schedule(doctor.Ctx(True, doctor.PROBE))
    assert not item["ok"] and item["required"] is False and item["unavailable"] is True


@pytest.mark.skipif(not WINDOWS, reason="真的任务计划程序只有 Windows 有")
def test_real_schtasks_round_trip(tmp_path, monkeypatch):
    """真的 schtasks：在单独的文件夹（\\MaaPilotTest\\）里按一个假的仓库路径建任务、读回来、再删掉；不碰真装的。"""
    monkeypatch.delenv(sched.SCHTASKS_ENV)
    b = sched.SchTasksBackend(folder="\\MaaPilotTest\\", root=tmp_path / "fake-root")
    assert b.available() == (True, None)
    cmd = sched.Command([sys.executable, "-c", "pass"], tmp_path / "log" / "x.log", run_if_missed=False, wake=False)
    try:
        r = b.install([(3, 7), (15, 7)], cmd)
        assert r["changed"] and r["installed"] == [b.task()]
        mine = [e for e in b.read() if e.mine]
        assert len(mine) == 1 and mine[0].times == ["03:07", "15:07"] and mine[0].enabled is True
        assert mine[0].settings == {"run_if_missed": False, "wake": False}
        assert b.install([(3, 7), (15, 7)], cmd)["changed"] is False
        assert b.remove()["removed"] == [b.task()]
        assert not [e for e in b.read() if e.mine]
    finally:
        subprocess.run([b.program(), "/Delete", "/TN", b.task(), "/F"], capture_output=True)


# --- 定时入口（launch） -----------------------------------------------------------------------------------------------

def test_launch_parse_and_env():
    sets, prepend, log, rest = launch.parse(["--env", "A=1", "--env", "B=x=y", "--env", f"PATH+=/p{os.pathsep}/q",
                                             "--log", "/l.log", "--", "daily", "run", "--env", "C=3"])
    assert sets == [("A", "1"), ("B", "x=y")] and prepend == ["/p", "/q"] and log == "/l.log"
    assert rest == ["daily", "run", "--env", "C=3"]
    for bad in (["daily"], ["--env", "nokey", "--", "x"], ["--log"], ["--env", "A=1"]):
        with pytest.raises(ValueError):
            launch.parse(bad)
    env = {"PATH": os.pathsep.join(["/q", "/usr/bin"])}
    launch.apply_env(sets, prepend, env)
    assert env == {"PATH": os.pathsep.join(["/p", "/q", "/usr/bin"]), "A": "1", "B": "x=y", "PYTHONUTF8": "1"}


def test_launch_end_to_end(tmp_path):
    """真的子进程：设好 MAAPILOT_PRIVATE、PATH，输出（连同 maapilot 的 JSON）追加进日志，退出码照搬。"""
    priv = tmp_path / "p2"
    log = tmp_path / "logs" / "deep" / "schedule.log"
    log.parent.parent.mkdir()
    log.parent.mkdir()
    log.write_text("旧的一行\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("MAAPILOT_PRIVATE", "PYTHONUTF8")}
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "maapilot.oscompat.launch", "--env", f"MAAPILOT_PRIVATE={priv}",
                        "--env", f"PATH+={tmp_path / 'bin'}", "--log", str(log), "--", "no-such-command"],
                       capture_output=True, env=env, cwd=str(paths.ROOT), timeout=60)
    assert p.returncode == 2 and p.stdout == b"" and p.stderr == b""                 # 全进了日志
    text = log.read_text(encoding="utf-8")
    assert text.startswith("旧的一行\n=== ") and "定时启动" in text and '"bad_arguments"' in text and "退出码 2" in text
    assert list((priv / "log").glob("*.jsonl"))                                       # MAAPILOT_PRIVATE 生效了
    p = subprocess.run([sys.executable, "-m", "maapilot.oscompat.launch", "daily"], capture_output=True, env=env,
                       cwd=str(paths.ROOT), timeout=60)
    assert p.returncode == 2 and "--" in p.stderr.decode("utf-8", "replace")


# --- 提示 -----------------------------------------------------------------------------------------------------------

def test_hints_per_os(monkeypatch):
    monkeypatch.setattr(hints, "WINDOWS", False)
    monkeypatch.setattr(hints, "MACOS", True)
    assert hints.install_hint("maa").startswith("brew ") and 16384 in hints.common_ports()
    monkeypatch.setattr(hints, "WINDOWS", True)
    monkeypatch.setattr(hints, "MACOS", False)
    assert "winget" in hints.install_hint("maa") and "brew" not in hints.install_hint("adb")
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
