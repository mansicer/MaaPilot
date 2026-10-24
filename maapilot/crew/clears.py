"""主线通关记录扫描（决策 D136）：终端 → 主题曲 → 乐章收录 → 章节卡 → 前往章节，沿章节地图逐个点开作战关的详情，
读星数（普通 3 星与突袭星分开）、代理指挥解锁没有、理智消耗，按返回键关掉详情接着找；做完回首页。

只读导航：只点 终端 / 主题曲 / 乐章收录（及其排序标签）/ 章节卡 / 前往章节 / 地图上的关卡名 / 地图右下的"下一章"。
从不点 开始行动、代理指挥开关、突袭 / 险地切换、演习、环境切换、任何付费入口：点关卡名之前查坐标禁区（`node_tap_ok`），
点"下一章"之前确认详情是关着的（那个位置在详情里是"开始行动"）。
突袭 / 险地模式、磨难 / 简单环境下看到的不是标准关卡：不记录，报给用户（本模块不替用户切换）。

识别移植自 MaaCore（resource/tasks/tasks.json 的任务与 resource/template/ 的模板，模板复制在 templates/maa/，见那里的 NOTICE）：
- 主题曲：StageTheme 模板（ROI 243,598,147,122）；乐章收录：OCR"乐章收录"（ClickChapterNew），排序标签照
  ClickChapterNewOpenTime / ClickChapterNewDefaultProgress；章节卡：NN@EnterEpisodeNew 模板，找不到就上下滑（SwipeUpToEpisode）；
  前往章节：OCR"前往章节"（EnterEpisodeNew-Click）。
- 地图：左右横滑在 y=100（ChapterSwipeToTheLeft / Right，避开关卡）；关卡名 OCR 的纠错照 ClickStageName 的 ocrReplace；
  章节号 OCR 的纠错照 EpisodeOcrReplace；环境文字区域照 LevelOfDifficulty。
- 详情：关卡编号区域照 ClickedCorrectStage；开始行动照 StartButton1；代理指挥按钮 UsePrts 模板、锁 UnableToAgent2 模板；
  模式：RaidDifficulty* / NormalDifficulty* 模板（ChangeToNormalDifficulty / NormalConfirm 的判定，本模块只看不点）。
- 星数：MaaCore 没有这项识别，按像素读"OPERATION 编号"右边那排六边形：蓝 = 获得，白 / 灰 = 没获得；第 4 个是突袭星。

`read_*` 是纯函数（截图 + OCR 结果），离线可测；导航函数吃 Game（鸭子类型，测试用假对象）。截图是 BGR（MaaFramework / OpenCV）。
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from maapilot.crew import nav
from maapilot.crew.screen import FULL_ROI

TEMPLATES = Path(__file__).resolve().parent / "templates" / "maa"

# --- 区域（1280x720 参考坐标；括号里是 MaaCore 的任务名） ------------------------------------
THEME_ROI = (243, 598, 147, 122)          # StageTheme
THEME_TAP = (318, 668)                    # 模板没认出时点 ROI 里的图标
CHAPTER_NEW_ROI = (991, 0, 289, 170)      # ClickChapterNew
OPEN_TIME_ROI = (987, 0, 293, 156)        # ClickChapterNewOpenTime
DEFAULT_PROGRESS_ROI = (695, 26, 85, 38)  # ClickChapterNewDefaultProgress
GO_CHAPTER_ROI = (1078, 595, 112, 42)     # EnterEpisodeNew-Click
STAGE_CODE_ROI = (845, 72, 220, 50)       # ClickedCorrectStage
STAGE_NAME_ROI = (845, 108, 175, 44)      # 编号下面的关卡名
START_ROI = (1010, 625, 260, 61)          # StartButton1
PROXY_ROI = (1000, 550, 280, 90)          # UsePrts
LOCK_ROI = (985, 523, 159, 165)           # UnableToAgent2
MODE_ROI = (600, 610, 130, 110)           # ChangeToNormalDifficulty
ENV_ROI = (26, 565, 302, 155)             # LevelOfDifficulty
AP_ROI = (1140, 668, 130, 42)             # 开始行动下面的理智消耗
MAP_BAR_ROI = (0, 600, 1280, 120)         # 章节地图底栏（推演计分 / EPISODE），同页面规则 chapter_map
EP_NOW_ROI = (1015, 625, 140, 90)         # 地图右下中间：当前显示的章节号
EP_NEXT_ROI = (1150, 620, 80, 95)         # 地图右下"下一章"
EP_NEXT_TAP = (1185, 665)
MAP_ROI = (0, 70, 1280, 550)              # 找关卡名；下沿 620 在地图底栏（推演计分、EPISODE）之上
# 整屏一次 OCR 会漏掉小字（真机：3-3 旁边有首领图标时整屏读不出，半屏能读出），分左右两半、重叠 40 像素各读一次
MAP_TILES = ((0, 70, 660, 550), (620, 70, 660, 550))
TERMINAL_CARD_ROI = (540, 95, 360, 175)   # 终端的主题曲卡："进度 15-18"
STORY_ROI = (880, 250, 260, 250)          # 剧情关弹窗的"开始剧情"
STORY_RE = r"开始剧情"
START_RE = r"开始行动|开始作战|开始推演"

# 星：六边形中心（x），纵向 STAR_Y；相邻、间距 56，第 4 个是突袭星
STAR_X = (1045, 1101, 1157, 1213)
STAR_Y = (103, 131)
STAR_HALF = 14

# 点关卡名的禁区：详情打开时右下是 代理指挥 / 开始行动 / 演习 / 突袭险地切换；地图底栏也不点
NODE_MAX_Y = 615                          # 详情里最靠上的按钮（险地 / 演习）从 628 开始
NODE_FORBIDDEN = ((985, 523, 295, 197),)

TPL_THRESHOLD = 0.8                       # MaaCore 模板的默认阈值
CARD_THRESHOLD = 0.95                     # 章节卡：别的页面上 10@ 能到 0.91，只在乐章收录页用、要求更高
LOCK_SURE, LOCK_UNSURE = 0.9, 0.85        # 锁 1.00 / 0.97，未勾的框也有 0.76：中间地带算认不准


@dataclass
class StageReading:
    """一关详情页的读数。`auto_deploy`：True 代理已解锁 / False 未解锁或这关没有代理 / None 认不准（不改记录）。"""
    code: str
    stage_id: str | None = None
    stars: int | None = None              # 普通 3 星里拿了几颗
    raid: bool | None = None              # 突袭星（第 4 个）：True / False；None = 这关没有
    auto_deploy: bool | None = None
    proxy: str | None = None              # unlocked / locked / absent / unclear
    ap_cost: int | None = None
    mode: str = "normal"                  # normal / raid（突袭或险地模式：不是标准关卡的记录）
    error: str | None = None              # code_mismatch / raid_mode / stars_unreadable / detail_not_opened / unsafe_tap
    shot: str | None = None               # 读数比记录低（调用方的 suspect 判定）时存的详情页截图

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


# --- 模板与纯识别 ----------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _template(name: str) -> np.ndarray:
    from maapilot.oscompat import fs
    img = fs.imread(TEMPLATES / f"{name}.png")          # 仓库在中文路径下也读得了
    if img is None:
        raise FileNotFoundError(TEMPLATES / f"{name}.png")
    return img


def match(img: np.ndarray, name: str, roi=FULL_ROI) -> tuple[float, tuple[int, int, int, int]]:
    """模板 `name` 在 `roi` 里的最好匹配（TM_CCOEFF_NORMED，同 MaaCore）→ (分数, 框)。区域比模板小 → (0, 区域)。"""
    tpl = _template(name)
    x, y, w, h = roi
    part = img[y:y + h, x:x + w]
    th, tw = tpl.shape[:2]
    if part.shape[0] < th or part.shape[1] < tw:
        return 0.0, roi
    res = ccoeff_normed(part, tpl)
    dy, dx = np.unravel_index(int(np.argmax(res)), res.shape)
    return float(res[dy, dx]), (x + int(dx), y + int(dy), tw, th)


def ccoeff_normed(img: np.ndarray, tpl: np.ndarray) -> np.ndarray:
    """OpenCV matchTemplate(TM_CCOEFF_NORMED) 的 numpy 版（多通道合在一起算，同 OpenCV）：
    不用 cv2，是因为 macOS 上 cv2 和 MaaFramework 各带一份 OpenCV，一起加载时 objc 会报类重复。
    相关项用 FFT 算，窗口内的和与平方和用积分图。→ (H-th+1, W-tw+1) 的分数，方差为 0 的窗口记 0。"""
    img = img.astype(np.float64)
    tpl = tpl.astype(np.float64)
    if img.ndim == 2:
        img, tpl = img[..., None], tpl[..., None]
    H, W, C = img.shape
    th, tw = tpl.shape[:2]
    n = th * tw
    oh, ow = H - th + 1, W - tw + 1
    t0 = tpl - tpl.mean(axis=(0, 1))                    # 模板去均值（每个通道）
    tnorm2 = float((t0 ** 2).sum())
    fh, fw = H + th - 1, W + tw - 1
    num = np.zeros((oh, ow))
    win2 = np.zeros((oh, ow))                           # 窗口去均值后的平方和
    for c in range(C):
        a = img[..., c]
        f = np.fft.irfft2(np.fft.rfft2(a, (fh, fw)) * np.fft.rfft2(t0[::-1, ::-1, c], (fh, fw)), (fh, fw))
        num += f[th - 1:th - 1 + oh, tw - 1:tw - 1 + ow]   # Σ T'·I（T' 均值为 0，等于 Σ T'·I'）
        s1 = _window_sum(a, th, tw)
        s2 = _window_sum(a * a, th, tw)
        win2 += s2 - s1 * s1 / n
    denom = np.sqrt(np.maximum(win2, 0) * tnorm2)
    out = np.zeros((oh, ow))
    ok = denom > 1e-6 * max(tnorm2, 1.0)
    out[ok] = num[ok] / denom[ok]
    return np.clip(out, -1.0, 1.0)


def _window_sum(a: np.ndarray, th: int, tw: int) -> np.ndarray:
    ii = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    ii[1:, 1:] = a.cumsum(0).cumsum(1)
    return ii[th:, tw:] - ii[:-th, tw:] - ii[th:, :-tw] + ii[:-th, :-tw]


def _slot(img: np.ndarray, cx: int) -> bool | None:
    """一个星位：True 蓝（获得）/ False 白或灰的六边形（没获得）/ None 这里没有六边形（底色均匀）。"""
    p = img[STAR_Y[0]:STAR_Y[1], cx - STAR_HALF:cx + STAR_HALF].reshape(-1, 3).astype(int)
    if p.size == 0:
        return None
    b, r = p[:, 0], p[:, 2]
    if ((b > 150) & (b - r > 80)).mean() > 0.3:
        return True
    if (p.min(1) > 190).mean() > 0.15 or p.mean(1).std() > 12:
        return False
    return None


def read_stars(img: np.ndarray) -> tuple[int, bool | None] | None:
    """详情页的星 → (普通星数 0-3, 突袭星 True/False/None)；认不出（不是 3 或 4 个连续星位）→ None。"""
    slots = [_slot(img, x) for x in STAR_X]
    n = next((i for i, s in enumerate(slots) if s is None), len(slots))
    if n not in (3, 4) or any(s is not None for s in slots[n:]):
        return None
    return sum(1 for s in slots[:3] if s), (slots[3] if n == 4 else None)


def read_proxy(img: np.ndarray) -> str:
    """代理指挥：unlocked（方框，勾没勾都算）/ locked（锁）/ absent（没有这个按钮，如 0 理智关）/ unclear（锁认不准）。"""
    if match(img, "UsePrts", PROXY_ROI)[0] < TPL_THRESHOLD:
        return "absent"
    lock = match(img, "UnableToAgent2", LOCK_ROI)[0]
    if lock >= LOCK_SURE:
        return "locked"
    return "unlocked" if lock < LOCK_UNSURE else "unclear"


def read_mode(img: np.ndarray) -> str:
    """常规 → "normal"（切换按钮是"突袭 / 险地作战"，或没有切换按钮）；突袭 / 险地模式 → "raid"（按钮变成切回常规）。"""
    raid = max(match(img, n, MODE_ROI)[0] for n in ("RaidDifficulty", "RaidDifficulty-Chapter15"))
    normal = max(match(img, n, MODE_ROI)[0] for n in ("NormalDifficulty", "NormalDifficulty-Chapter15"))
    return "raid" if normal >= TPL_THRESHOLD and normal > raid else "normal"


# ClickStageName / ClickedCorrectStage 的 ocrReplace（Python 写法），外加 OCR 常把"-"读成的几种横线
_CODE_FIXES = [(r"OPERATION", ""), (r"[—–一_]", "-"), (r"-\]", "-1"), (r" ", ""), (r"s", "S"), (r"c", "C"), (r"o", "O"),
               (r"g", "8"), (r"t", "T"), (r":", ""), (r"'", ""), (r"(H\d+)O(?=-)", r"\g<1>0"), (r"(-\d+)O", r"\g<1>0"),
               (r"([A-Z])0(?=-)", r"\g<1>O"), (r".*?([A-Z]{2}-EX-\d+)", r"\1"), (r"-I$", "-1")]


_CODE_RE = re.compile(r"(?<![A-Z\d])([A-Z]{0,2}\d{1,2}-\d{1,2})(?!\d)")


def norm_code(text: str) -> str:
    for pat, rep in _CODE_FIXES:
        text = re.sub(pat, rep, text)
    return text


def code_in(text: str) -> str | None:
    """OCR 文字里的关卡编号（纠错后；"OPERATION 15-3·" → "15-3"）；没有 → None。教程 TR-x 不算。"""
    m = _CODE_RE.search(norm_code(text))
    return m.group(1) if m else None


def read_episode(texts) -> int | None:
    """地图右下中间的文字（"当前进度" "EPISODE" "15"）→ 章节号；纠错照 EpisodeOcrReplace。"""
    for t in texts:
        t = re.sub(r"\s", "", t)
        t = re.sub(r"[EF]PIS[O0]DE", "EPISODE", t)
        t = re.sub(r"[Oo](\d)", r"0\1", t)
        t = re.sub(r"[Oo]$", "0", t)
        m = re.fullmatch(r"(?:EPISODE)?(\d{1,2})", t)
        if m:
            return int(m.group(1))
    return None


def read_ap(texts) -> int | None:
    for t in texts:
        m = re.search(r"-?\s*(\d{1,3})", t)
        if m:
            return int(m.group(1))
    return None


# 标题字体里 1 常被 OCR 成 T / 7 / I / l / |，也会丢掉开头的 1（真机：15-9 → "5-9"、15-12 → "T5-72"、15-14 → "5-74"）
_ONE_LIKE = frozenset("1T7IlL|")
_FUZZY_RE = re.compile(r"([0-9TIl|]{1,3})-([0-9TIl|]{1,3})")


def _part_ok(code_part: str, read: str, *, may_drop_one: bool = False) -> bool:
    """编号的一段（"15"）和读到的一段（"T5"、"5"）是否相容：1 和 7 可读成 1/T/7/I/l/|；`may_drop_one`（章节号那段）时
    开头的 1 可以丢掉。截断不算（15-17 读成 "T5-1" 不相容，只能靠关卡名）。"""
    def eq(c, r):
        return c == r or (c in "17" and r in _ONE_LIKE)
    if len(read) == len(code_part):
        return all(eq(c, r) for c, r in zip(code_part, read))
    return (may_drop_one and len(read) == len(code_part) - 1 and code_part[0] == "1"
            and all(eq(c, r) for c, r in zip(code_part[1:], read)))


def _fuzzy_parts(texts) -> list[tuple[str, str]]:
    out = []
    for t in [*texts, "".join(texts)]:
        t = norm_code(t).replace("O", "0")
        out += _FUZZY_RE.findall(t)
    return out


def _name_key(text: str) -> str:
    return re.sub(r"[^\w]", "", text or "")


def _best_name(name_texts, titles: dict[str, str]) -> tuple[str | None, float]:
    """关卡名 OCR 和本章哪一关的名字最像：相似度 ≥ 0.75 且唯一最高 → (那一关的编号, 相似度)；否则 (None, 0)。"""
    from difflib import SequenceMatcher
    read = _name_key("".join(name_texts))
    if not read or not titles:
        return None, 0.0
    scores = sorted(((SequenceMatcher(None, read, _name_key(n)).ratio(), c) for c, n in titles.items() if _name_key(n)),
                    reverse=True)
    if not scores or scores[0][0] < 0.75 or (len(scores) > 1 and scores[1][0] >= scores[0][0]):
        return None, 0.0
    return scores[0][1], scores[0][0]


def title_matches(code: str, code_texts, name_texts=(), titles: dict[str, str] | None = None) -> str | None:
    """详情标题是不是点的那一关。None = 是；否则是不认的原因。`titles` = 本章会打开详情的关卡的 {编号: 名字}（knowledge.stages.episode_titles）。
    认：编号读得和 `code` 一样；或编号只和本章的 `code` 相容（1 读成 T/7、章节号丢了开头的 1）；或关卡名认出是 `code`。
    没有 `titles` 时只认读得一样的编号（不知道本章还有哪些关，无法判断相容是否唯一）。
    不认：编号读成了本章另一关的编号；关卡名认出是另一关；编号和名字都说明不了是 `code`。宁可不记，不张冠李戴。"""
    titles = titles or {}
    exact = {c for c in (code_in(t) for t in [*code_texts, "".join(code_texts)]) if c}
    by_name, score = _best_name(name_texts, titles)
    if by_name is not None and by_name != code:
        return f"name_mismatch：名字像 {by_name}"
    if code in exact:
        return None
    if exact & (set(titles) - {code}):
        # 编号读成了另一关（真机：6-12 → "6-2"、3-1 → "3-4"、R8-1 → "R8-7"）：名字几乎一字不差是这一关才认——
        # 详情停在上一关时名字也是上一关的，不会和点的这一关对上
        if by_name == code and score >= 0.9:
            return None
        return f"code_mismatch：读到 {'/'.join(sorted(exact))}"
    if by_name == code:
        return None
    fuzzy = {c for c in titles if re.fullmatch(r"\d{1,2}-\d{1,2}", c)
             and any(_part_ok(c.split("-")[0], left, may_drop_one=True) and _part_ok(c.split("-")[1], right)
                     for left, right in _fuzzy_parts(code_texts))}
    if fuzzy == {code}:
        return None
    seen = "/".join(sorted(exact | {f"{a}-{b}" for a, b in _fuzzy_parts(code_texts)}))
    return f"code_mismatch：读到 {seen or '（空）'}" + (f"（和 {'/'.join(sorted(fuzzy))} 都相容）" if len(fuzzy) > 1 else "")


_LOOKALIKE = {("S", "5"), ("5", "S"), ("0", "O"), ("O", "0"), ("8", "B"), ("B", "8"), ("7", "Z")}   # 真机：6-17 → "6-1Z"
_LABEL_TOKEN = re.compile(r"[A-Z0-9|]{1,5}-[A-Z0-9|]{1,3}")


def _char_ok(c: str, r: str) -> bool:
    return c == r or (c in "17" and r in _ONE_LIKE) or (c, r) in _LOOKALIKE


def _label_ok(code: str, token: str) -> bool:
    """地图上的关卡名读数（"T-2"、"59-3"、"6-1Z"）和编号是否相容：逐字容许 1/7↔T/I/L/|、S↔5、0↔O、8↔B、7↔Z；
    左段可以丢开头的 1。"""
    cl, _, cr = code.partition("-")
    tl, _, tr = token.partition("-")
    if len(tr) != len(cr) or not all(_char_ok(c, r) for c, r in zip(cr, tr)):
        return False
    if len(tl) == len(cl):
        return all(_char_ok(c, r) for c, r in zip(cl, tl))
    return len(tl) == len(cl) - 1 and cl[0] == "1" and all(_char_ok(c, r) for c, r in zip(cl[1:], tl))


def label_code(text: str, universe, targets=()) -> str | None:
    """地图上一个文字框是哪一关：和本章全部编号（`universe`，含剧情关）里唯一相容的那个；相容的不止一个时，取读得一模一样的
    那个（"15-7" 和 15-1、15-7 都相容，取 15-7），再不行取其中唯一的作战关（`targets`，"T5-7" → 15-7，15-1 是剧情关）。
    认不出 → None。认错了也不会记错：点开后有详情标题核对，点到剧情关只弹剧情框。"""
    tokens = set(_LABEL_TOKEN.findall(norm_code(text).upper()))
    hits = {u for u in universe for t in tokens if _label_ok(u, t)}
    if len(hits) == 1:
        return next(iter(hits))
    exact = hits & tokens
    if len(exact) == 1:
        return next(iter(exact))
    battle = hits & set(targets)
    return next(iter(battle)) if len(battle) == 1 else None


def read_detail(img: np.ndarray, code: str, code_texts, ap_texts, name_texts=(),
                titles: dict[str, str] | None = None) -> StageReading:
    """点开 `code` 后的详情页 → 读数。标题认不出是这一关（`title_matches`）、突袭 / 险地模式、星认不出 → error，不给星数（不写记录）。"""
    r = StageReading(code=code, ap_cost=read_ap(ap_texts))
    why = title_matches(code, code_texts, name_texts, titles)
    if why is not None:
        r.error = why
        return r
    r.mode = read_mode(img)
    if r.mode != "normal":
        r.error = "raid_mode"
        return r
    stars = read_stars(img)
    if stars is None:
        r.error = "stars_unreadable"
        return r
    r.stars, r.raid = stars
    r.proxy = read_proxy(img)
    r.auto_deploy = {"unlocked": True, "locked": False, "absent": False}.get(r.proxy)
    return r


def node_tap_ok(x: int, y: int) -> bool:
    """关卡名的点击位置不在禁区里（详情的按钮区、地图底栏）。"""
    if y >= NODE_MAX_Y or y < MAP_ROI[1]:
        return False
    return not any(fx <= x < fx + fw and fy <= y < fy + fh for fx, fy, fw, fh in NODE_FORBIDDEN)


# --- 导航 --------------------------------------------------------------------------------

def _texts(hits) -> list[str]:
    return [h.text for h in hits]


def _detail_open(g, img) -> bool:
    return g.find(START_RE, START_ROI, img) is not None or g.find(STORY_RE, STORY_ROI, img) is not None


def _on_map(g, img) -> bool:
    return g.find(r"推演计分|EPISODE", MAP_BAR_ROI, img) is not None


def _wait(g, pred, timeout: float = 10, interval: float = 0.8):
    """反复截图直到 pred(img) 为真 → 那张截图；超时 → None。"""
    t0 = time.monotonic()
    while True:
        img = g.screencap()
        if pred(img):
            return img
        if time.monotonic() - t0 >= timeout:
            return None
        time.sleep(interval)


_terminal_progress: list = [None]                     # 最近一次在终端读到的主线进度 (章, 关)；open_collection 写


def read_progress(texts) -> tuple[int, int] | None:
    """终端主题曲卡上的"进度 15-18" → (15, 18)；1 常被读成 T / 7 等，按 1 处理（只用来判断章节有没有打完，读错了最多多找一次卡）。"""
    for t in texts:
        m = _FUZZY_RE.search(norm_code(t))
        if m:
            left, right = (re.sub(r"[TIl|]", "1", x) for x in m.groups())
            if left.isdigit() and right.isdigit():
                return int(left), int(right)
    return None


def _chapter_unfinished(progress) -> bool:
    """终端进度所在的章还没打到最后一关（数据表里这一章最大的编号）→ 后面的章一定没解锁。认不准 → False（照常去找卡）。"""
    if progress is None:
        return False
    ep, n = progress
    try:
        from maapilot.knowledge import stages
        last = max(int(c.split("-")[1]) for c in stages.episode_titles(ep, story=True)
                   if re.fullmatch(rf"{ep}-\d+", c))
    except Exception:                                     # noqa: BLE001 - 没有数据表 / 没有这一章
        return False
    return n < last


def open_collection(g) -> bool:
    """从任何页面到乐章收录（终端 → 主题曲 → 乐章收录，排序选默认进度）。顺便读终端上的主线进度（`_terminal_progress`）。"""
    nav.goto(g, "终端")
    img = _wait(g, lambda im: match(im, "StageTheme", THEME_ROI)[0] >= 0.7
                or g.find(r"前往上一次作战", FULL_ROI, im) is not None, timeout=10)
    if img is None:
        g.save("clears_terminal_fail")
        return False
    _terminal_progress[0] = read_progress(_texts(g.ocr(TERMINAL_CARD_ROI, img)))
    score, box = match(img, "StageTheme", THEME_ROI)       # 底栏"主题曲"；模板没认出、但确定在终端时点它的位置
    g.tap(*((box[0] + box[2] // 2, box[1] + box[3] // 2) if score >= 0.7 else THEME_TAP), delay=2.5)
    img = _wait(g, lambda im: g.find(r"乐章收录", CHAPTER_NEW_ROI, im) is not None, timeout=10)
    if img is None:
        g.save("clears_theme_fail")
        return False
    g.tap_hit(g.find(r"乐章收录", CHAPTER_NEW_ROI, img), delay=2.5)
    img = g.screencap()
    hit = g.find(r"开启时间", OPEN_TIME_ROI, img)            # 按开启时间排的视图 → 切回默认进度（MaaCore 同样处理）
    if hit is not None:
        g.tap_hit(hit, delay=1.5)
        img = g.screencap()
    hit = g.find(r"默认进度", DEFAULT_PROGRESS_ROI, img)
    if hit is not None:
        g.tap_hit(hit, delay=1.5)
    return True


def _card_moves():
    """找章节卡的滑动：先往下翻（手指上滑，同 SwipeUpToEpisode），翻不到再往回翻。"""
    return [(1250, 580, 1250, 110)] * 6 + [(1250, 110, 1250, 580)] * 10


def open_episode_card(g, ep: int) -> str | None:
    """在乐章收录里点第 `ep` 章的卡、再点"前往章节"，到章节地图。None = 到了；否则是原因。"""
    name = f"{ep:02d}@EnterEpisodeNew"
    for move in [None, *_card_moves()]:
        if move is not None:
            g.swipe(*move, duration_ms=800, delay=1.2)
        img = g.screencap()
        score, (x, y, w, h) = match(img, name, FULL_ROI)
        cx, cy = x + w // 2, y + h // 2
        if score < CARD_THRESHOLD or not 90 <= cy <= 690:
            continue
        g.tap(cx, cy, delay=2.5)
        img = _wait(g, lambda im: g.find(r"前往章节", GO_CHAPTER_ROI, im) is not None, timeout=6)
        if img is None:                                   # 点了卡没有章节简介：还没解锁
            return "not_unlocked"
        g.tap_hit(g.find(r"前往章节", GO_CHAPTER_ROI, img), delay=3.0)
        if _wait(g, lambda im: _on_map(g, im), timeout=15) is None:
            g.save(f"clears_ep{ep}_map_fail")
            return "map_not_opened"
        return None
    g.save(f"clears_ep{ep}_card_fail")
    return "card_not_found"


def current_episode(g, img) -> int | None:
    return read_episode(_texts(g.ocr(EP_NOW_ROI, img)))


def _next_episode(g, ep: int) -> bool | None:
    """在第 ep-1 章的地图上点"下一章"到第 ep 章。点之前确认详情关着（那个位置在详情里是"开始行动"）。
    True = 到了；None = 点了但还停在上一章（下一章还没解锁，按钮是灰的）；False = 没点或没到。"""
    img = g.screencap()
    if _detail_open(g, img) or g.find(r"EPISODE", EP_NEXT_ROI, img) is None or current_episode(g, img) != ep - 1:
        return False
    g.tap(*EP_NEXT_TAP, delay=3.0)
    img = _wait(g, lambda im: g.find(r"前往章节", GO_CHAPTER_ROI, im) is not None
                or (_on_map(g, im) and current_episode(g, im) == ep), timeout=8)   # 切换动画里还是上一章：等章节号变
    if img is None:
        img = g.screencap()
        if _on_map(g, img) and not _detail_open(g, img) and current_episode(g, img) == ep - 1:
            return None
        return False
    if not _on_map(g, img):                                # 先到了章节简介
        g.tap_hit(g.find(r"前往章节", GO_CHAPTER_ROI, img), delay=3.0)
        img = _wait(g, lambda im: _on_map(g, im), timeout=15)
    return img is not None and current_episode(g, img) == ep


def enter_episode(g, ep: int) -> str | None:
    """到第 `ep` 章的地图（详情关着）。已在这一章 / 在上一章点"下一章" / 否则走乐章收录。None = 到了；否则是原因。"""
    img = g.screencap()
    if _on_map(g, img) and not _detail_open(g, img):
        cur = current_episode(g, img)
        if cur == ep:
            return None
        if cur == ep - 1:
            moved = _next_episode(g, ep)
            if moved:
                return None
            if moved is None:                             # 下一章按钮点了没反应：还没解锁，不去乐章收录找（找不到也要一分钟）
                return "not_unlocked"
    _terminal_progress[0] = None
    if not open_collection(g):
        return "collection_not_opened"
    prog = _terminal_progress[0]
    if prog is not None and ep > prog[0] and _chapter_unfinished(prog):   # 进度还在前面的章：不用找卡（找不到要一分钟）
        return "not_unlocked"
    why = open_episode_card(g, ep)
    if why is not None:
        return why
    img = g.screencap()
    cur = current_episode(g, img)
    if cur is not None and cur != ep:
        g.save(f"clears_ep{ep}_wrong_episode")
        return f"wrong_episode：地图显示第 {cur} 章"
    return None


def _rewind(g) -> None:
    for _ in range(6):                                    # ChapterSwipeToTheLeft：快滑到最左（MaaCore 150ms × 5）
        g.swipe(300, 100, 900, 100, duration_ms=150, delay=0.5)
    time.sleep(0.8)


def _thumb(img: np.ndarray) -> np.ndarray:
    """比较两屏是否一样用的缩略灰度图（地图区域）。"""
    x, y, w, h = MAP_ROI
    return img[y:y + h:8, x:x + w:8].mean(axis=2)


def _same_screen(a: np.ndarray | None, b: np.ndarray) -> bool:
    """滑了但画面几乎没变 = 滑到头了（地图上的粒子动画差值很小，真滑动差值很大）。"""
    return a is not None and a.shape == b.shape and float(np.abs(a - b).mean()) < 5.0


def _forward(g) -> None:
    g.swipe(900, 100, 380, 100, duration_ms=800, delay=1.2)   # 慢滑 520 像素，惯性小，前后两屏重叠一半以上


def _close_detail(g) -> bool:
    """返回键关掉详情 → 还在地图上。返回键把地图也退掉了 → False（调用方重新进章节）。"""
    for _ in range(2):
        g.key_back(delay=1.5)
        img = g.screencap()
        if not _detail_open(g, img):
            return _on_map(g, img)
    return False


def _open_and_read(g, code: str, hit, titles: dict[str, str] | None = None) -> StageReading:
    x, y = hit.center
    if not node_tap_ok(x, y):
        return StageReading(code=code, error="unsafe_tap")
    g.tap(x, y, delay=1.5)
    img = _wait(g, lambda im: _detail_open(g, im), timeout=6, interval=0.5)
    if img is None:
        return StageReading(code=code, error="detail_not_opened")
    if g.find(STORY_RE, STORY_ROI, img) is not None:
        return StageReading(code=code, error="story")
    r = None
    for attempt in range(2):                              # 星的动画没放完、编号还没刷新：等一下再读一次
        time.sleep(0.8)
        img = g.screencap()
        r = read_detail(img, code, _texts(g.ocr(STAGE_CODE_ROI, img)), _texts(g.ocr(AP_ROI, img)),
                        _texts(g.ocr(STAGE_NAME_ROI, img)), titles)
        if r.error is None or r.error == "raid_mode":
            break
    if r.error is not None:
        g.save(f"clears_{code}")                          # maafw_debug/clears_<编号>.png：认不准的详情页，下次对照
    return r


def scan_episode(g, ep: int, codes: dict[str, str], skip: frozenset[str] = frozenset(), *, log=print,
                 titles: dict[str, str] | None = None, suspect=None) -> dict:
    """扫第 `ep` 章：`codes` = {关卡编号: stageId}（要读的作战关），`skip` 里的编号不点开；`titles` = 本章全部关卡的
    {编号: 名字}（核对详情标题，默认取数据表）；`suspect(读数)` 为真（比记录低）时趁详情还开着存截图，路径写进读数的 `shot`。
    → {status, stages: {编号: 读数}, not_found: [...], seconds}。status：done / env_not_standard / 进不去的原因。"""
    t0 = time.monotonic()
    if titles is None:
        from maapilot.knowledge import stages
        try:
            titles = stages.episode_titles(ep)
        except (stages.UnknownStageError, OSError):
            titles = {}
    out: dict = {"status": "done", "stages": {}, "not_found": []}
    why = enter_episode(g, ep)
    if why is not None:
        return {**out, "status": why, "seconds": round(time.monotonic() - t0)}
    env = _texts(g.ocr(ENV_ROI, g.screencap()))
    if any(re.search(r"磨难|简单", t) for t in env):         # 标准环境以外看到的是另一份关卡：不记，交给用户切回
        return {**out, "status": "env_not_standard", "env": env, "seconds": round(time.monotonic() - t0)}
    try:
        from maapilot.knowledge import stages
        universe = set(stages.episode_titles(ep, story=True)) | set(codes)
    except Exception:                                     # noqa: BLE001 - 没有数据表：只认计划里的编号
        universe = set(codes)
    pending = [c for c in codes if c not in skip]
    readings: dict[str, StageReading] = {}
    by_id = {sid: c for c, sid in codes.items()}

    def locked() -> set[str]:
        """前置关（数据表 unlockCondition，同一章里的）读到 0 星、或本身就锁着的：地图上不会有，不算没找到。"""
        out_: set[str] = set()
        changed = True
        while changed:
            changed = False
            for c in pending:
                if c in out_ or c in readings:
                    continue
                for pre in _prereqs(codes[c]):
                    pc = by_id.get(pre)
                    r0 = readings.get(pc) if pc else None
                    if (r0 is not None and r0.error is None and r0.stars == 0) or pc in out_:
                        out_.add(c)
                        changed = True
                        break
        return out_
    misses: dict[str, int] = {}
    deferred: set[str] = set()                            # 这一屏上位置不安全的关：滑一下再点
    bad_spots: set[tuple[int, int]] = set()               # 这一屏上点了弹出剧情框的位置（关卡名认错了）
    seen: set[str] = set()                                # 地图上认出过的编号
    frames: list = []                                     # 每屏（往右滑之前）的截图和 OCR，没找全时存下来
    second_pass = False
    _rewind(g)
    last = None
    for _ in range(len(pending) * 5 + 40):
        if all(c in readings for c in pending if c not in locked()):
            break
        img = g.screencap()
        if _detail_open(g, img):
            if not _close_detail(g):
                why = enter_episode(g, ep)
                if why is not None:
                    out["status"] = why
                    break
                _rewind(g)
                last = None
            continue
        hits = [h for roi in MAP_TILES for h in g.ocr(roi, img)]
        visible: dict[str, object] = {}
        for h in hits:
            c = label_code(h.text, universe, codes)
            if c in codes and c not in visible and h.center not in bad_spots:
                visible[c] = h
        seen |= set(visible)
        todo = sorted((visible[c].box[0], c) for c in visible
                      if c in pending and c not in readings and c not in deferred)
        if todo:
            code = todo[0][1]
            r = _open_and_read(g, code, visible[code], titles)
            r.stage_id = codes[code]
            if r.error == "unsafe_tap":
                deferred.add(code)
                continue
            if r.error == "story":                        # 认错了字、点到剧情关：这个位置不再点，编号照样待读
                bad_spots.add(visible[code].center)
            elif r.error is None or r.error == "raid_mode":
                readings[code] = r
                if r.error is None and suspect is not None and suspect(r):
                    try:                                  # maafw_debug/clears_<编号>_lower.png：给核对的 agent 看
                        r.shot = str(g.save(f"clears_{code}_lower"))
                    except Exception:                     # noqa: BLE001
                        pass
            else:
                misses[code] = misses.get(code, 0) + 1
                if misses[code] >= 2:
                    readings[code] = r
            log(f"   {code}：" + (f"{r.stars}★" + ("" if r.raid is None else f"（突袭{'✓' if r.raid else '✗'}）")
                                 + f" 代理{ {True: '已解锁', False: '未解锁'}.get(r.auto_deploy, '认不准')}"
                                 if r.error is None else r.error))
            if r.error != "detail_not_opened" and not _close_detail(g):
                why = enter_episode(g, ep)
                if why is not None:
                    out["status"] = why
                    break
                _rewind(g)
                last = None
            continue
        thumb = _thumb(img)
        if _same_screen(last, thumb):                     # 上一次往右滑之后画面没变：滑到头了
            left = [c for c in pending if c not in readings and c not in locked()]
            if second_pass or not left:
                break
            second_pass = True                            # 还有没读的：从头再过一遍（点开详情后地图会挪，可能错过了）
            log(f"   第 {ep} 章还有 {len(left)} 关没读到，从头再找一遍")
            _rewind(g)
            last = None
            continue
        last = thumb
        frames.append((img, [h.text for h in hits]))
        deferred.clear()
        bad_spots.clear()
        _forward(g)
    out["stages"] = {c: r.to_dict() for c, r in readings.items()}
    lk = locked()
    out["not_found"] = [c for c in pending if c not in readings and c not in lk]
    if lk:
        out["locked"] = sorted(lk, key=pending.index)
    if out["not_found"]:
        out["map_ocr"] = [texts for _, texts in frames]
        out["seen"] = sorted(seen & set(out["not_found"]))
        _save_frames(g, ep, frames)
    out["seconds"] = round(time.monotonic() - t0)
    return out


def _prereqs(stage_id: str) -> list[str]:
    from maapilot.knowledge import stages
    try:
        return stages.unlock_prereqs(stage_id)
    except Exception:                                     # noqa: BLE001 - 没有数据表
        return []


def _save_frames(g, ep: int, frames) -> None:
    """没找全的章：每屏地图存成 maafw_debug/clears_ep<章>_map<序号>.jpg（下次对照是 OCR 没认出还是滑过了）。"""
    from maapilot import paths
    save_to = getattr(g, "save_to", None)
    for i, (img, _) in enumerate(frames[:20]):
        try:
            if save_to is not None:
                save_to(paths.DEBUG_DIR / f"clears_ep{ep}_map{i:02d}.jpg", img)
        except Exception:                                 # noqa: BLE001 - 存图失败不影响扫描结果
            pass


def build_plan(episodes) -> dict[int, dict[str, str]]:
    """{章节号: {关卡编号: stageId}}，按数据表（knowledge.stages.episode_stages）。"""
    from maapilot.knowledge import stages
    return {ep: {s.code: s.stage_id for s in stages.episode_stages(ep)} for ep in sorted(episodes)}


def scan(g, plan: dict[int, dict[str, str]], skip: frozenset[str] = frozenset(), *, log=print, suspect=None) -> dict:
    """按 `plan`（build_plan 的格式）逐章扫，章节号升序；`skip`（关卡编号或 stageId）不点开，整章都在里面的不进。
    有一章还没解锁就停（章节按顺序解锁），后面的记 not_unlocked；在进得去的最后一章之后找不到卡、或"下一章"点了没反应，
    都算没解锁。结束回首页。`suspect` 同 scan_episode。
    → {"episodes": {章节号: scan_episode 的结果 | {"status": "complete" / "not_unlocked"}}, "home": 回没回到首页}。"""
    out: dict[int, dict] = {}
    locked = False
    latest = None                                         # 这次进得去的最后一章
    for ep in sorted(plan):
        codes = plan[ep]
        skipped = frozenset(c for c, sid in codes.items() if c in skip or sid in skip)
        if locked:
            out[ep] = {"status": "not_unlocked"}
            continue
        if codes and skipped >= set(codes):
            out[ep] = {"status": "complete"}
            continue
        log(f"第 {ep} 章：{len(codes) - len(skipped)} 关要读")
        try:
            out[ep] = scan_episode(g, ep, codes, skipped, log=log, suspect=suspect)
        except Exception as e:                            # noqa: BLE001 - 一章出错不影响后面的章
            try:
                g.save(f"clears_ep{ep}_error")
            except Exception:                             # noqa: BLE001
                pass
            out[ep] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        st = out[ep].get("status")
        if st in ("done", "env_not_standard", "complete"):
            latest = ep if latest is None else max(latest, ep)
        elif st == "card_not_found" and latest is not None and ep > latest:
            out[ep]["status"] = "not_unlocked"            # 比进得去的最后一章还靠后、又找不到卡：账号还没推到这里
        locked = out[ep].get("status") == "not_unlocked"
    try:
        home = nav.ensure_home(g)
    except Exception:                                     # noqa: BLE001
        home = False
    return {"episodes": out, "home": home}
