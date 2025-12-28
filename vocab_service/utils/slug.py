# utils/slug.py
# -*- coding: utf-8 -*-
"""
统一的 “word -> slug” 规则。
必须全站一致：生成脚本 / 后端 / 前端 都使用同一规则。
"""

from __future__ import annotations
import re

INVALID_FS_CHARS = r'<>:"/\\|?*'
_invalid_re = re.compile(f"[{re.escape(INVALID_FS_CHARS)}]")


def normalize_word(text: str) -> str:
    """去首尾空格 + 压缩多空格，用于稳定 key。"""
    s = (text or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

import unicodedata
import re

def safe_filename_from_word(word: str) -> str:
    """
    Convert word to a filesystem-safe ASCII filename.
    - remove accents (naïve -> naive)
    - lowercase
    - keep a-z 0-9 _
    """
    if not word:
        return ""

    # 1. normalize & remove accents
    word = unicodedata.normalize("NFKD", word)
    word = "".join(c for c in word if not unicodedata.combining(c))

    # 2. lowercase
    word = word.lower()

    # 3. replace non-alphanumeric with underscore
    word = re.sub(r"[^a-z0-9]+", "_", word)

    # 4. trim underscores
    return word.strip("_")