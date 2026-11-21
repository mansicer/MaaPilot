"""反馈上传前的兜底脱敏（`maapilot feedback`）：把这台电脑上已知的个人信息从文字里换掉。

agent 写会话记录时自己会脱敏（skill `maapilot-feedback`），这里是第二层：不靠模型判断，按这台机器上的真实值匹配——
家目录、项目文件夹、系统用户名、模拟器地址、森空岛的 uid 和昵称、agent 另外指定的字样（`--mask`）；
再加几种认得出形状的：邮箱、手机号、IP 和 MAC 地址、用户目录、长得像 token 的一段。
换成什么都看得出原来是哪一类（`<家目录>`、`<UID>`），排查问题时不丢上下文。第三层是用户在对话里看过再同意。

顺序：先换形状（邮箱要趁用户名还没被换掉时整个认出来），再换已知的值（长的先换），最后换像 token 的。
已知的值里，路径和用户名不分大小写（Windows 上同一个路径大小写随便写）；短的 ASCII 字样和用户名按整词匹配，免得误伤别的词。
一个字的值没法换（会把正文换得面目全非）：记在 `skipped` 里，`maapilot feedback` 把它告诉 agent。
"""
from __future__ import annotations

import getpass
import json
import os
import re
from pathlib import Path

from maapilot import paths
from maapilot.tools.log_summary import scrub_text

MASKED = "<已隐去>"
USERNAME = "用户名"
PATH = "路径"
MASK = "指定的字样"
MIN_CHARS = 2                             # 一个字的不换
WORD_BELOW = 4                            # 比这短的纯 ASCII 字样按整词匹配
_GENERIC_USERS = {"root", "user", "admin", "administrator", "home", "runner", "ubuntu", "users"}
# 用户名后面跟着这些后缀时是域名的一段（sicer.top），不是用户名
_TLDS = "top|com|net|org|io|dev|cn|me|xyz|app|ai|co|cc|info|edu|gov"

_LOCAL = r"[A-Za-z0-9._%+-]"
# 有上限、前面不是同类字符：一长串没有 @ 的字母数字不会让它反复回溯
_EMAIL = re.compile(rf"(?<!{_LOCAL}){_LOCAL}{{1,64}}@[A-Za-z0-9-]{{1,63}}(?:\.[A-Za-z0-9-]{{1,63}}){{1,8}}")
# 前面是字母（v5.12.3.1）的当版本号，不动
_IPV4 = re.compile(r"(?<![A-Za-z0-9_.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?::\d{2,5})?(?![A-Za-z0-9_]|\.\d)")
_MAC = re.compile(r"(?<![0-9A-Fa-f:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMULATOR = re.compile(r"\bemulator-\d{4,5}\b")
_WIN_USER = re.compile(r"(?i)((?:\b[a-z]:|/mnt/[a-z]|/[a-z])[\\/]+Users[\\/]+)([^\\/\s\"'<>|:*?]+)")
_NIX_USER = re.compile(r"(/(?:Users|home)/)([^/\s\"'<>:]+)")
USER_MARK = "<用户>"


def _path_forms(p: str) -> list[str]:
    """一个路径可能出现的几种写法：原样、正斜杠、反斜杠、JSON 里转义过的反斜杠；Windows 盘符路径再加 Git Bash 和 WSL 的写法。"""
    forms = {p, p.replace("\\", "/"), p.replace("/", "\\"), p.replace("\\", "\\\\")}
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if m:
        rest = m.group(2).replace("\\", "/")
        forms |= {f"/{m.group(1).lower()}/{rest}", f"/mnt/{m.group(1).lower()}/{rest}"}
    return [f for f in forms if len(f) >= 3]


def known_values() -> list[tuple[str, str, str]]:
    """这台电脑上已知的个人信息：[(原样, 换成什么, 哪一类)]。读不到的跳过。"""
    out: list[tuple[str, str, str]] = []

    def add(value, repl: str, kind: str) -> None:
        v = str(value or "").strip()
        if v:
            out.append((v, repl, kind))

    for form in _path_forms(str(paths.ROOT)):
        add(form, "<项目>", PATH)
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        home = ""
    if home and home not in ("/", "\\"):
        for form in _path_forms(home):
            add(form, "<家目录>", PATH)
    try:
        user = getpass.getuser()
    except (OSError, KeyError, ImportError):
        user = ""
    if user.lower() not in _GENERIC_USERS:
        add(user, USER_MARK, USERNAME)
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
        nick = str((store.get("role") or {}).get("nickName") or "")
        add(nick, "<昵称>", "账号")
        if "#" in nick:                                 # "博士#1234"：对话里多半只说前半截
            add(nick.split("#", 1)[0], "<昵称>", "账号")
        add(store.get("token"), MASKED, "凭据")
        add(store.get("did"), MASKED, "凭据")
    except Exception:                                   # noqa: BLE001 - 没接森空岛、文件读不了
        pass
    for key in ("CLAUDE_CODE_USER_EMAIL", "CLAUDE_CODE_ACCOUNT_UUID", "CLAUDE_CODE_ORGANIZATION_UUID"):
        add(os.environ.get(key), MASKED, "账号")
    return out


def _pattern(value: str, kind: str) -> re.Pattern:
    flags = re.IGNORECASE if kind in (PATH, USERNAME) else 0
    body = re.escape(value)
    if kind == USERNAME:                                # 整词，后面也不是域名的一段
        return re.compile(rf"(?<![A-Za-z0-9_-]){body}(?![A-Za-z0-9_-]|\.(?:{_TLDS})\b)", flags)
    if value.isascii() and len(value) < WORD_BELOW:     # 短的 ASCII 字样：整词
        return re.compile(rf"(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])", flags)
    return re.compile(body, flags)


class Scrubber:
    """`apply(文字)` → 脱敏后的文字。`counts`：每一类换了几处；`skipped`：太短没法换的已知值是哪几类。"""

    def __init__(self, masks: list[str] | None = None, known: list[tuple[str, str, str]] | None = None):
        values = list(known_values() if known is None else known)
        values += [(m.strip(), MASKED, MASK) for m in (masks or [])]
        self.skipped: list[str] = []
        seen: set[str] = set()
        rules = []
        for v, repl, kind in values:
            if len(v) < MIN_CHARS:
                if v and kind not in self.skipped:
                    self.skipped.append(kind)
                continue
            forms = [v]
            if not v.isascii():                         # JSON 里转义成 \uXXXX 的写法（工具输出里常见）
                forms.append(json.dumps(v)[1:-1])
            for f in forms:
                if f not in seen:
                    seen.add(f)
                    rules.append((f, repl, kind))
        rules.sort(key=lambda r: -len(r[0]))            # 长的先换：项目路径在家目录里面
        self.rules = [(_pattern(v, kind), repl, kind) for v, repl, kind in rules]
        self.counts: dict[str, int] = {}

    def _count(self, kind: str, n: int = 1) -> None:
        if n:
            self.counts[kind] = self.counts.get(kind, 0) + n

    def apply(self, text: str) -> str:
        if not text:
            return text
        text = self._sub(_EMAIL, lambda m: "<邮箱>", text, "邮箱")
        for pattern, repl, kind in self.rules:
            text = self._sub(pattern, lambda m, r=repl: r, text, kind)
        text = self._sub(_WIN_USER, lambda m: m.group(1) + USER_MARK, text, PATH, keep=_already_user)
        text = self._sub(_NIX_USER, lambda m: m.group(1) + USER_MARK, text, PATH, keep=_already_user)
        text = self._sub(_IPV4, lambda m: "<地址>", text, "网络地址", keep=_not_ip)
        text = self._sub(_MAC, lambda m: "<地址>", text, "网络地址")
        text = self._sub(_PHONE, lambda m: "<手机号>", text, "手机号")
        text = self._sub(_EMULATOR, lambda m: "<模拟器地址>", text, "模拟器地址")
        cleaned = scrub_text(text)
        if cleaned != text:
            self._count("像密钥的字样")
        return cleaned

    def _sub(self, pattern: re.Pattern, repl, text: str, kind: str, keep=None) -> str:
        hits = 0

        def one(m: re.Match) -> str:
            nonlocal hits
            if keep and keep(m):
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


def _already_user(m: re.Match) -> bool:
    return m.group(2) == USER_MARK or m.group(2).startswith("<")


def _not_ip(m: re.Match) -> bool:
    return any(int(m.group(i)) > 255 for i in range(1, 5))
