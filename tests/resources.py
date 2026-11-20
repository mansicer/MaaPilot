"""测试要用、但不入库的两样东西：OCR 模型和游戏数据表。固定到上游的某个提交，下载后核对 sha256。

    python -m tests.resources fetch            # 都下到 tests/.resources/（不入库）
    python -m tests.resources fetch ocr-v6     # 只下一样：ocr-v3、ocr-v6、data

测试用哪一份：
- 数据表：`tests/.resources/data/` 在就用它，否则用本机的 `data/`（`maapilot data sync` 下的，版本随游戏更新）。
- OCR 模型：环境变量 `MAAPILOT_TEST_OCR=v3|v6` 指定 `tests/.resources/ocr-<代>/`；没设时 `ocr-v3` 在就用它，否则用本机的
  `resource/model/ocr/`（setup 时从 MAA 资源复制的）。测试把 `paths.RESOURCE` 整个指到下载的那一代
  （`model/ocr/`；仓库 `resource/pipeline` 每次开跑时照抄一份过去，凑成完整的 MaaFramework 资源包）。
- 只用下完整了的（目录里有 `.complete`，内容和现在固定的版本对得上）；下到一半的当作没有。
- 都没有：用到它的测试跳过；设了 `MAAPILOT_TEST_STRICT=1`（CI 里设）改成失败，免得悄悄一条都没跑。

OCR 模型有两代：MAA 在 2026-11-02 把 PaddleOCR 换成了新模型（PP-OCRv6），之前是 PP-OCRv3。用户机器上是哪一代，
看 setup 时 MAA 资源是哪个版本，所以两代都要测（见 tests/test_screens.py 的 OCR_GEN）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from maapilot import paths

DIR = Path(__file__).parent / ".resources"
REPO_RESOURCE = Path(__file__).parent.parent / "resource"       # 入库的那部分 MaaFramework 资源（pipeline）
STRICT = os.environ.get("MAAPILOT_TEST_STRICT") == "1"

MAA_REPO = "MaaAssistantArknights/MaaAssistantArknights"
GAMEDATA_REPO = "Kengxxiao/ArknightsGameData"
RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"

# 每一项：{"repo", "commit", "dir"（仓库里的目录）, "files": {存成的文件名: (仓库里的相对路径, sha256)}}
PINS: dict[str, dict] = {
    "ocr-v3": {                                         # 换模型前的最后一个提交
        "repo": MAA_REPO, "commit": "4351f1816ad4bf8f11bc4abeb1182f4c042f50b2", "dir": "resource/PaddleOCR",
        "files": {
            "det.onnx": ("det/inference.onnx", "d572c1773fd00e72f2f2a4c6399513223c49d70f64bc8ccf52fc6cc500b2803c"),
            "rec.onnx": ("rec/inference.onnx", "ece6e0173b177a79358b7610524d768711c7c887895f1b2b767e2bed83ec88cf"),
            "keys.txt": ("rec/keys.txt", "0fa289d928d99b82bf8df12baf040d7e59fe4c092ca9faae99ce663b6a8f5661"),
        },
    },
    "ocr-v6": {                                         # "PaddleOCR 切换至新模型"那个提交
        "repo": MAA_REPO, "commit": "24648364a3b8184383f2fa1d9bd169d364e09358", "dir": "resource/PaddleOCR",
        "files": {
            "det.onnx": ("det/inference.onnx", "decf4b48bc8c456ef33545f5aef53e9329ab2d137b7a1f2c81a38fc30ec2f317"),
            "rec.onnx": ("rec/inference.onnx", "f9439bc49866e39c5403dd73f6807cf1d19800a0c25ef014b4786d39c6bc34e2"),
            "keys.txt": ("rec/keys.txt", "c8cb17c640b38f6bb4400d364e62e4d6c54f5587ef26802a9c2eac0984271a7f"),
        },
    },
    "data": {
        "repo": GAMEDATA_REPO, "commit": "7e560050b03c34661db1d322d008733072352b2a", "dir": "zh_CN/gamedata/excel",
        "files": {f: (f, sha) for f, sha in {
            "activity_table.json": "26dc00605d5f7cd46d491b108c16beda388fbc94ea94bb33eb0809d9275fb23e",
            "building_data.json": "bd9f3edb85fc74b73fb20024ce04aeeff7dee0ec0ec2d641d1c3edafe75655ad",
            "character_table.json": "af28555d90a6d667304989d1677efa4ef46ffe89eff4145dccd8997329d8ed52",
            "gamedata_const.json": "377e2a0ac98a0e73be8ffa49ef3d6230bbce62dbfce4248e28a719dd0e062e02",
            "item_table.json": "def2db887f6aa7d5098ac36f33cc8fb37933f433d317c7ed28c50459d5eb8adc",
            "skill_table.json": "edc7cda2bae05f623c03d42adbf053a7017a645a4a668063b9ee5a7e99cb6160",
            "stage_table.json": "ed2d294569274aea4784ce00ee67e938226eb6bace63c44e2989421840c8e7a7",
            "uniequip_table.json": "ea9c66d111aa80662c7f89585f76fd2e4799bbe7c3a6ea8c0af78c0fd9311869",
            "zone_table.json": "0241e7ab1f60ba19e27b0b5899cb1ffa8254fe372e1e07cf449e33b397ccc88b",
        }.items()},
    },
}
OCR_GENS = {PINS[name]["files"]["rec.onnx"][1]: name.removeprefix("ocr-") for name in ("ocr-v3", "ocr-v6")}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


# --- 测试用哪一份 ---------------------------------------------------------------------

STAMP = ".complete"                                  # fetch 全部下完、核对过才写；内容是这份固定版本的标识


def pin_id(name: str) -> str:
    pin = PINS[name]
    return pin["commit"] + "".join(f"\n{f} {sha}" for f, (_, sha) in sorted(pin["files"].items())) + "\n"


def ready(name: str) -> bool:
    """tests/.resources/<name>/ 下完整了、而且就是现在固定的版本（下到一半、固定版本换了都不算）。"""
    stamp = DIR / name / STAMP
    return name in PINS and stamp.exists() and stamp.read_text(encoding="utf-8") == pin_id(name)


def data_dir() -> Path:
    return DIR / "data" if ready("data") else paths.DATA


OCR_SUB = Path("model") / "ocr"                      # 资源包里 OCR 模型的位置（同 paths.OCR_MODEL 相对 paths.RESOURCE）


def ocr_bundle() -> Path:
    """测试用的 MaaFramework 资源包目录。"""
    gen = os.environ.get("MAAPILOT_TEST_OCR")
    if gen:
        return DIR / f"ocr-{gen}"
    return DIR / "ocr-v3" if ready("ocr-v3") else paths.RESOURCE


def ocr_gen() -> str | None:
    """现在用的 OCR 模型是哪一代："v3" / "v6"；认不出（别的版本）是 "unknown"，没有模型是 None。"""
    rec = paths.OCR_MODEL / "rec.onnx"
    if not rec.exists() or not (paths.OCR_MODEL / "det.onnx").exists():
        return None
    if paths.RESOURCE.parent == DIR and not ready(paths.RESOURCE.name):      # 下到一半的不用
        return None
    return OCR_GENS.get(sha256(rec), "unknown")


def use() -> None:
    """把 paths.DATA、paths.OCR_MODEL 指到测试用的那一份（conftest 在收集测试前调一次）。"""
    paths.DATA = data_dir()
    bundle = ocr_bundle()
    if bundle.parent == DIR and ready(bundle.name):
        _sync_pipeline(bundle)
    paths.RESOURCE = bundle
    paths.OCR_MODEL = bundle / OCR_SUB


def _sync_pipeline(bundle: Path) -> None:
    """下载的资源包里只有模型：入库的那部分资源（resource/pipeline）每次照现在的抄一份过去，凑成完整的资源包。"""
    src, dst = REPO_RESOURCE / "pipeline", bundle / "pipeline"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


def describe() -> str:
    """给 pytest 开头那几行：这次用的是哪份数据表、哪一代 OCR 模型。"""
    gen = ocr_gen()
    data = paths.DATA if has_data("character_table") else None
    return f"maapilot 测试资源：OCR 模型 {gen or '没有'}（{paths.OCR_MODEL}）；游戏数据表 {data or '没有'}"


def has_data(*tables: str) -> bool:
    return all((paths.DATA / f"{t}.json").exists() for t in tables)


def needs(present: bool, what: str):
    """用到不入库资源的测试的标记：有 → 照常跑；没有 → 跳过，`MAAPILOT_TEST_STRICT=1` 时失败（conftest 里拦）。"""
    if present:
        return pytest.mark.resource(what)
    hint = f"缺少：{what}（python -m tests.resources fetch）"
    return pytest.mark.missing_resource(hint) if STRICT else pytest.mark.skip(reason=hint)


def needs_data(*tables: str):
    return needs(has_data(*tables), "游戏数据表 " + "、".join(tables))


# --- 下载 -----------------------------------------------------------------------------

def fetch(name: str, dest: Path | None = None) -> Path:
    """下载 PINS[name] 到 dest（默认 tests/.resources/<name>/）：已经有、哈希对的不重下；哈希对不上的不留。
    全部下完、核对过才写 STAMP；没有 STAMP 的目录测试不用（下到一半、文件坏了重下又失败的）。"""
    pin = PINS[name]
    dest = dest or DIR / name
    files_dir = dest / OCR_SUB if name.startswith("ocr-") else dest
    files_dir.mkdir(parents=True, exist_ok=True)
    (dest / STAMP).unlink(missing_ok=True)
    for filename, (rel, want) in pin["files"].items():
        out = files_dir / filename
        if out.exists():
            if sha256(out) == want:
                continue
            out.unlink()                                # 坏了的不留：下面重下失败时也不会被当成好的
        url = RAW.format(repo=pin["repo"], commit=pin["commit"], path=f"{pin['dir']}/{rel}")
        print(f"下载 {name}/{filename}", file=sys.stderr, flush=True)
        tmp = out.with_name(out.name + ".part")
        _download(url, tmp)
        got = sha256(tmp)
        if got != want:
            tmp.unlink()
            raise SystemExit(f"{name}/{filename} 的 sha256 对不上：要 {want}，下到的是 {got}（{url}）")
        tmp.replace(out)
    if name == "data":
        _build_recipes(dest)
    (dest / STAMP).write_text(pin_id(name), encoding="utf-8")
    return dest


def _download(url: str, tmp: Path, tries: int = 3) -> None:
    """下到 tmp；网络一时不通再试两次，还不行就说清楚是哪个地址。"""
    req = urllib.request.Request(url, headers={"User-Agent": "maapilot-tests"})
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            return
        except OSError as e:                            # URLError、超时、断线都是 OSError
            tmp.unlink(missing_ok=True)
            if attempt == tries:
                raise SystemExit(f"下载失败（试了 {tries} 次）：{url}（{e}）") from None
            print(f"下载出错，{attempt * 5} 秒后再试：{e}", file=sys.stderr, flush=True)
            time.sleep(attempt * 5)


def _build_recipes(dest: Path) -> None:
    """recipes.json 是从 building_data.json 生成的（同 `maapilot data sync`）。"""
    from maapilot.knowledge import recipes
    old = paths.DATA
    paths.DATA = dest
    try:
        recipes.write_recipes_json()
    finally:
        paths.DATA = old


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "fetch" or any(a not in PINS for a in argv[1:]):
        print(f"用法：python -m tests.resources fetch [{' | '.join(PINS)}]…", file=sys.stderr)
        return 2
    for name in argv[1:] or list(PINS):
        print(fetch(name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
