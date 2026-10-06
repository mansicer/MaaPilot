"""游戏数据表同步（见 docs/concepts/knowledge.md，决策 D20）：data/*.json 来自 Kengxxiao/ArknightsGameData，`data/manifest.json`
记录每张表的 sha256 / 来源 / 抓取时间；`plan()` 只挑出缺失或远端已变化的表。

纯逻辑函数（REQUIRED_TABLES / plan / manifest / sha256_file / missing_tables）不联网，测试只用它们；
联网只发生在 `fetch_remote_shas()` 与 `download()`，由 `sync()` 串起来。
远端"变化"用 GitHub contents API 的 git blob sha 判断（manifest 里的 `git_sha`）；`sha256` 记录本地文件内容，
供其他模块核对文件是否被手工改过。

统计数据（企鹅物流掉率、一图流物品价值，`live`，D121）在官方表之后一并刷新：`sync(server=...)` 传企鹅物流的服务器代码，
None 表示不碰统计数据。统计数据失败不抛异常，结果在 `live` 里。
"""
from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from maapilot import paths

REQUIRED_TABLES: tuple[str, ...] = (
    "character_table", "item_table", "gamedata_const", "building_data",
    "stage_table", "zone_table", "uniequip_table", "activity_table",
)
OPTIONAL_TABLES: tuple[str, ...] = ("skill_table",)   # 同步时一起下，缺了不算环境问题（只用来显示技能名）
REPO = "Kengxxiao/ArknightsGameData"
EXCEL_DIR = "zh_CN/gamedata/excel"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/master/{EXCEL_DIR}"
API_URL = f"https://api.github.com/repos/{REPO}/contents/{EXCEL_DIR}?ref=master"
MANIFEST_NAME = "manifest.json"
LIVE_SOURCES = {"penguin": "企鹅物流", "values": "一图流"}   # live.sync() 结果里的来源键 → 显示名
LIVE_OK = ("ok", "skipped")                                  # 其余值是错误文字（skipped：这个服务器没有该来源）


def table_files() -> list[str]:
    return [f"{t}.json" for t in REQUIRED_TABLES + OPTIONAL_TABLES]


def source_url(file: str) -> str:
    return f"{RAW_BASE}/{file}"


# --- 纯逻辑 ---------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path() -> Path:
    return paths.DATA / MANIFEST_NAME


def read_manifest() -> dict:
    p = manifest_path()
    if not p.exists():
        return {}
    return json.load(open(p, encoding="utf-8"))


def write_manifest(manifest: dict) -> None:
    p = manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def missing_tables() -> list[str]:
    """本地缺失（不存在或空文件）的表名。"""
    return [t for t in REQUIRED_TABLES if not (paths.DATA / f"{t}.json").exists()
            or (paths.DATA / f"{t}.json").stat().st_size == 0]


def plan(local_manifest: dict, remote_shas: dict[str, str]) -> list[str]:
    """需要下载的文件：manifest 里没有、或记录的 sha 与远端不同。manifest 条目用 `git_sha`（GitHub blob sha）
    比对，没有 `git_sha` 时退回 `sha256` 字段。"""
    out = []
    for file, remote in remote_shas.items():
        entry = local_manifest.get(file) or {}
        local = entry.get("git_sha") or entry.get("sha256")
        if local != remote:
            out.append(file)
    return out


# --- 联网 ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "maapilot-datasync", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_remote_shas(files: list[str] | None = None) -> dict[str, str]:
    """GitHub contents API：excel 目录下每个文件的 git blob sha（只取我们需要的文件）。"""
    wanted = set(files or table_files())
    listing = json.loads(_get(API_URL))
    return {e["name"]: e["sha"] for e in listing if e.get("name") in wanted}


def download(file: str) -> Path:
    dest = paths.DATA / file
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = _get(source_url(file), timeout=300)
    json.loads(data)                                  # 不把半截/错误页当表存下来
    tmp = dest.with_suffix(".json.part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def sync(check_only: bool = False, *, server: str | None = None, now: datetime | None = None) -> dict:
    """比对 manifest 与远端，下载缺失/变化的表并更新 manifest，需要时重建 data/recipes.json。
    返回 {"to_update", "updated", "missing", "checked", "not_on_remote", "recipes_rebuilt"}（check_only 时没有最后一项）。
    官方表的网络失败直接抛异常，由调用方决定是否致命（日常的 sys:data 里非致命）。

    `server`（企鹅物流的服务器代码，如 "CN"）不为 None 时结果另有 `live`：刷新时是 `live.sync()` 的结果
    （{penguin, values, fetched_at}，失败不抛），check_only 时是缓存年龄 {matrix_age_h, values_age_d}（没有缓存为 None）。"""
    manifest = read_manifest()
    files = table_files()
    remote = fetch_remote_shas(files)
    missing = [f"{t}.json" for t in missing_tables()]
    to_update = sorted(set(plan(manifest, remote)) | set(missing))
    result = {"to_update": to_update, "updated": [], "missing": missing, "checked": len(remote),
              "not_on_remote": [f for f in files if f not in remote]}
    if check_only:
        if server is not None:
            result["live"] = live_ages(server, now=now)
        return result
    for file in to_update:
        if file not in remote:
            continue
        dest = download(file)
        manifest[file] = {"sha256": sha256_file(dest), "git_sha": remote[file],
                          "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                          "source_url": source_url(file)}
        result["updated"].append(file)
    write_manifest(manifest)
    result["recipes_rebuilt"] = _rebuild_recipes_if_stale(result["updated"])
    if server is not None:
        from maapilot.knowledge import live
        result["live"] = live.sync(server, now=now)
    return result


def live_ages(server: str, *, now: datetime | None = None) -> dict:
    """统计数据缓存的年龄：{matrix_age_h: 掉率几小时前, values_age_d: 物品价值几天前}；没拉到过的为 None。
    只读 data/live/manifest.json，不联网。"""
    from maapilot.knowledge import live
    a = live.ages(server, now=now)
    matrix_s, values_s = a.get(live.matrix_file(server)), a.get(live.VALUES_FILE)
    return {"matrix_age_h": None if matrix_s is None else round(matrix_s / 3600, 1),
            "values_age_d": None if values_s is None else round(values_s / 86400, 1)}


def _rebuild_recipes_if_stale(updated: list[str]) -> bool:
    """data/recipes.json 由 building_data.json 生成：building_data 刚更新、或 recipes.json 还没有时重建。"""
    if not (paths.DATA / "building_data.json").exists():
        return False
    if "building_data.json" not in updated and (paths.DATA / "recipes.json").exists():
        return False
    from maapilot.knowledge import recipes
    recipes.write_recipes_json()
    return True
