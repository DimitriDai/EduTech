# -*- coding: utf-8 -*-
"""
services/pipeline_service.py

extract -> match -> enrich 串成一个完整后端管道
上线策略：
- 文本字段：同步 enrich（DeepSeek）
- 音频字段：只检测缺失，写入队列，离线补齐
"""

from __future__ import annotations
from core.guards import validate_inputs, normalize_and_limit_words

import os
import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from core.entry_schema import Entry
from modules.deepseek_client import DeepSeekClient

from services.extract_service import ExtractService, ExtractConfig
from services.match_service import MatchService, CacheStores, CacheMode, MatchResultItem
from services.enrich_service import EnrichService, EnrichItem

import uuid
from datetime import datetime

# =========================
# 数据结构
# =========================

@dataclass
class PipelineOutput:
    run_id: str
    words_merged: List[str]
    matched: List[MatchResultItem]
    enriched_entries: List[Entry]

# =========================
# 音频缺失 enqueue（上线兜底）
# =========================

AUDIO_FIELDS = ("audio_primary", "audio_uk", "audio_us")
AUDIO_QUEUE_PATH = os.path.join("storage", "missing_audio_queue.jsonl")


def enqueue_missing_audio(entries: List[Entry]) -> None:
    """
    检查音频字段缺失的 entry，追加写入 jsonl 队列
    - 不抛异常
    - 不阻塞主流程
    - 不去重（由离线脚本统一处理）
    """
    try:
        os.makedirs(os.path.dirname(AUDIO_QUEUE_PATH), exist_ok=True)
        with open(AUDIO_QUEUE_PATH, "a", encoding="utf-8") as f:
            for e in entries:
                word_norm = e.word_norm
                for accent in ("uk", "us"):
                    field = f"audio_{accent}"
                    if not getattr(e, field, None):
                        record = {
                            "word_norm": word_norm,
                            "word_original": e.word_original,
                            "accent": accent,
                            "source": "pipeline_runtime",
                        }
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # 任何异常都吞掉，绝不影响主流程
        pass


# =========================
# 主 Pipeline
# =========================

def run_pipeline(
    passage_text: str,
    scattered_text: str,
    count: int,
    selected_fields: List[str],
    cache_mode: str = CacheMode.USE_CACHE,
    stores: Optional[CacheStores] = None,
) -> PipelineOutput:
    """
    一个请求的完整闭环：
    1) extract
    2) match
    3) enrich（仅文本字段）
    4) enqueue missing audio（不阻塞）
    """
        # ===== run_id（本次 pipeline 的唯一标识）=====
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    stores = stores or CacheStores(
        global_cache_path=os.path.join("storage", "global_cache.json"),
        uploaded_vocab_cache_path=os.path.join("storage", "uploaded_vocab_cache.json"),
    )

    client = DeepSeekClient()

    # A) 输入校验
    validate_inputs(passage_text, scattered_text, count)

    # 1) Extract
    extractor = ExtractService(client, ExtractConfig())
    passage_words, scattered_words = extractor.extract(
        passage_text=passage_text,
        scattered_text=scattered_text,
        count=count,
    )
    merged = extractor.merge_two_sources(passage_words, scattered_words)
    merged = normalize_and_limit_words(merged)

    # 2) Match
    matcher = MatchService(stores)
    matched = matcher.match_words(merged, selected_fields, cache_mode=cache_mode)

    # 3) Enrich（只处理非音频缺失）
    enrich_items: List[EnrichItem] = []
    for m in matched:
        if m.missing_fields:
            enrich_items.append(
                EnrichItem(
                    word_original=m.word_original,
                    entry=m.entry,
                    missing_fields=m.missing_fields,
                )
            )

    enricher = EnrichService(stores, client)
    enriched_map: Dict[str, Entry] = {}

    if enrich_items:
        enriched_entries = enricher.enrich_batch(enrich_items, cache_mode=cache_mode)
        for e in enriched_entries:
            enriched_map[e.word_norm] = e

    # 4) 整理最终 entries
    final_entries: List[Entry] = []
    for m in matched:
        final_entry = enriched_map.get(m.word_norm, m.entry)
        final_entries.append(final_entry)

    # 5) 【上线关键】记录缺失音频（不影响返回）
    enqueue_missing_audio(final_entries)

    return PipelineOutput(
    run_id=run_id,
    words_merged=merged,
    matched=matched,
    enriched_entries=final_entries,
)