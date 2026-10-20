"""`maapilot host status / install <claude|codex>`：agent 宿主的准备情况与安装（见 maapilot.hosts）。契约见 docs/cli.md「host」。

install codex：在 Codex 的用户配置（$CODEX_HOME/config.toml，默认 ~/.codex/config.toml）里信任这个文件夹——
不信任时 Codex 不加载项目的 `.codex/rules/`，定时日常叫起的 codex 既用不了放行的 maapilot，也拦不住直接跑 adb / maa。
只追加一段 `[projects."<仓库路径>"]`，别的不动；再用 `codex execpolicy check` 核对规则。改用户的设置，agent 先在对话里问过用户。
install claude：Claude Code 的信任只能在交互里接受，这里只检查、给出做法。
config.yaml 的 `agent`、`tools.<宿主>` 由 agent 按用户说的改（改完要 `daily schedule` 重新记下要执行的程序）。
"""
from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

from maapilot import config, hosts, paths, policy
from maapilot.cliutil import BAD_REQUEST, ENVIRONMENT, CliError
from maapilot.oscompat import fs, procs

RULES = policy.CODEX_RULES                 # 由 maapilot/policy.py 生成（maapilot dev rules）
# 规则文件该给的结论（install 时用 codex execpolicy check 核对；tests/test_policy.py 核对它和 policy 一致）：(结论, 命令)。Windows 的写法（.venv\Scripts\maapilot.exe、
# adb.exe、maa.exe）也核对：规则文件各系统同一份，规则按命令的第一个词认程序
EXPECT = [("allow", ["maapilot", "doctor", "--brief"]),
          ("allow", [".venv/bin/maapilot", "doctor", "--brief"]),
          ("allow", [".venv\\Scripts\\maapilot.exe", "doctor", "--brief"]),
          ("allow", ["maapilot.exe", "daily", "run"]),
          ("prompt", ["maapilot", "guard", "allow", "源石", "1"]),
          ("prompt", [".venv\\Scripts\\maapilot.exe", "guard", "allow", "源石", "1"]),
          ("allow", [".venv/Scripts/maapilot.exe", "daily", "done"])]


def codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env).expanduser() if env else Path.home() / ".codex"


def _codex_config() -> Path:
    return codex_home() / "config.toml"


def _projects(text: str) -> dict:
    p = tomllib.loads(text).get("projects") or {}
    return p if isinstance(p, dict) else {}


def _project_entry(projects: dict, d) -> tuple[str, dict] | None:
    """projects 里指 d 这个文件夹的那一段（Windows 上键的大小写、分隔符可能和我们写的不一样）：(键, 内容)；没有 → None。"""
    if str(d) in projects:
        return str(d), projects[str(d)]
    return next(((k, v) for k, v in projects.items() if fs.same_path(k, d)), None)


def codex_trusted(root: Path) -> bool | None:
    """[projects."<root 或上级>"] trust_level = "trusted"。没有配置文件 → False；读不了 / 写错 → None。"""
    f = _codex_config()
    if not f.exists():
        return False
    try:
        projects = _projects(fs.read_text_utf8(f))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    p = Path(root).resolve()
    for d in (p, *p.parents):
        e = _project_entry(projects, d)
        if e and isinstance(e[1], dict) and e[1].get("trust_level") == "trusted":
            return True
    return False


def _rules_check(binary: str) -> dict:
    """用 codex execpolicy check 核对项目的规则文件：{ok, results: {结论: 实际}}。"""
    rules = paths.ROOT / RULES
    if not rules.exists():
        return {"ok": False, "error": f"没有规则文件 {RULES}"}
    out = {}
    for want, cmd in EXPECT:
        try:
            p = procs.run_text([binary, "execpolicy", "check", "-r", str(rules), *cmd], timeout=30)
            got = json.loads(p.stdout or "{}").get("decision")
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            return {"ok": False, "error": f"codex execpolicy check 没跑成：{e}"}
        out[" ".join(cmd)] = {"want": want, "got": got}
    return {"ok": all(v["want"] == v["got"] for v in out.values()), "results": out}


def status() -> dict:
    cfg = config.load_or_defaults()
    from maapilot.tools import doctor
    out = {"agent": None, "session": hosts.session(), "sessions": hosts.sessions(), "hosts": {}}
    try:
        sp = hosts.spec(cfg)
        out["agent"] = sp["name"]
        if sp["command"]:                                           # 自定义的 agent：照 command 起（没实测过的 harness）
            written, found = hosts.agent_program(cfg, which=doctor.PROBE.which)
            out["agent_command"] = {"command": sp["command"], "prompt": sp["prompt"], "path": found}
    except CliError as e:
        out["agent_error"] = e.message
    for h in hosts.HOSTS:
        path = hosts.resolve(cfg, h, which=doctor.PROBE.which)      # 和定时日常、doctor 找的是同一个
        trusted = codex_trusted(paths.ROOT) if h == "codex" else doctor.claude_trusted(paths.ROOT)
        out["hosts"][h] = {"path": path, "trusted": trusted}
    return out


def install(host: str, dry_run: bool) -> dict:
    cfg = config.load_or_defaults()
    if host == "claude":
        from maapilot.tools import doctor
        t = doctor.claude_trusted(paths.ROOT)
        return {"host": host, "trusted": t, "changed": False,
                "next": None if t else "在这个文件夹里交互地启动一次 claude，接受\"信任这个文件夹\"的提示"}
    binary = hosts.binary(cfg, "codex")
    if not Path(binary).is_absolute():
        raise CliError("no_codex", f"找不到 codex（{binary}）", exit=ENVIRONMENT,
                       hint="先装 Codex；装在别处就在 config.yaml 的 tools.codex 写绝对路径")
    out = {"host": host, "path": binary, "config": str(_codex_config()), "changed": False, "dry_run": dry_run}
    trusted = codex_trusted(paths.ROOT)
    if trusted is None:
        raise CliError("codex_config_unreadable", f"读不了 {_codex_config()}（TOML 写错了？）", exit=ENVIRONMENT,
                       hint="请用户自己看一下这个文件")
    if not trusted:
        text = fs.read_text_utf8(_codex_config()) if _codex_config().exists() else ""
        have = _project_entry(_projects(text), paths.ROOT)
        if have:                                        # 已经有这一段但不是 trusted（用户选过不信任）：不替用户改
            raise CliError("codex_untrusted", f"Codex 设置里这个文件夹是 {have[1]}，不是 trusted",
                           exit=BAD_REQUEST, hint=f"请用户自己在 {_codex_config()} 里改成 trust_level = \"trusted\"")
        block = f'\n[projects.{json.dumps(str(paths.ROOT))}]\ntrust_level = "trusted"\n'
        out["append"] = block.strip()
        if not dry_run:
            _codex_config().parent.mkdir(parents=True, exist_ok=True)
            with open(_codex_config(), "a", encoding="utf-8") as f:
                f.write(("" if not text or text.endswith("\n") else "\n") + block)
            out["changed"] = True
    out["trusted"] = codex_trusted(paths.ROOT) if not dry_run else trusted
    out["rules"] = _rules_check(binary)
    nxt = []
    try:
        agent = hosts.name(cfg)
    except CliError:
        agent = None
    if agent != "codex":
        nxt.append("定时日常要改用 Codex：config.yaml 写 agent: codex、tools.codex: " + binary
                   + "，再 maapilot daily schedule（用户同意后）")
    out["next"] = nxt
    return out


def cmd_status(args):
    return status()


def cmd_install(args):
    return install(args.host, args.dry_run)


def register(sub) -> None:
    g = sub.add_parser("host", help="agent 宿主（Claude Code / Codex）的准备情况与安装")
    gs = g.add_subparsers(dest="sub", metavar="<子命令>")
    p = gs.add_parser("status", help="两个宿主找不找得到、信没信任这个文件夹、记下的用户会话（只读）")
    p.set_defaults(func=cmd_status, command="host status")
    p = gs.add_parser("install", help="让宿主能跑定时日常（codex：在 Codex 设置里信任这个文件夹）")
    p.add_argument("host", choices=hosts.HOSTS)
    p.add_argument("--dry-run", action="store_true", help="只看要改什么，不改")
    p.set_defaults(func=cmd_install, command="host install")
