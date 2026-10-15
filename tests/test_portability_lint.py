"""跨系统的写法只在 maapilot/oscompat/ 里：别处直接用 Unix 专有的进程调用、os.replace、不写编码的文本子进程，测试就失败。"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "maapilot"
# 只有 macOS / Linux 有，或者在 Windows 上行为不一样
BANNED_ATTRS = {("os", "killpg"), ("os", "getpgrp"), ("os", "getpgid"), ("os", "setsid"), ("os", "fork"),
                ("signal", "SIGHUP"), ("signal", "SIGKILL"), ("os", "replace")}
SUBPROCESS = {"run", "Popen", "check_output", "check_call", "call"}


def _sources():
    for f in sorted(ROOT.rglob("*.py")):
        if "oscompat" in f.relative_to(ROOT).parts:
            continue
        yield f, ast.parse(f.read_text(encoding="utf-8"), filename=str(f))


def _where(f, node):
    return f"{f.relative_to(ROOT.parent)}:{node.lineno}"


def test_no_posix_only_calls_outside_oscompat():
    bad = []
    for f, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and (node.value.id, node.attr) in BANNED_ATTRS:
                bad.append(f"{_where(f, node)} {node.value.id}.{node.attr}")
            elif isinstance(node, ast.keyword) and node.arg in ("start_new_session", "preexec_fn"):
                bad.append(f"{_where(f, node)} {node.arg}=")
    assert not bad, "改用 maapilot.oscompat（procs / fs）：\n" + "\n".join(bad)


def test_text_subprocess_has_encoding():
    bad = []
    for f, tree in _sources():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                    and node.func.attr in SUBPROCESS):
                continue
            kw = {k.arg: k.value for k in node.keywords}
            texty = any(isinstance(kw.get(k), ast.Constant) and kw[k].value is True
                        for k in ("text", "universal_newlines"))
            if texty and "encoding" not in kw:
                bad.append(_where(f, node))
    assert not bad, "文本模式的子进程要写 encoding（或用 oscompat.procs.run_text / spawn）：\n" + "\n".join(bad)
