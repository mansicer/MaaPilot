"""MaaPilot 自己、maa-cli、MaaCore 有没有新版本（不碰模拟器）。契约见 docs/cli.md「update」。

目标是用户不和更新断联，所以：
- 新版本信息只看 GitHub Releases。没有关掉检查的开关；连续查不成功也不悄悄过去：超过 CHECK_LOST_DAYS 天没查成功，`status()` 的 `lost` 为真，启动检查、日常都写明。
- 告诉过用户（`told`）只管一阵子：超过 REMIND_DAYS 天还没更新，再列进 `due`。

- MAA 资源不问用户：日常开头 `maa hot-update`，maa-cli 跑任务前也会自己更新（cli.toml 的 resource.auto_update）。
  更新不成任务照跑，连着 CHECK_LOST_DAYS 天不成才告诉用户。

缓存在 private/state/update.json：`{at, maa_core_at, maapilot, maa_core, local, errors, resource, told: {<名字>: {version, at}}}`。
`at`、`maa_core_at` 是最近一次查成功 MaaPilot、MaaCore 新版本的时间（各记各的：一边查不到不能被另一边盖住）；`resource` 是 `{at（最近一次更新成功）, commit, commit_at, error}`。
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from datetime import datetime, timedelta

from maapilot import __version__, paths, toolchain, version
from maapilot.knowledge import sources
from maapilot.knowledge.sources import UpstreamFormat
from maapilot.oscompat import procs
from maapilot.tools import common

MAAPILOT_REPO = "mansicer/MaaPilot"
MAAPILOT_GIT = f"https://github.com/{MAAPILOT_REPO}.git"
MAAPILOT_API = f"https://api.github.com/repos/{MAAPILOT_REPO}/releases/latest"
MAA_API = "https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights/releases/latest"
TIMEOUT_S = 8
STALE = timedelta(hours=20)     # 缓存多旧算旧（日常开头、update check 重查）
CHECK_LOST_DAYS = 3             # 这么多天没查成功就告诉用户（查更新的路断了）
RESOURCE_FRESH = timedelta(hours=1)  # 接着跑（--only）时不重复更新资源
RESOURCE_TIMEOUT_S = 300
REMIND_DAYS = 7                 # 告诉过用户、过了这么多天还没更新就再说一次
NOTES_MAX = 4000
WHATS = ("maapilot", "maa-cli", "maa-core")


def _file():
    return paths.STATE / "update.json"


def load() -> dict:
    d = common.read_json(_file(), {})
    return d if isinstance(d, dict) else {}


def _now() -> datetime:
    return datetime.now().astimezone()


def _age(at) -> timedelta | None:
    try:
        return _now() - datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return None


def stale(d: dict | None = None) -> bool:
    age = _age((d if d is not None else load()).get("at"))
    return age is None or age >= STALE


def http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "maapilot", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def _release(source: str, url: str) -> dict:
    """GitHub 的最新正式发布 → {version, tag, url, published, notes, via}。格式不对 → UpstreamFormat（已记进 sources.json）。"""
    try:
        d = http_json(url)
        tag = sources.need(d, "tag_name", str, source=source)
        if not toolchain.version_tuple(tag):
            raise UpstreamFormat(source, "tag_name", f"认不出版本号 {tag[:40]}")
    except UpstreamFormat as e:
        sources.bad(source, e, effect="查不到新版本")
        raise
    sources.ok(source)
    return {"version": tag.removeprefix("v"), "tag": tag, "url": d.get("html_url"), "published": d.get("published_at"),
            "notes": str(d.get("body") or "")[:NOTES_MAX]}


def local_versions() -> dict:
    """这台电脑上跑的 maa-cli、MaaCore 版本（`maa version`）；跑不了 → 空。"""
    from maapilot import config
    from maapilot.crew import maa as crew_maa
    try:
        p = procs.run_text([config.tool("maa"), "version"], timeout=30, env=crew_maa.env())
    except (OSError, subprocess.SubprocessError):
        return {}
    text = p.stdout or ""
    cli, core = re.search(r"maa-?cli\s+v?(\S+)", text, re.I), re.search(r"maa-?core\s+v?(\S+)", text, re.I)
    return {"maa_cli": cli.group(1) if cli else None, "maa_core": core.group(1) if core else None}


def refresh() -> dict:
    """重新查一次（联网，要几秒到几十秒）。查不到的项留上次的结果、原因写进 errors，不抛错。"""
    errors: dict = {}
    new: dict = {"local": local_versions()}
    try:
        new.update(maapilot=_release("maapilot_release", MAAPILOT_API), at=_now().isoformat(timespec="seconds"))
    except Exception as e:                               # noqa: BLE001 - 限流、连不上、格式变了：留上次的，连着几天这样就告诉用户
        errors["maapilot"] = f"{type(e).__name__}: {e}"
    try:
        new.update(maa_core=_release("maa_release", MAA_API), maa_core_at=_now().isoformat(timespec="seconds"))
    except Exception as e:                               # noqa: BLE001 - 查不到 MaaCore 不耽误 MaaPilot 的
        errors["maa-core"] = f"{type(e).__name__}: {e}"
    d = {**load(), **new, "errors": errors}              # 拉的这几秒里别的进程记下的 told 不丢
    common.write_json(_file(), d)
    return d


def reread_local() -> None:
    """换过 maa-cli / MaaCore 后重读本机版本（不联网）。"""
    d = load()
    if d:
        d["local"] = local_versions()
        common.write_json(_file(), d)


def resource_stale(d: dict | None = None) -> bool:
    age = _age(((d if d is not None else load()).get("resource") or {}).get("at"))
    return age is None or age >= RESOURCE_FRESH


def _resource_version() -> dict:
    """资源目录的 git 提交：{commit, commit_at}；读不了 → 空。"""
    from maapilot.crew import maacore
    hot = maacore.hot_update_dir()
    if hot is None:
        return {}
    try:
        p = procs.run_text(["git", "-C", str(hot), "log", "-1", "--format=%h %cI"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    parts = (p.stdout or "").split()
    return {"commit": parts[0], "commit_at": parts[1]} if p.returncode == 0 and len(parts) == 2 else {}


def hot_update() -> dict:
    """更新 MAA 资源（`maa hot-update`，联网）。成败都记进缓存，不抛错；要改用户的数据目录，调用方保证 MAA 没在跑。→ 这条记录。"""
    from maapilot import config
    from maapilot.crew import maa as crew_maa
    rec = dict(load().get("resource") or {})
    try:
        crew_maa.install_config()
        p = procs.run_text([config.tool("maa"), "hot-update"], timeout=RESOURCE_TIMEOUT_S, env=crew_maa.env())
        err = None if p.returncode == 0 else f"退出码 {p.returncode}：{(p.stderr or p.stdout or '').strip()[-200:]}"
    except (OSError, subprocess.SubprocessError) as e:
        err = f"{type(e).__name__}: {e}"
    rec.update(_resource_version(), error=err)
    if not err:
        rec["at"] = _now().isoformat(timespec="seconds")
    d = load()
    d["resource"] = rec
    common.write_json(_file(), d)
    return rec


def _resource(d: dict) -> dict:
    r = d.get("resource") or {}
    age = _age(r.get("at"))
    return {"commit": r.get("commit"), "commit_at": r.get("commit_at"), "at": r.get("at"), "error": r.get("error"),
            "age_days": None if age is None else age.days,
            "lost": bool(r.get("error")) and (age is None or age.days >= CHECK_LOST_DAYS)}


def _maa_cli(d: dict) -> dict:
    from maapilot import config
    hi = toolchain.MAA_CLI_TESTED[1]
    m = toolchain.managed("maa")
    cur = toolchain.installed_version("maa") if m else (d.get("local") or {}).get("maa_cli")
    behind = bool(cur) and toolchain.version_tuple(cur) < toolchain.version_tuple(hi)
    out = {"current": cur, "latest": hi, "update": bool(m and behind and str(m) == config.tool("maa"))}
    if behind and not out["update"]:
        out["note"] = f"用的是别处装的 maa-cli（{config.tool('maa')}），这里换不了：自己升级，或把 tools.maa 改成 maa 后装项目里的"
    return out


def _behind(cur, latest) -> bool:
    return bool(cur and latest) and toolchain.version_tuple(cur) < toolchain.version_tuple(latest)


def status(d: dict | None = None) -> dict:
    """{maapilot, maa-cli, maa-core, at, age_days, lost, errors, due}。只读缓存，不联网。"""
    d = load() if d is None else d
    v = version.info()
    p, c = d.get("maapilot") or {}, d.get("maa_core") or {}
    cur = (d.get("local") or {}).get("maa_core")
    core_age = _age(d.get("maa_core_at"))
    out = {"maapilot": {"current": v["version"], "commit": v["commit"], "latest": p.get("version"),
                        "update": _behind(__version__, p.get("version")), "url": p.get("url"),
                        "published": p.get("published"), "notes": p.get("notes") or ""},
           "maa-cli": _maa_cli(d),
           "maa-core": {"current": cur, "latest": c.get("version"), "update": _behind(cur, c.get("version")), "url": c.get("url"),
                        "at": d.get("maa_core_at"), "age_days": None if core_age is None else core_age.days,
                        "lost": core_age is None or core_age.days >= CHECK_LOST_DAYS},
           "maa-resource": _resource(d)}
    age = _age(d.get("at"))
    out.update(at=d.get("at"), age_days=None if age is None else age.days, errors=d.get("errors") or {},
               lost=age is None or age.days >= CHECK_LOST_DAYS)
    out["due"] = [w for w in WHATS if out[w]["update"] and _due(w, out[w]["latest"], d)]
    return out


def _due(what: str, latest: str, d: dict) -> bool:
    t = (d.get("told") or {}).get(what) or {}
    if t.get("version") != latest:
        return True
    age = _age(t.get("at"))
    return age is None or age.days >= REMIND_DAYS


def told(whats: list[str]) -> dict:
    d = load()
    st = status(d)
    rec = d.setdefault("told", {})
    for w in whats:
        rec[w] = {"version": st[w]["latest"], "at": _now().isoformat(timespec="seconds")}
    common.write_json(_file(), d)
    return status(d)


NAMES = {"maapilot": "MaaPilot", "maa-cli": "maa-cli", "maa-core": "MAA 本体（MaaCore）"}


def lines(st: dict | None = None) -> list[str]:
    """给 agent 看的几行：有新版本还没说的、查更新的路断了的。"""
    st = status() if st is None else st
    out = [f"{NAMES[w]} 有新版本 {st[w]['latest']}（现在 {st[w]['current']}）" for w in st["due"]]
    if st["lost"]:
        days = "从没" if st["at"] is None else f"已经 {st['age_days']} 天没"
        why = st["errors"].get("maapilot")
        out.append(f"{days}查到 MaaPilot 的新版本信息" + (f"（{why}）" if why else ""))
    c = st["maa-core"]
    if c["lost"]:
        days = "从没" if c["at"] is None else f"已经 {c['age_days']} 天没"
        why = st["errors"].get("maa-core")
        out.append(f"{days}查到 MAA 的新版本信息" + (f"（{why}）" if why else ""))
    r = st["maa-resource"]
    if r["lost"]:
        days = "一直" if r["at"] is None else f"已经 {r['age_days']} 天"
        out.append(f"MAA 资源{days}没更新成功（{r['error']}）")
    return out
