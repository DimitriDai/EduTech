# -*- coding: utf-8 -*-
"""
extract_service.py

Step 1: 提取英文词汇（只返回英文单词/短语列表）
- 支持篇章文本提取重点词
- 支持零散词汇输入提取/清洗
- 输出必须是 list[str]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from modules.deepseek_client import DeepSeekClient


# 允许的数量选项（按需求）
ALLOWED_COUNTS = {15, 20, 25, 30, 50}


@dataclass
class ExtractConfig:
    default_count: int = 25
    max_tokens: int = 300
    temperature: float = 0.2


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        key = x.lower().strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(x.strip())
    return out


def _clean_candidate(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    # 去掉常见编号/前缀
    s = re.sub(r"^[\-\*\d\.\)\]\：\:\s]+", "", s).strip()
    # 去掉结尾多余标点
    s = re.sub(r"[，,。\.!！\?？;；:\s]+$", "", s).strip()
    return s


def _parse_json_list(text: str) -> Optional[List[str]]:
    """
    期望模型输出形如：
    ["word", "phrase", ...]
    """
    t = (text or "").strip()
    # 有些模型会把 JSON 包在 ```json ``` 里
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)

    try:
        obj = json.loads(t)
        if isinstance(obj, list):
            return [str(x) for x in obj]
    except Exception:
        return None
    return None


def _salvage_list(text: str) -> List[str]:
    """
    JSON 解析失败时兜底：从文本里捞出可能的词汇项
    支持：
    - 1. sustainable
    - - carbon footprint
    - sustainable; carbon footprint; ...
    """
    t = (text or "").strip()
    # 按行切
    parts = []
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        # 去掉引号
        line = line.strip().strip('"').strip("'")
        # 如果一行里有多个分隔符
        if re.search(r"[;；|｜/／,，]", line):
            for p in re.split(r"[;；|｜/／,，]", line):
                p = _clean_candidate(p)
                if p:
                    parts.append(p)
        else:
            p = _clean_candidate(line)
            if p:
                parts.append(p)

    # 过滤：必须包含英文字母
    parts = [p for p in parts if re.search(r"[A-Za-z]", p)]
    return _dedupe_keep_order(parts)


def build_passage_prompt(passage: str, count: int) -> str:
    return f"""
You are an IELTS vocabulary extractor.

Task:
Extract EXACTLY {count} important English words/phrases from the passage.

Rules:
- Output MUST be a pure JSON array of strings. No extra text.
- Each item should be an English word or an English phrase (2-5 words).
- No explanations, no translations, no numbering.
- Prefer high-value vocabulary and useful collocations.
- Keep the original form used in the passage when appropriate.

Passage:
{passage}
""".strip()


def build_scattered_prompt(raw_words: str) -> str:
    return f"""
You are a vocabulary cleaner.

Task:
From the input, extract English words/phrases only.

Rules:
- Output MUST be a pure JSON array of strings. No extra text.
- Deduplicate obvious repeats (case-insensitive).
- Ignore any Chinese, numbers, and explanations.
- Keep phrases together if they form a unit (e.g., "carbon footprint").

Input:
{raw_words}
""".strip()


class ExtractService:
    def __init__(self, client: DeepSeekClient, config: Optional[ExtractConfig] = None):
        self.client = client
        self.config = config or ExtractConfig()

    def extract(
        self,
        passage_text: str = "",
        scattered_text: str = "",
        count: int = 25,
    ) -> Tuple[List[str], List[str]]:
        """
        返回 (passage_words, scattered_words)
        - passage_words: 从篇章提取的重点词（最多 count）
        - scattered_words: 从零散输入清洗得到的词（不限制数量，但会去重）
        """
        print("[EXTRACT_IN]", (passage_text or "")[:60], "|", scattered_text)

        if count not in ALLOWED_COUNTS:
            count = self.config.default_count

        passage_words: List[str] = []
        scattered_words: List[str] = []

        # 1) 篇章提取
        if passage_text and passage_text.strip():
            prompt = build_passage_prompt(passage_text.strip(), count=count)
            raw = self.client.call_model(prompt, max_tokens=self.config.max_tokens, temperature=self.config.temperature)
            parsed = _parse_json_list(raw)
            if parsed is None:
                parsed = _salvage_list(raw)
            passage_words = _dedupe_keep_order([_clean_candidate(x) for x in parsed])[:count]
            print("[EXTRACT_P_WORDS_HEAD]", passage_words[:10], "len=", len(passage_words))

        # 2) 零散输入清洗
        if scattered_text and scattered_text.strip():
            prompt = build_scattered_prompt(scattered_text.strip())
            raw = self.client.call_model(prompt, max_tokens=self.config.max_tokens, temperature=self.config.temperature)
            parsed = _parse_json_list(raw)
            if parsed is None:
                parsed = _salvage_list(raw)
            scattered_words = _dedupe_keep_order([_clean_candidate(x) for x in parsed])
            print("[EXTRACT_S_WORDS_HEAD]", scattered_words[:10], "len=", len(scattered_words))

        return passage_words, scattered_words

    def merge_two_sources(self, passage_words: List[str], scattered_words: List[str]) -> List[str]:
        """
        需求案：两个输入框得到的英文单词合并成一个 list。
        这里做：保序去重（先 passage，再 scattered）
        """
        return _dedupe_keep_order(passage_words + scattered_words)
        print("[EXTRACT_MERGED_HEAD]", merged[:20], "len=", len(merged))