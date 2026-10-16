"""agent 宿主在 Windows 上的部分（在哪个系统上都能测：换掉 exe.WINDOWS、which、环境变量、fs 的路径规则）：
优先原生的 .exe、常见安装位置、doctor 的提醒和 Codex 沙箱、规则里 Windows 的写法、信任的键写法不同。不跑真的 claude / codex。"""
from __future__ import annotations

import json
import ntpath
import re

import pytest

from maapilot import hosts, paths
from maapilot.oscompat import exe, fs
from maapilot.tools import doctor, host_cmd
from tests.test_daily import _no_real_crontab, env, run  # noqa: F401 - fixture
from tests.test_hosts import _codex_rules, _decide, _use_codex, codex  # noqa: F401 - fixture

ROOT = paths.ROOT


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(exe, "WINDOWS", True)


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    return p


# --- 找程序 ----------------------------------------------------------------------------------------------------------

def test_expand_env_vars():
    e = {"APPDATA": "/u/AppData/Roaming"}
    assert exe.expand("%APPDATA%/npm/codex.cmd", e) == "/u/AppData/Roaming/npm/codex.cmd"
    assert exe.expand("%LOCALAPPDATA%/pnpm/codex.cmd", e) is None             # 没这个变量：这个位置不找
    assert exe.expand("/usr/local/bin/codex", e) == "/usr/local/bin/codex"


def test_shim_and_msix_only_on_windows(win, monkeypatch):
    assert exe.is_shim(r"C:\npm\codex.CMD") and exe.is_shim("x.ps1") and not exe.is_shim(r"C:\x\codex.exe")
    assert exe.in_msix_cache(r"C:\Users\u\AppData\Local\Packages\Python.3.12_x\LocalCache\Roaming\npm\codex.cmd")
    assert not exe.in_msix_cache(r"C:\Users\u\AppData\Roaming\npm\codex.cmd")
    monkeypatch.setattr(exe, "WINDOWS", False)
    assert not exe.is_shim("/x/codex.cmd")


def test_native_codex_inside_npm_package(win, tmp_path, monkeypatch):
    npm = tmp_path / "npm"
    shim = _touch(npm / "codex.cmd")
    assert exe.native_for("codex", shim, {}) is None                               # 包里没有：照旧用包装
    base = npm / "node_modules" / "@openai" / "codex" / "vendor"
    x64 = _touch(base / "x86_64-pc-windows-msvc" / "codex" / "codex.exe")
    arm = _touch(base / "aarch64-pc-windows-msvc" / "codex" / "codex.exe")
    monkeypatch.setattr(exe.platform, "machine", lambda: "AMD64")
    assert exe.native_for("codex", shim, {}) == str(x64)
    monkeypatch.setattr(exe.platform, "machine", lambda: "ARM64")
    assert exe.native_for("codex", shim, {}) == str(arm)
    assert exe.native_for("codex", x64, {}) is None                                # 已经是原生的
    sib = _touch(npm / "codex.exe")                                                 # 同目录的 .exe 最优先
    assert exe.native_for("codex", shim, {}) == str(sib)


def test_native_claude_from_installer(win, tmp_path):
    shim = _touch(tmp_path / "npm" / "claude.cmd")
    home = tmp_path / "home"
    assert exe.native_for("claude", shim, {"USERPROFILE": str(home)}) is None
    native = _touch(home / ".local" / "bin" / "claude.exe")
    assert exe.native_for("claude", shim, {"USERPROFILE": str(home)}) == str(native)


def test_resolve_windows_fallbacks_prefer_native(win, tmp_path, monkeypatch):
    """定时任务里 PATH 很短：到 %APPDATA%\\npm 找到 codex.cmd，再换成包里的 codex.exe。"""
    appdata = tmp_path / "Roaming"
    shim = _touch(appdata / "npm" / "codex.cmd")
    native = _touch(appdata / "npm" / "node_modules" / "@openai" / "codex" / "vendor" / "x86_64-pc-windows-msvc"
                    / "codex" / "codex.exe")
    asked = []

    def which(c):
        asked.append(c)
        return str(shim) if c == str(appdata) + r"\npm\codex.cmd" else None      # macOS 上反斜杠不算分隔符，按原样认
    monkeypatch.setattr(exe.platform, "machine", lambda: "x86_64")
    environ = {"APPDATA": str(appdata)}
    assert hosts.resolve({}, "codex", which=which, environ=environ) == str(native)
    assert asked[0] == "codex" and not any("%" in a for a in asked)                # 没设的 %LOCALAPPDATA% 等跳过
    assert hosts.resolve({"tools": {"codex": "/x/codex"}}, "codex", which=lambda c: None, environ={}) is None


def test_configured_tool_is_not_swapped_silently(monkeypatch):
    """tools.claude 写错了：无头 agent 报"起不来"，不悄悄换成 PATH 里的另一个；推消息用的 codex 可以往下找。"""
    found = {"claude": "/usr/bin/claude", "codex": "/usr/bin/codex"}
    which = found.get
    cfg = {"tools": {"claude": "/gone/claude", "codex": "/gone/codex"}}
    assert hosts.resolve(cfg, "claude", which=which, environ={}) is None
    assert hosts.resolve({}, "claude", which=which, environ={}) == "/usr/bin/claude"
    assert hosts.resolve(cfg, "codex", which=which, environ={}, fallback=True) == "/usr/bin/codex"


def test_windows_fallbacks_listed():
    assert r"%APPDATA%\npm\codex.cmd" in hosts.CODEX_FALLBACKS
    assert r"%USERPROFILE%\.local\bin\claude.exe" in hosts.CLAUDE_FALLBACKS
    assert not [f for f in hosts.CODEX_FALLBACKS + hosts.CLAUDE_FALLBACKS if re.match(r"[A-Za-z]:\\", f)]  # 不写死盘符


def test_agent_and_queue_use_the_same_codex(env, codex, monkeypatch):
    prog, _ = codex
    cfg = {"agent": "codex", "tools": {"codex": str(prog)}}
    assert hosts.binary(cfg) == hosts.codex_path(cfg) == str(prog)


# --- doctor --------------------------------------------------------------------------------------------------------

class _Probe(doctor.Probe):
    def __init__(self, found=None, help_=None):
        self.found, self.help, self.calls = found, help_ or {}, []

    def which(self, prog):
        return self.found

    def run(self, argv, timeout=15, env=None):
        self.calls.append(argv[1:])
        key = " ".join(argv[1:])
        if key not in self.help:
            return doctor.Proc(1, b"", b"error: unrecognized subcommand")
        return doctor.Proc(0, self.help[key].encode(), b"")


def _check(probe, host="claude"):
    ctx = doctor.Ctx(False, probe)
    ctx.cfg = {"agent": host}
    return doctor.check_agent(ctx)


def test_doctor_warns_about_shim_and_msix(win, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "claude_trusted", lambda root: True)
    shim = _touch(tmp_path / "npm" / "claude.cmd")
    item = _check(_Probe(str(shim)))
    assert item["ok"] is True and item["path"] == str(shim) and "npm 的包装" in item["warnings"][0]
    msix = _touch(tmp_path / "Packages" / "Node_x" / "LocalCache" / "Roaming" / "npm" / "claude.exe")
    item = _check(_Probe(str(msix)))
    assert item["ok"] is True and "定时任务看不见" in item["detail"] and len(item["warnings"]) == 1
    ps1 = _touch(tmp_path / "npm" / "claude.ps1")
    item = _check(_Probe(str(ps1)))
    assert item["ok"] is False and item["required"] is False and "PowerShell" in item["detail"]
    native = _touch(tmp_path / "bin" / "claude.exe")
    assert "warnings" not in _check(_Probe(str(native)))


EXEC_HELP = "  -s, --sandbox <SANDBOX_MODE>\n          [possible values: read-only, workspace-write, danger-full-access]"
SANDBOX_NEW = "Commands:\n  uninstall  Remove the legacy Windows sandbox's machine-wide accounts\n"
SANDBOX_OLD = "Commands:\n  macos  Run a command under Seatbelt\n  linux  Run a command under Landlock\n"


def test_codex_sandbox_probe(win, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "codex_trusted", lambda root: True)
    prog = str(_touch(tmp_path / "bin" / "codex.exe"))
    ok = _Probe(prog, {"--version": "codex-cli 0.162.1\n", "exec --help": EXEC_HELP, "sandbox --help": SANDBOX_NEW})
    item = _check(ok, "codex")
    assert item["ok"] is True and item["sandbox"]["supported"] is True and item["sandbox"]["version"] == "codex-cli 0.162.1"
    assert all(c[0] in ("--version", "exec", "sandbox") and c[-1] in ("--version", "--help") for c in ok.calls)  # 只看帮助
    old = _Probe(prog, {"--version": "codex-cli 0.20.0\n", "exec --help": EXEC_HELP, "sandbox --help": SANDBOX_OLD})
    item = _check(old, "codex")
    assert item["ok"] is False and item["required"] is True and item["sandbox"]["supported"] is False
    assert "不要改成 danger-full-access" in item["fix"]
    item = _check(_Probe(prog), "codex")                                            # 帮助跑不出来：只提醒
    assert item["ok"] is True and item["sandbox"]["supported"] is None and item["warnings"]


def test_codex_sandbox_not_probed_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(exe, "WINDOWS", False)
    monkeypatch.setattr(doctor, "codex_trusted", lambda root: True)
    probe = _Probe("/x/codex")
    item = _check(probe, "codex")
    assert item["ok"] is True and "sandbox" not in item and probe.calls == []


def test_sandbox_supported_from_help():
    assert doctor.sandbox_supported(EXEC_HELP, SANDBOX_NEW) is True
    assert doctor.sandbox_supported(EXEC_HELP, SANDBOX_OLD) is False
    assert doctor.sandbox_supported("--sandbox <MODE> [possible values: read-only]", SANDBOX_NEW) is False
    assert doctor.sandbox_supported(None, SANDBOX_NEW) is None


def test_codex_command_never_bypasses_sandbox(win, tmp_path):
    for stdin in (False, True):
        argv = hosts.command("codex", r"C:\x\codex.exe", "做事", tmp_path / "a.jsonl", stdin=stdin)
        assert argv[argv.index("--sandbox") + 1] == "workspace-write"
        assert "danger-full-access" not in argv and "--dangerously-bypass-approvals-and-sandbox" not in argv


# --- 规则里 Windows 的写法 --------------------------------------------------------------------------------------------

MAAPILOT_FORMS = ["maapilot", ".venv/bin/maapilot", "maapilot.exe", ".venv\\Scripts\\maapilot.exe", ".venv/Scripts/maapilot.exe"]


def _claude_bash(kind):
    perms = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]
    out = []
    for r in perms[kind]:
        m = re.fullmatch(r"Bash\((.+?)(?::\*)?\)", r)
        if m:
            out.append(m.group(1).split())
    return out


def test_codex_rules_cover_every_maapilot_form():
    """Codex 没有 PATH 里的 maapilot 时写 .venv 里的：每种写法放行 / 询问 / 禁止都和本名一样。"""
    rules = _codex_rules()
    want = {"allow": "allow", "ask": "prompt", "deny": "forbidden"}
    for kind, dec in want.items():
        for cmd in _claude_bash(kind):
            if cmd[0] != "maapilot":
                continue
            for form in MAAPILOT_FORMS:
                assert _decide(rules, [form, *cmd[1:]]) == dec, f"{form} {' '.join(cmd[1:])}：应是 {dec}"
    for prog in ("maa", "maa.exe"):
        assert _decide(rules, [prog, "fight", "1-7"]) == "forbidden"
        assert _decide(rules, [prog, "install"]) is None                          # setup 装 maa-cli：照常问
    for prog in ("adb", "adb.exe"):
        assert _decide(rules, [prog, "-s", "emu:1", "shell", "input"]) == "forbidden"


def test_claude_denies_windows_forms():
    """Claude Code 的放行只列本名（启动钩子放好了 PATH，别的写法按默认弹确认）；禁止的要连 Windows 的写法一起列。"""
    deny = [" ".join(c) for c in _claude_bash("deny")]
    for form in MAAPILOT_FORMS:
        assert f"{form} skland token" in deny
    for prog in ("maa", "maa.exe"):
        for sub in ("run", "fight", "copilot", "ssscopilot", "roguelike", "reclamation", "startup", "closedown"):
            assert f"{prog} {sub}" in deny
    for prog in ("adb", "adb.exe"):
        assert f"{prog} shell" in deny and f"{prog} -s" in deny


def test_rules_check_expectations_match_the_rules_file():
    """host install 用 codex execpolicy check 核对的那些命令：项目的规则文件确实给这些结论（不用真的 codex 也核对一遍）。"""
    rules = _codex_rules()
    assert {w for w, _ in host_cmd.EXPECT} == {"allow", "prompt", "forbidden"}
    assert any("Scripts" in c[0] for _, c in host_cmd.EXPECT) and any(c[0] == "adb.exe" for _, c in host_cmd.EXPECT)
    for want, cmd in host_cmd.EXPECT:
        assert _decide(rules, cmd) == want, cmd


# --- 信任：键的写法不同（Windows 的大小写、分隔符） ----------------------------------------------------------------------

def _windows_spelling(p) -> str:
    return str(p).upper().replace("/", "\\")


def test_codex_trust_key_in_windows_spelling(capsys, env, codex, monkeypatch):
    prog, _ = codex
    _use_codex(prog)
    monkeypatch.setattr(fs, "_path", ntpath)
    cfgfile = host_cmd.codex_home() / "config.toml"
    cfgfile.parent.mkdir(parents=True)
    cfgfile.write_text(f'[projects.{json.dumps(_windows_spelling(ROOT))}]\ntrust_level = "trusted"\n', encoding="utf-8")
    assert host_cmd.codex_trusted(ROOT) is True
    code, out = run(capsys, "host", "install", "codex")
    assert code == 0 and out["data"]["changed"] is False and cfgfile.read_text(encoding="utf-8").count("[projects") == 1
    cfgfile.write_text(f'[projects.{json.dumps(_windows_spelling(ROOT))}]\ntrust_level = "untrusted"\n', encoding="utf-8")
    code, out = run(capsys, "host", "install", "codex")                             # 用户选过不信任：不另加一段
    assert code == 2 and cfgfile.read_text(encoding="utf-8").count("[projects") == 1


def test_claude_trust_key_in_windows_spelling(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "_path", ntpath)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    repo = (tmp_path / "Repo").resolve()
    (tmp_path / ".claude.json").write_text(json.dumps(
        {"projects": {_windows_spelling(repo): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    assert doctor.claude_trusted(repo) is True
