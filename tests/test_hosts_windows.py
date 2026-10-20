"""agent 宿主在 Windows 上的部分（在哪个系统上都能测：换掉 exe.WINDOWS、which、环境变量、fs 的路径规则）：
优先原生的 .exe、常见安装位置、规则里 Windows 的写法、信任的键写法不同。不跑真的 claude / codex。"""
from __future__ import annotations

import json
import ntpath
import os
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
    assert exe.expand("%APPDATA%/npm/codex.cmd", e) == os.path.normpath("/u/AppData/Roaming/npm/codex.cmd")
    assert exe.expand("%LOCALAPPDATA%/pnpm/codex.cmd", e) is None             # 没这个变量：这个位置不找
    assert exe.expand("/usr/local/bin/codex", e) == os.path.normpath("/usr/local/bin/codex")


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
    """PATH 上没有：到 %APPDATA%\\npm 找到 codex.cmd，再换成包里的 codex.exe。"""
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
    """tools.claude 写错了：报找不到，不悄悄换成 PATH 里的另一个；fallback=True 时可以往下找。"""
    found = {"claude": "/usr/bin/claude", "codex": "/usr/bin/codex"}
    which = found.get
    cfg = {"tools": {"claude": "/gone/claude", "codex": "/gone/codex"}}
    assert hosts.resolve(cfg, "claude", which=which, environ={}) is None
    assert hosts.resolve({}, "claude", which=which, environ={}) == os.path.abspath("/usr/bin/claude")
    assert hosts.resolve(cfg, "codex", which=which, environ={}, fallback=True) == os.path.abspath("/usr/bin/codex")


def test_windows_fallbacks_listed():
    assert r"%APPDATA%\npm\codex.cmd" in hosts.CODEX_FALLBACKS
    assert r"%USERPROFILE%\.local\bin\claude.exe" in hosts.CLAUDE_FALLBACKS
    assert not [f for f in hosts.CODEX_FALLBACKS + hosts.CLAUDE_FALLBACKS if re.match(r"[A-Za-z]:\\", f)]  # 不写死盘符


def test_binary_and_codex_path_use_the_same_codex(env, codex, monkeypatch):
    prog, _ = codex
    cfg = {"tools": {"codex": str(prog)}}
    assert hosts.binary(cfg, "codex") == hosts.codex_path(cfg) == str(prog)


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
    for prog in ("maa", "maa.exe", "adb", "adb.exe"):                            # 10-20 起不设规则，按默认
        assert _decide(rules, [prog, "fight", "1-7"]) is None


def test_claude_denies_no_commands():
    """10-20 起 Claude Code 只拒绝森空岛凭据文件，不拒绝任何命令（maa / adb 的 Windows 写法也不再列）。"""
    assert _claude_bash("deny") == []


def test_rules_check_expectations_match_the_rules_file():
    """host install 用 codex execpolicy check 核对的那些命令：项目的规则文件确实给这些结论（不用真的 codex 也核对一遍）。"""
    rules = _codex_rules()
    assert {w for w, _ in host_cmd.EXPECT} == {"allow", "prompt"}                  # 10-20 起没有 forbidden
    assert any("Scripts" in c[0] for _, c in host_cmd.EXPECT)
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
