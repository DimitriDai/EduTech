# -*- coding: utf-8 -*-
"""
entry_schema.py

定义系统中“Entry”的标准数据结构。
这是整个 vocab_service 的最小语义原子，必须保持长期稳定。
"""

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List


# =========================
# Entry：单条词汇释义单位
# =========================

@dataclass
class Entry:
    # ---- 核心标识 ----
    word_original: str              # 原始英文（保留大小写 / 原样）
    word_norm: str                  # 规范化 key（小写 + 去多空格）
    word_display: str = ""  # 展示用（大小写正确版本）；为空则回退 word_original/word_norm

    # ---- 发音 ----
    phonetic_uk: str = ""           # 英式音标
    phonetic_us: str = ""           # 美式音标（目前留空，但字段必须存在）

    # ---- 发音音频（离线 / CDN）----
    audio_uk: str = ""              # 英式发音音频（URL 或相对路径）
    audio_us: str = ""              # 美式发音音频（URL 或相对路径）
    audio_provider: str = ""        # piper / commonvoice / azure / none
    audio_version: str = ""         # piper_v1 / cv_v1 / ...

    # ---- 释义 ----
    pos_cn: str = ""                # 中文释义（含词性，如 "n. xxx；v. xxx"）
    definition_en: str = ""         # 英文解释（当前不强制生成）

    # ---- 用法 ----
    example: str = ""               # 英文例句
    example_cn: str = ""            # 例句翻译
    synonyms: List[str] = field(default_factory=list)

    # ---- 统计 & 决策辅助 ----
    tokens: int = 1                 # token 数（用于 match_service 决策）
    pos_count: int = 0              # 中文释义中出现的词性数量

    # ---- 来源 & 追溯 ----
    source: str = "unknown"         # uploaded | cache | deepseek
    meta: Dict[str, Any] = field(default_factory=dict)
    # meta 示例：
    # {
    #   "file": "xxx.xlsx",
    #   "sheet": "Day01",
    #   "row_index": 12
    # }

    # =========================
    # 序列化 / 反序列化
    # =========================

    def to_dict(self) -> Dict[str, Any]:
        """
        转为可 JSON 序列化的 dict（写入 global_cache）
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entry":
        """
        从 dict 还原 Entry（从 global_cache 读取）
        """
        d = dict(data or {})
        d.setdefault("word_display", "")
        return cls(**d)


# ==================================
# WordEntryGroup：同一 word 的 entry 集合
# ==================================

@dataclass
class WordEntryGroup:
    word_norm: str
    word_display: str               # 默认展示用（通常取首条 entry 的 word_original）
    entries: List[Entry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "word_norm": self.word_norm,
            "word_display": self.word_display,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WordEntryGroup":
        return cls(
            word_norm=data["word_norm"],
            word_display=data.get("word_display", data["word_norm"]),
            entries=[Entry.from_dict(e) for e in data.get("entries", [])],
        )
