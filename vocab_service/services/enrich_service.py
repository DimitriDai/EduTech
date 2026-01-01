# -*- coding: utf-8 -*-
"""
services/enrich_service.py

职责：
- 输入：MatchResultItem 列表（或等价结构：word + base_entry + missing_fields）
- 调用 DeepSeek：只补 missing_fields
- 输出：补全后的 Entry 列表
- 写回：global_cache.json（按 cache_mode）

强约束：
- 模型输出必须是 JSON object（纯 JSON，不要解释文字）
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from core.entry_schema import Entry, WordEntryGroup
from modules.deepseek_client import DeepSeekClient
from services.match_service import CacheMode, CacheRepo, CacheStores, VALID_FIELDS, norm_word

from services.match_service import load_json_safely  # enrich 里要重载用

# =========================
# 选择：哪些字段允许由 AI 生成
# =========================
AI_GENERATABLE_FIELDS = {
    "word_display",
    "pos_cn",
    "phonetic_uk",
    "phonetic_us",
    "definition_en",
    "example",
    "example_cn",
    "synonyms",
}


def _parse_json_obj(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _salvage_json_obj(text: str) -> Optional[Dict[str, Any]]:
    """
    兜底：从输出里捞出第一个 {...} JSON 对象
    """
    t = (text or "").strip()
    m = re.search(r"\{.*\}", t, flags=re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _has_value(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, list):
        return len(v) > 0
    if isinstance(v, dict):
        return len(v) > 0
    return True


def fill_entry_fields(entry: Entry, patch: Dict[str, Any], only_fields: List[str]) -> Entry:
    """
    只把 patch 里指定字段写进 entry。
    对 USE_CACHE：只填空不覆盖
    覆盖策略由外层决定（force overwrite 在写回层处理）
    """
    for f in only_fields:
        if f not in VALID_FIELDS:
            continue
        if f not in patch:
            continue

        val = patch[f]

        # 类型清洗
        if f == "synonyms":
            if isinstance(val, str):
                # 允许模型给 "a; b; c"
                val = [x.strip() for x in re.split(r"[;,/，；]", val) if x.strip()]
            elif isinstance(val, list):
                val = [str(x).strip() for x in val if str(x).strip()]
            else:
                val = []

        if isinstance(val, str):
            val = val.strip()

        setattr(entry, f, val)
    return entry


def build_enrich_prompt(word_original: str, base_entry: Entry, missing_fields: List[str]) -> str:
    """
    强制输出 JSON object，只包含 missing_fields（避免模型发散）
    """
    fields = [f for f in missing_fields if f in AI_GENERATABLE_FIELDS]

    # 给模型一点上下文（例如已有 pos_cn）
    context = {
        "word_original": base_entry.word_original,
        "word_norm": base_entry.word_norm,
        "word_display": getattr(base_entry, "word_display", ""),
        "pos_cn": base_entry.pos_cn,
        "definition_en": base_entry.definition_en,
        "phonetic_uk": base_entry.phonetic_uk,
        "example": base_entry.example,
        "example_cn": base_entry.example_cn,
        "synonyms": base_entry.synonyms,
    }

    return f"""
You are a vocabulary entry enricher for an IELTS learning app.

Word: {word_original}

Existing fields (may be empty):
{json.dumps(context, ensure_ascii=False)}

Task:
Generate ONLY the missing fields: {fields}

Rules:
- Output MUST be a pure JSON object (no extra text, no markdown).
- Only include keys from the missing fields list.
- word_display: ONLY include this key if you are confident it should be capitalized (nationality/language/proper noun). Otherwise, OMIT this key.
- For "phonetic_uk" (and phonetic_us if requested): use IPA with slashes, e.g. "/ˈfɑːmɪŋ/".
- For "pos_cn": concise Chinese meaning with part of speech if appropriate (e.g. "n. 碳足迹；温室气体排放量").
- For "example": a natural IELTS-style sentence.
- For "example_cn": faithful Chinese translation of the example.
- For "synonyms": JSON array of 2-4 close synonyms or near-synonyms (strings).

Return JSON only.
""".strip()


# =========================
# EnrichService
# =========================

@dataclass
class EnrichItem:
    word_original: str
    entry: Entry
    missing_fields: List[str]


class EnrichService:
    def __init__(self, stores: CacheStores, client: DeepSeekClient):
        self.stores = stores
        self.client = client
        self.global_repo = CacheRepo(stores.global_cache_path)

    def enrich_batch(
        self,
        items: List[EnrichItem],
        cache_mode: str = CacheMode.USE_CACHE,
        temperature: float = 0.2,
        max_tokens: int = 400,
    ) -> List[Entry]:

        enriched_entries: List[Entry] = []

        for it in items:
            # 过滤字段
            missing = [f for f in it.missing_fields if f in VALID_FIELDS and f in AI_GENERATABLE_FIELDS]

            # 即使没有 missing_fields，也要写回 global_cache
            if not missing:
                self._write_back(it.entry, VALID_FIELDS, cache_mode)
                enriched_entries.append(it.entry)
                continue
            prompt = build_enrich_prompt(it.word_original, it.entry, missing)

            try:
                raw = self.client.call_model(prompt, max_tokens=max_tokens, temperature=temperature)
                patch = _parse_json_obj(raw) or _salvage_json_obj(raw) or {}
            except Exception as e:
                logger.error("[ENRICH_DEEPSEEK_FAIL] %r", e)
                patch = {}

            # 只应用 missing 字段
            if patch:
                fill_entry_fields(it.entry, patch, missing)

            # 写回 global_cache（按 cache_mode）
            self._write_back(it.entry, missing, cache_mode)

            enriched_entries.append(it.entry)
        if cache_mode in (CacheMode.USE_CACHE, CacheMode.FORCE_OVERWRITE):
            # 只 save，不要在 save 前 reload
            self.global_repo.save()

        return enriched_entries

    def _write_back(self, entry: Entry, fields: List[str], cache_mode: str) -> None:
        if cache_mode == CacheMode.TEMP_ONLY:
            return

        wn = entry.word_norm or norm_word(entry.word_original)

        group = self.global_repo.get_group(wn)
        if not group:
            disp = (getattr(entry, "word_display", "") or "").strip() or wn
            group = WordEntryGroup(word_norm=wn, word_display=disp, entries=[])

        # ✅ 关键：如果这次 entry 有明确的 display，就更新 group 展示词
        new_disp = (getattr(entry, "word_display", "") or "").strip()
        if new_disp:
            group.word_display = new_disp

        if not group.entries:
            group.entries.append(entry)
            self.global_repo.upsert_group(group)
            return

        existing = group.entries[0]

        if cache_mode == CacheMode.FORCE_OVERWRITE:
            # 直接覆盖为 entry
            group.entries[0] = Entry.from_dict(entry.to_dict())
        else:
            # USE_CACHE：只填空，不覆盖已有值
            for f in fields:
                if not _has_value(getattr(existing, f, None)) and _has_value(getattr(entry, f, None)):
                    setattr(existing, f, getattr(entry, f))
            group.entries[0] = existing

        self.global_repo.upsert_group(group)


# =========================
# 本地自测入口
# =========================
if __name__ == "__main__":
    # 运行方式：python -m services.enrich_service
    # 前提：你已经设置了环境变量
    # $env:DEEPSEEK_API_KEY="..."
    # $env:DEEPSEEK_API_URL="https://api.deepseek.com/v1/chat/completions"
    # $env:DEEPSEEK_MODEL="deepseek-chat"

    stores = CacheStores(
        global_cache_path=os.path.join("storage", "global_cache.json"),
        uploaded_vocab_cache_path=os.path.join("storage", "uploaded_vocab_cache.json"),
    )

    client = DeepSeekClient()
    svc = EnrichService(stores, client)

    # 模拟来自 match_service 的结果（用你刚刚跑出来的那种）
    items = [
        EnrichItem(
            word_original="organic farming",
            entry=Entry(word_original="organic farming", word_norm="organic farming", pos_cn="有机农业"),
            missing_fields=["phonetic_uk", "example", "example_cn", "synonyms"],
        ),
        EnrichItem(
            word_original="carbon footprint",
            entry=Entry(word_original="carbon footprint", word_norm="carbon footprint"),
            missing_fields=["pos_cn", "phonetic_uk", "example", "example_cn", "synonyms"],
        ),
    ]

    enriched = svc.enrich_batch(items, cache_mode=CacheMode.USE_CACHE)

    print("\n=== ENRICH RESULTS ===")
    for e in enriched:
        print("\nWORD:", e.word_original)
        print("pos_cn:", e.pos_cn)
        print("phonetic_uk:", e.phonetic_uk)
        print("example:", e.example)
        print("example_cn:", e.example_cn)
        print("synonyms:", e.synonyms)
