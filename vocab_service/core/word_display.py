# -*- coding: utf-8 -*-
"""
core/word_display.py

目标：
- 把“展示大小写/格式”从 word_original/word_norm 的 fallback 逻辑里剥离出来
- 仅在“非常确定”时返回 word_display，否则返回空字符串（表示不要覆盖，继续用 word_norm 展示）
"""

from __future__ import annotations

import re


# -----------------------------
# 内置：高确定性白名单/规则
# 你后续可以继续加，但不要写成“全自动 Title Case”
# -----------------------------
_UPPER_ABBR = {
    "uk": "UK",
    "usa": "USA",
    "us": "US",
    "eu": "EU",
    "un": "UN",
    "u.k.": "UK",
    "u.s.": "US",
    "u.s.a.": "USA",
    "unesco": "UNESCO",
    "who": "WHO",
}

# 国家/国籍/语言的“高频小白名单”（你可以逐步补）
# key 用 word_norm（小写）来比对
_PROPER_NOUNS = {
    "africa": "Africa",
    "asia": "Asia",
    "europe": "Europe",
    "australia": "Australia",
    "antarctica": "Antarctica",
    "olympic": "Olympic",
    "olympic games": "Olympic Games",

    # Polar regions (adjectives)
    "antarctic": "Antarctic",
    "arctic": "Arctic",

    # Ocean adjectives (single token)
    "pacific": "Pacific",
    "atlantic": "Atlantic",
    "indian": "Indian",
    "southern": "Southern",

    # Common countries (add as you need)
    "china": "China",
    "france": "France",
    "germany": "Germany",
    "japan": "Japan",
    "canada": "Canada",
    "india": "India",
    "russia": "Russia",

    # Nationalities / languages (add as you need)
    "chinese": "Chinese",
    "australian": "Australian",
    "british": "British",
    "american": "American",
    "french": "French",
    "german": "German",
    "japanese": "Japanese",
    "english": "English",
    "spanish": "Spanish",
    "arabic": "Arabic",

    "france": "France",
    "china": "China",
    "australia": "Australia",
    "britain": "Britain",
    "england": "England",
    "canada": "Canada",
    "japan": "Japan",
    "korea": "Korea",
    "india": "India",
    "europe": "Europe",
    "asia": "Asia",
    "africa": "Africa",
    "america": "America",

    "chinese": "Chinese",
    "australian": "Australian",
    "british": "British",
    "french": "French",
    "english": "English",
    "american": "American",

    # Oceans
    "pacific ocean": "Pacific Ocean",
    "atlantic ocean": "Atlantic Ocean",
    "indian ocean": "Indian Ocean",
    "southern ocean": "Southern Ocean",
    "arctic ocean": "Arctic Ocean",

    # Continents / regions (multi-word)
    "north america": "North America",
    "south america": "South America",

    # Countries / political entities (multi-word / with article)
    "the usa": "the USA",
    "the u.s.a.": "the USA",
    "the united states": "the United States",
    "the uk": "the UK",
    "the united kingdom": "the United Kingdom",

    # Treaties / agreements (explicit only; do NOT infer)
    "antarctic treaty": "Antarctic Treaty",
    "paris agreement": "Paris Agreement",
    "kyoto protocol": "Kyoto Protocol",
}


def _looks_like_sentence_capitalization(word_original: str, word_norm: str) -> bool:
    """
    识别“句首误大写”的典型污染：例如 Original=Apple 但 norm=apple 且其余都是小写。
    这种情况下我们不要用 word_original 覆盖展示。
    """
    if not word_original:
        return False
    if len(word_original) < 2:
        return False
    if word_original[0].isupper() and word_original[1:].islower() and word_norm == word_original.lower():
        return True
    return False


def compute_word_display(word_original: str, word_norm: str) -> str:
    """
    返回：
    - "" ：表示不确定，不写 word_display（展示继续走 word_norm）
    - 非空：表示非常确定的规范展示（写入 word_display）

    注意：我们“宁可不改”，也不要误改。
    """
    w0 = (word_original or "").strip()
    wn = (word_norm or "").strip()

    if not wn:
        return ""

    # 1) 缩写：UK / USA / US / EU / UN / U.S. / U.K.
    #    - 如果 norm 命中，直接返回规范大写
    if wn in _UPPER_ABBR:
        return _UPPER_ABBR[wn]

    # 2) 带冠词的国家/组织：the usa / the uk / the united states
    #    - 只处理我们确定的几类
    if wn.startswith("the "):
        tail = wn[4:].strip()
        if tail in _UPPER_ABBR:
            return "the " + _UPPER_ABBR[tail]
        if tail in ("united states", "united kingdom"):
            # 这里给出“规范写法”（你可按自己口径调整）
            return "the " + tail.title()

    # 3) 单词级 proper noun 白名单：france/chinese/australian...
    if wn in _PROPER_NOUNS:
        return _PROPER_NOUNS[wn]

    # 4) 词组：只做“首词 proper noun 化”的保守规则（例如 chinese people / australian flowers）
    #    - 只在第一个词命中白名单时才改
    parts = wn.split()
    if len(parts) >= 2:
        first = parts[0]
        if first in _PROPER_NOUNS:
            parts[0] = _PROPER_NOUNS[first]
            return " ".join(parts)

        # the + abbreviation 的另一种写法：the + U.S.
        if parts[0] == "the" and parts[1] in _UPPER_ABBR:
            parts[1] = _UPPER_ABBR[parts[1]]
            return " ".join(parts)

    # 5) 句首误大写污染：不返回 display，让它走 word_norm
    if _looks_like_sentence_capitalization(w0, wn):
        return ""

    # 6) 其它情况不确定：不改
    return ""


def pick_display_word(word_display: str, word_norm: str, word_original: str) -> str:
    """
    统一“展示词”选择：
    - 优先 word_display
    - 否则 word_norm
    - 最后才兜底 original（理论上不该走到这里，但保持安全）
    """
    if (word_display or "").strip():
        return word_display.strip()
    if (word_norm or "").strip():
        return word_norm.strip()
    return (word_original or "").strip()