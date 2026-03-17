# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from typing import List, Tuple

MAX_PASSAGE_CHARS = 40000       # 你可调：防止长文
MAX_SCATTERED_CHARS = 20000     # 你可调
MAX_WORDS = 1000                 # extract 合并后最大词数（硬上限）
MAX_WORD_LEN = 60              # 单个词/短语最大长度

_WORD_OK_RE = re.compile(r"[A-Za-z]")

def validate_inputs(passage_text: str, scattered_text: str, count: int) -> None:
    if passage_text and len(passage_text) > MAX_PASSAGE_CHARS:
        raise ValueError(f"passage_text too long (>{MAX_PASSAGE_CHARS} chars)")
    if scattered_text and len(scattered_text) > MAX_SCATTERED_CHARS:
        raise ValueError(f"scattered_text too long (>{MAX_SCATTERED_CHARS} chars)")
    if count not in {15, 20, 25, 30, 50, 80, 100, 150}:
        raise ValueError("count must be one of 15/20/25/30/50/80/100/150")

def normalize_and_limit_words(words: List[str]) -> List[str]:
    out = []
    seen = set()
    for w in words:
        w = (w or "").strip()
        if not w:
            continue
        if len(w) > MAX_WORD_LEN:
            continue
        if not _WORD_OK_RE.search(w):
            continue
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
        if len(out) >= MAX_WORDS:
            break
    return out
