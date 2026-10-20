"""agent 的权限规则：只在这里写一份，生成各宿主的格式（`maapilot dev rules`，开发用）。

生成的文件（入库，不要手改；改规则改这里，再跑 `maapilot dev rules`，`--check` 核对）：
- `.claude/settings.json` 的 `permissions`（Claude Code；文件里别的部分如 hooks 原样保留）；
- `.codex/rules/maapilot.rules`（Codex，Starlark 的 `prefix_rule`，整份生成）；
- `docs/cli.md`「Claude Code 权限设置」里抄的 settings.json 全文。

两边的差别（都按今天的实际做法）：
- maapilot 的几种写法（`MAAPILOT`）：Codex 每条规则每种写法都列（规则按命令的第一个词认程序，没 PATH 时 agent 写 `.venv` 里的）；
  Claude Code 的放行 / 询问只列本名（启动钩子把 `.venv` 的程序目录放进了 PATH，别的写法按默认弹确认），拒绝的每种写法都列。
- 文件规则（`FILES`：Read / Edit / WebFetch）只有 Claude Code 有；Codex 的规则只管命令，管不了文件（靠沙箱和 AGENTS.md 硬规则）。
- `sleep 300` 在 Claude Code 里是整条命令精确匹配，在 Codex 里是前缀（Codex 没有精确匹配的写法）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

ALLOW, ASK, DENY = "allow", "ask", "deny"
KINDS = (ALLOW, ASK, DENY)
CODEX = {ALLOW: "allow", ASK: "prompt", DENY: "forbidden"}       # 结论在 Codex 里的叫法

# maapilot 的几种写法：PATH 里的、.venv 里的（macOS / Linux）、Windows 的 .exe（PATH 里的、.venv 里两种分隔符）
MAAPILOT = ("maapilot", ".venv/bin/maapilot", "maapilot.exe", ".venv\\Scripts\\maapilot.exe", ".venv/Scripts/maapilot.exe")
BARE = MAAPILOT[0]

TOKEN_WHY = "MaaPilot：森空岛凭据只由用户自己处理"
DIRECT_WHY = "MaaPilot：只通过 maapilot 操作模拟器"


@dataclass(frozen=True)
class Cmd:
    """maapilot 后面的前缀（一个词 = 整组）→ 结论。"""
    words: tuple[str, ...]
    decision: str
    why: str | None = None          # 拒绝的理由（Codex 的 justification）


@dataclass(frozen=True)
class Other:
    """别的程序：progs 里每种写法 × args 里每个参数 → 结论。exact：Claude Code 里整条命令精确匹配（不带 `:*`）。"""
    name: str | None                # Codex 规则文件里程序列表的变量名（只有一种写法时不要）
    progs: tuple[str, ...]
    args: tuple[str, ...]
    decision: str
    why: str | None = None
    exact: bool = False


def _cmds(decision: str, *specs: str, why: str | None = None) -> list[Cmd]:
    return [Cmd(tuple(s.split()), decision, why) for s in specs]


# maapilot 的命令：整个放行，只列例外（拒绝 > 询问 > 放行，两边都是）。加普通的新命令不用改这里，也不用重新生成。
# 例外：记下花合成玉 / 源石的同意（guard allow）要问；开发命令（dev，其中 dev rules 会改 agent 自己的权限）要问；
# 用户自己在终端里跑的（skland token）拒绝。其余以用户在对话里的同意为准（AGENTS.md 硬规则 3），程序那层另有把关（锁、对账、定时日常里拒绝）。
# 新命令要是会花合成玉 / 源石、碰凭据或改权限，记得加进这里的例外。
COMMANDS: list[Cmd] = [
    Cmd((), ALLOW),
    *_cmds(ASK, "guard allow", "dev"),
    *_cmds(DENY, "skland token", why=TOKEN_WHY),
]

# 别的程序：掉线时等用户回复的 5 分钟计时器放行；直接用 maa 跑任务、adb shell / -s 绕过了锁和对账，拒绝
# （maa install / update、adb connect / devices 不在其列，按默认问）。
OTHERS: list[Other] = [
    Other(None, ("sleep",), ("300",), ALLOW, exact=True),
    Other("MAA", ("maa", "maa.exe"),
          ("run", "fight", "copilot", "ssscopilot", "roguelike", "reclamation", "startup", "closedown"), DENY, DIRECT_WHY),
    Other("ADB", ("adb", "adb.exe"), ("shell", "-s"), DENY, DIRECT_WHY),
]

# 文件和网页（只有 Claude Code）：(工具, 范围, 结论)。写文件的规则写 Edit（覆盖所有写文件的工具；Write(...) 不生效）。
FILES: list[tuple[str, str, str]] = [
    ("Read", "private/**", ALLOW),
    ("Edit", "private/config.yaml", ALLOW),
    ("Edit", "private/goals.md", ALLOW),
    ("Edit", "private/goals-done.md", ALLOW),
    ("Edit", "private/setup.json", ALLOW),
    ("Edit", "private/shots/**", ALLOW),
    ("WebFetch", "domain:prts.maa.plus", ALLOW),
    ("WebFetch", "domain:prts.plus", ALLOW),
    ("WebFetch", "domain:maa.plus", ALLOW),
    ("Edit", "private/guard/**", DENY),           # 只由程序写
    ("Edit", "private/lock.json", DENY),
    ("Read", "private/skland.json", DENY),        # 森空岛凭据
    ("Edit", "private/skland.json", DENY),
]


# --- 判定（测试用，也说明两边的语义） --------------------------------------------------------------------------------

def _prefix(pat: tuple[str, ...], argv: list[str]) -> bool:
    return len(argv) >= len(pat) and tuple(argv[:len(pat)]) == pat


def decide(argv: list[str], host: str = "claude") -> str | None:
    """这条命令（按词拆开，第一个是程序）在 host（claude / codex）里的结论：allow / ask / deny / None（没有规则）。
    拒绝 > 询问 > 放行，两边一样。"""
    found = set()
    prog, rest = argv[0], argv[1:]
    for c in COMMANDS:
        progs = MAAPILOT if (host == "codex" or c.decision == DENY) else (BARE,)
        if prog in progs and _prefix(c.words, rest):
            found.add(c.decision)
    for o in OTHERS:
        if prog in o.progs and rest and rest[0] in o.args:
            if not (o.exact and host == "claude" and len(rest) != 1):
                found.add(o.decision)
    for k in (DENY, ASK, ALLOW):
        if k in found:
            return k
    return None


# --- Claude Code -----------------------------------------------------------------------------------------------------

def claude_permissions() -> dict:
    out = {k: [] for k in KINDS}
    for c in COMMANDS:
        for prog in (MAAPILOT if c.decision == DENY else (BARE,)):
            out[c.decision].append(f"Bash({' '.join((prog, *c.words))}:*)")
    for o in OTHERS:
        for prog in o.progs:
            for a in o.args:
                out[o.decision].append(f"Bash({prog} {a})" if o.exact else f"Bash({prog} {a}:*)")
    for tool, what, k in FILES:
        out[k].append(f"{tool}({what})")
    return out


def render_claude(settings: dict) -> dict:
    """只换掉 settings 里的 permissions，别的（$schema、hooks……）和键的顺序不动。"""
    perms = claude_permissions()
    if "permissions" not in settings:
        return {**settings, "permissions": perms}
    return {k: (perms if k == "permissions" else v) for k, v in settings.items()}


def dump_json(d: dict) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


# --- Codex -----------------------------------------------------------------------------------------------------------

CODEX_HEADER = """\
# MaaPilot：Codex 的命令规则。由 maapilot/policy.py 生成（maapilot dev rules），不要手改；和 .claude/settings.json 的权限出自同一份。
# allow 在沙箱外跑、不弹确认；prompt 每次问用户（无头 codex exec 里等于拒绝）；forbidden 一律拒绝。
# maapilot 整个放行，只列例外：合成玉 / 源石的放行（guard allow）、开发命令（dev）要问，森空岛凭据拒绝；其余以用户在对话里的同意为准。
# 禁止的只有绕过锁和对账的直接操作（直接用 maa 跑任务、直接 adb shell）和森空岛凭据。
# 规则按命令的第一个词认程序，所以 maapilot 的几种写法（PATH 里的、.venv 里的、Windows 的 .exe）、adb / maa 的 .exe 都列上。
# Windows 上 Codex 经 PowerShell 跑命令，规则看到的是不是这样拆开的命令还没核实过；写绝对路径的也认不出来
# （拦直接操作靠的是锁、对账和 AGENTS.md 的硬规则，这里只是一道护栏）。
# 规则只管命令、管不了文件：Claude Code 里拒绝读写的文件（private/skland.json、private/guard/……）这里没有对应。
"""


def _s(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def _patterns(cmds: list[Cmd]) -> list[list]:
    """同一前缀的合成一条：整组的放进一个列表，`daily run / status …` 写成 ["daily", ["run", "status", …]]。"""
    whole = [[]] if any(not c.words for c in cmds) else []          # 空前缀：整个程序
    singles = [c.words[0] for c in cmds if len(c.words) == 1]
    groups: dict[tuple, list[str]] = {}
    for c in cmds:
        if len(c.words) > 1:
            groups.setdefault(c.words[:-1], []).append(c.words[-1])
    out = whole + ([[singles]] if singles else [])
    for head, last in groups.items():
        out.append([*head, last if len(last) > 1 else last[0]])
    return out


def render_codex() -> str:
    lines = [CODEX_HEADER.rstrip("\n"), f"MAAPILOT = {_s(list(MAAPILOT))}"]
    lines += [f"{o.name} = {_s(list(o.progs))}" for o in OTHERS if o.name]
    lines += ["", "# maapilot 后面的部分"]
    for k, var in ((ALLOW, "ALLOW"), (ASK, "PROMPT")):
        lines.append(f"{var} = [")
        lines += [f"    {_s(p)}," for p in _patterns([c for c in COMMANDS if c.decision == k])]
        lines.append("]")
    deny = [c for c in COMMANDS if c.decision == DENY]
    lines.append("FORBIDDEN = [                      # [前缀, 理由]")
    for why in dict.fromkeys(c.why for c in deny):
        lines += [f"    [{_s(p)}, {_s(why)}]," for p in _patterns([c for c in deny if c.why == why])]
    lines += ["]",
              "for prog in MAAPILOT:",
              "    for p in ALLOW:",
              '        prefix_rule(pattern = [prog] + p, decision = "allow")',
              "    for p in PROMPT:",
              '        prefix_rule(pattern = [prog] + p, decision = "prompt")',
              "    for p, why in FORBIDDEN:",
              '        prefix_rule(pattern = [prog] + p, decision = "forbidden", justification = why)']
    for o in OTHERS:
        arg = _s(o.args[0]) if len(o.args) == 1 else _s(list(o.args))
        why = f", justification = {_s(o.why)}" if o.why else ""
        rule = f'decision = "{CODEX[o.decision]}"{why})'
        if o.name:
            lines += [f"for prog in {o.name}:",
                      f"    prefix_rule(pattern = [prog, {arg}],", f"                {rule}"]
        else:
            lines += [f"prefix_rule(pattern = [{_s(o.progs[0])}, {arg}], {rule}"]
    return "\n".join(lines) + "\n"


# --- 生成的文件 ------------------------------------------------------------------------------------------------------

SETTINGS = Path(".claude") / "settings.json"
CODEX_RULES = Path(".codex") / "rules" / "maapilot.rules"
CLI_DOC = Path("docs") / "cli.md"
_DOC_BLOCK = re.compile(r"(## Claude Code 权限设置\n.*?```json\n)(.*?)(```)", re.S)


def render_doc(doc: str, settings_text: str) -> str:
    """docs/cli.md「Claude Code 权限设置」下面的 ```json 块换成 settings.json 全文。"""
    m = _DOC_BLOCK.search(doc)
    if not m:
        raise ValueError("docs/cli.md 里找不到「Claude Code 权限设置」下的 ```json 块")
    return doc[:m.start(2)] + settings_text + doc[m.end(2):]


def generated(root: Path) -> list[tuple[Path, str | None, str]]:
    """[(相对路径, 现在的内容, 该是的内容)]。"""
    def read(p: Path) -> str | None:
        f = root / p
        return f.read_text(encoding="utf-8") if f.exists() else None

    settings_now = read(SETTINGS)
    settings = dump_json(render_claude(json.loads(settings_now) if settings_now else {}))
    doc_now = read(CLI_DOC)
    out = [(SETTINGS, settings_now, settings), (CODEX_RULES, read(CODEX_RULES), render_codex())]
    if doc_now is not None:
        out.append((CLI_DOC, doc_now, render_doc(doc_now, settings)))
    return out
