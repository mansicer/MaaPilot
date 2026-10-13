"""识别结果的容错读法（不连设备、不导入 MaaFramework，可以离线测试）。

真机上快捷操作失败的一半是 OCR 读错（10-05～10-13 的 26 次里 13 次）：
- 中文标签差一个字：环烃预制体 → 环烽预制体、训练中 → 川练中、采购中心 → 采购心；
  → `best_word` / `matches`：在一份已知词表里找编辑距离最近的词（三个字以上的词最多差 1 个字），最近的不唯一就当没认出。
- "有/要"数字标签丢了斜杠：60/1 → 601、57/15 → 5715、47/12 → 47|12、8/1 → 8Z1、16/16 → 6716；
  → `binarize`：先把标签区域二值化成白底黑字再识别（细斜杠就认得出了）；
  → `parse_have_need`：认斜杠的形近字；`split_by_need`：知道"要"是几（游戏数据）时，从粘在一起的数字里切出"有"。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Optional, Sequence

# ---- 文字：编辑距离 ---------------------------------------------------------------


def substring_distance(text: str, word: str) -> int:
    """word 和 text 里最像它的一段之间的编辑距离（text 前后多出来的字不算）。"""
    if not word:
        return 0
    prev = [0] * (len(text) + 1)                      # 空 word 和 text 的任何位置距离 0（从哪开始都行）
    for i, wc in enumerate(word, 1):
        cur = [i] + [0] * len(text)
        for j, tc in enumerate(text, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wc != tc))
        prev = cur
    return min(prev)


def allowed_distance(word: str) -> int:
    """词允许差几个字：三个字（含）以上的差 1 个，更短的要一字不差（"确认"差一个字什么都像）。"""
    return 1 if len(word) >= 3 else 0


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text)


def best_word(text: str, vocab: Iterable[str]) -> Optional[str]:
    """text 里认出的是 vocab 里的哪个词：
    - 有词原样出现在 text 里 → 其中最长的（"加工站B105" → 加工站）；
    - 否则距离最近、且在允许范围内（`allowed_distance`）的词；最近的有几个（"异铁炮"离 异铁块 / 异铁组 一样近）→ None。"""
    t = _clean(text)
    words = [w for w in dict.fromkeys(vocab) if w]
    exact = [w for w in words if w in t]
    if exact:
        return max(exact, key=len)
    dist = {w: substring_distance(t, w) for w in words}
    ok = {w: d for w, d in dist.items() if d <= allowed_distance(w)}
    if not ok:
        return None
    m = min(ok.values())
    best = [w for w, d in ok.items() if d == m]
    return best[0] if len(best) == 1 else None


def matches(text: str, word: str, vocab: Iterable[str] = ()) -> bool:
    """text 认作 word：`best_word(text, vocab ∪ {word}) == word`。
    vocab 写同一处可能出现的别的词（同一列表里的其他配方名、训练位的其他状态），免得把近似的别的词认成 word。"""
    return best_word(text, [word, *vocab]) == word


# ---- 数字："有/要"标签 -------------------------------------------------------------

# 斜杠的形近字（真机读到过的）："47|12"、"8Z1"、"14:1"、"8?1"
SLASH_LIKE = "/|｜\\:：;；?？Z"
_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0"})


def parse_have_need(text: str) -> Optional[tuple[int, int]]:
    """"57/15" → (57, 15)。斜杠的形近字也算（只有一个时）；前后粘着的杂点（"c59/2"、"0/2r,"、"-1/1"）去掉。
    认不出（没有分隔符、分隔符不止一个）→ None。"""
    t = re.sub(r"^[^\dOD]*|[^\dOD]*$", "", _clean(text))   # 前后的杂点先去掉（尾巴上的小写 o 是杂点，不是 0）
    t = t.translate(_DIGIT_FIX)
    m = re.fullmatch(r"(\d+)/(\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    seps = [c for c in t if not c.isdigit()]
    if len(seps) == 1 and seps[0] in SLASH_LIKE:
        a, b = t.split(seps[0])
        if a and b:
            return int(a), int(b)
    return None


def split_by_need(text: str, need: int) -> Optional[tuple[int, int]]:
    """斜杠丢了的数字串（"2712"）按已知的"要"切开：末尾是 need 的数字、前面还有数字 → (前面, need)；否则 None。
    "要"必须来自游戏数据，不能来自同一次识别（"6716" 是 16/16 丢了开头的 1，靠它切会切出 67）。"""
    pair = parse_have_need(text)
    if pair is not None:
        return pair if pair[1] == need else None
    t = _clean(text).translate(_DIGIT_FIX).strip("-_.,:;'`~、，。")
    s = str(need)
    if t.isdigit() and len(t) > len(s) and t.endswith(s):
        return int(t[: -len(s)]), need
    return None


def vote(readings: Sequence[Optional[tuple[int, int]]]) -> Optional[tuple[int, int]]:
    """几种读法的结果投票：读到的里面最多的那个；并列（两种读法说法不同）→ None。"""
    c = Counter(r for r in readings if r is not None)
    if not c:
        return None
    top = c.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None
    return top[0][0]


# ---- 图像预处理 -------------------------------------------------------------------

def binarize(img, roi: tuple[int, int, int, int], *, threshold: int, channel: str = "max", scale: int = 1):
    """把截图里 roi 这块变成白底黑字给 OCR：亮的笔画（白字、黄字、红字）→ 黑，其余 → 白。
    channel：判断"亮"用哪个通道——"max"（RGB 最大值，白 / 红 / 黄字都亮）、"min_rg"（R、G 较小的那个：白字和黄字亮，
    蓝底不亮，专精确认框用）。scale>1 时放大后贴到一张白的 1280×720 图上（小字放大了好认）。
    → (新图, 新图上要识别的 roi)。img 是 BGR（MaaFramework 截图的格式）。"""
    import numpy as np
    x, y, w, h = roi
    reg = img[y:y + h, x:x + w].astype(np.int16)
    if channel == "max":
        v = reg.max(axis=2)
    elif channel == "min_rg":
        v = np.minimum(reg[:, :, 2], reg[:, :, 1])
    else:
        raise ValueError(f"unknown channel {channel!r}")
    sub = np.where(v >= threshold, 0, 255).astype(np.uint8)
    canvas = np.full(img.shape[:2], 255, np.uint8)
    if scale == 1:
        canvas[y:y + h, x:x + w] = sub
        out_roi = roi
    else:
        from PIL import Image
        big = np.asarray(Image.fromarray(sub).resize((w * scale, h * scale), Image.BILINEAR))
        H, W = canvas.shape
        bh, bw = min(big.shape[0], H - 40), min(big.shape[1], W - 40)
        canvas[20:20 + bh, 20:20 + bw] = big[:bh, :bw]
        out_roi = (0, 0, bw + 40, bh + 40)
    return np.ascontiguousarray(np.stack([canvas] * 3, axis=2)), out_roi
