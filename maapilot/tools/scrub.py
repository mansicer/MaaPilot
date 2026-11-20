"""反馈上传前的兜底脱敏（`maapilot feedback`）：把这台电脑上已知的个人信息从文字里换掉。

agent 写会话记录时自己会脱敏（skill `maapilot-feedback`），这里是第二层：不靠模型判断，按这台机器上的真实值原样匹配——
家目录、项目文件夹、系统用户名、模拟器地址、森空岛的 uid 和昵称、agent 另外指定的字样（`--mask`）；
再加几种认得出形状的：邮箱、IP 地址、Windows 用户目录、长得像 token 的一段。
换成什么都看得出原来是哪一类（`<家目录>`、`<UID>`），排查问题时不丢上下文。第三层是用户在对话里看过再同意。
"""
from __future__ import annotations

import getpass
import os
import re
from pathlib import Path

from maapilot import paths
from maapilot.tools.log_summary import scrub_text

MASKED = "<已隐去>"
USERNAME = "用户名"                        # 这一类按整词匹配（用户名常是别的词的一部分：sicer 之于 sicer.top）
MIN_LITERAL = 3                           # 太短的字样不按原样匹配（会误伤一大片）
_GENERIC_USERS = {"root", "user", "admin", "administrator", "home", "runner", "ubuntu", "users"}

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?(?![\d.])")
_EMULATOR = re.compile(r"\bemulator-\d{4,5}\b")
_WIN_USER = re.compile(r"(?i)\b([a-z]:[\\/]+Users[\\/]+)([^\\/\s\"'<>|:*?]+)")
_NIX_USER = re.compile(r"(?<![\w.-])(/(?:Users|home)/)([^/\s\"'<>:]+)")


def _path_forms(p: str) -> list[str]:
    """一个路径可能出现的几种写法：原样、正斜杠、反斜杠、JSON 里转义过的反斜杠。"""
    forms = {p, p.replace("\\", "/"), p.replace("/", "\\"), p.replace("\\", "\\\\")}
    return [f for f in forms if len(f) >= MIN_LITERAL]


def known_values() -> list[tuple[str, str, str]]:
    """这台电脑上已知的个人信息：[(原样, 换成什么, 哪一类)]。读不到的跳过。"""
    out: list[tuple[str, str, str]] = []

    def add(value, repl: str, kind: str) -> None:
        v = str(value or "").strip()
        if len(v) >= MIN_LITERAL:
            out.append((v, repl, kind))

    for form in _path_forms(str(paths.ROOT)):
        add(form, "<项目>", "路径")
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        home = ""
    if home and home not in ("/", "\\"):
        for form in _path_forms(home):
            add(form, "<家目录>", "路径")
    try:
        user = getpass.getuser()
    except (OSError, KeyError, ImportError):
        user = ""
    if user.lower() not in _GENERIC_USERS:
        add(user, "<用户>", USERNAME)
    try:
        from maapilot import config
        addr = ((config.raw() or {}).get("emulator") or {}).get("address")
        add(addr, "<模拟器地址>", "模拟器地址")
    except Exception:                                   # noqa: BLE001 - 配置读不了不影响别的
        pass
    try:
        from maapilot.tools.skland_cmd import load_store
        store = load_store() or {}
        add(store.get("uid"), "<UID>", "账号")
        add((store.get("role") or {}).get("nickName"), "<昵称>", "账号")
        add(store.get("token"), MASKED, "凭据")
        add(store.get("did"), MASKED, "凭据")
    except Exception:                                   # noqa: BLE001 - 没接森空岛、文件读不了
        pass
    for key in ("CLAUDE_CODE_USER_EMAIL", "CLAUDE_CODE_ACCOUNT_UUID", "CLAUDE_CODE_ORGANIZATION_UUID"):
        add(os.environ.get(key), MASKED, "账号")
    return out


class Scrubber:
    """`apply(文字)` → 脱敏后的文字；`counts` 记每一类换了几处（给用户看"程序去掉了什么"）。"""

    def __init__(self, masks: list[str] | None = None, known: list[tuple[str, str, str]] | None = None):
        values = list(known_values() if known is None else known)
        values += [(m, MASKED, "指定的字样") for m in (masks or []) if len(m.strip()) >= 2]
        seen: set[str] = set()
        self.values = []
        for v, repl, kind in sorted(values, key=lambda x: -len(x[0])):      # 长的先换：项目路径在家目录里面
            if v not in seen:
                seen.add(v)
                self.values.append((v, repl, kind))
        self.counts: dict[str, int] = {}

    def _count(self, kind: str, n: int = 1) -> None:
        if n:
            self.counts[kind] = self.counts.get(kind, 0) + n

    def apply(self, text: str) -> str:
        if not text:
            return text
        for v, repl, kind in self.values:
            if kind == USERNAME:                        # 整词，后面也不是域名的一段
                word = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(v)}(?![A-Za-z0-9_-]|\.[A-Za-z])")
                text = self._sub(word, lambda m, r=repl: r, text, kind)
                continue
            n = text.count(v)
            if n:
                text = text.replace(v, repl)
                self._count(kind, n)
        text = self._sub(_WIN_USER, lambda m: m.group(1) + "<用户>", text, "路径", skip="<用户>")
        text = self._sub(_NIX_USER, lambda m: m.group(1) + "<用户>", text, "路径", skip="<用户>")
        text = self._sub(_EMAIL, lambda m: "<邮箱>", text, "邮箱")
        text = self._sub(_IPV4, lambda m: "<地址>", text, "网络地址")
        text = self._sub(_EMULATOR, lambda m: "<模拟器地址>", text, "模拟器地址")
        cleaned = scrub_text(text)
        if cleaned != text:
            self._count("像密钥的字样")
        return cleaned

    def _sub(self, pattern: re.Pattern, repl, text: str, kind: str, skip: str | None = None) -> str:
        hits = 0

        def one(m: re.Match) -> str:
            nonlocal hits
            if skip and m.group(m.lastindex or 0) == skip:
                return m.group(0)
            hits += 1
            return repl(m)
        out = pattern.sub(one, text)
        self._count(kind, hits)
        return out

    def apply_json(self, value):
        """字典、列表里的每个字符串（键不动）。"""
        if isinstance(value, str):
            return self.apply(value)
        if isinstance(value, list):
            return [self.apply_json(v) for v in value]
        if isinstance(value, dict):
            return {k: self.apply_json(v) for k, v in value.items()}
        return value
