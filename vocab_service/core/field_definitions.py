# core/field_definitions.py
# 目的：
# 1) 统一字段白名单（防止 generator 各写各的）
# 2) preset -> selected_fields 的唯一入口
# 3) 输出列顺序与占位列（即使为空也要输出）
# 4) 支持 computed fields（timer/combo 用于播放编排；都是数字）
#
# 协议（锁定）：
# - timer: int，单位毫秒（例如 5000）
# - combo: int，重复播放次数（例如 2）
# - audio_primary: 优先 UK，缺失则 US（由导出层/解析层决定，不在这里拼 URL）

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Literal


# -----------------------------
# 字段分类
# -----------------------------
FieldSource = Literal["entry", "computed", "placeholder"]


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    source: FieldSource  # entry / computed / placeholder
    required_in_output: bool = False


# -----------------------------
# Entry 核心字段（必须与 entry_schema.Entry 的 key 对齐）
# -----------------------------
VALID_ENTRY_FIELDS: List[str] = [
    # identity
    "word_original",
    "word_norm",
    "word_display",
    # phonetics
    "phonetic_uk",
    "phonetic_us",
    # meaning & usage
    "pos_cn",
    "definition_en",
    "example",
    "example_cn",
    "synonyms",
    # audio (cache already has them; output just references)
    "audio_uk",
    "audio_us",
    "audio_primary",
    # optional / misc (如果你 entry_schema 里有，也可以在这里继续加)
    # "notes",
]

VALID_ENTRY_FIELD_SET: Set[str] = set(VALID_ENTRY_FIELDS)


# -----------------------------
# Computed fields（导出时生成，不写入 entry cache）
# -----------------------------
ComputedField = Literal["timer", "combo"]

VALID_COMPUTED_FIELDS: List[str] = ["timer", "combo"]
VALID_COMPUTED_FIELD_SET: Set[str] = set(VALID_COMPUTED_FIELDS)


# -----------------------------
# Placeholder（纯占位列，如 No）
# -----------------------------
VALID_PLACEHOLDER_FIELDS: List[str] = [
    "no",
]
VALID_PLACEHOLDER_FIELD_SET: Set[str] = set(VALID_PLACEHOLDER_FIELDS)


# -----------------------------
# 所有允许输出的字段（白名单）
# -----------------------------
VALID_OUTPUT_FIELDS: List[str] = (
    VALID_PLACEHOLDER_FIELDS + VALID_ENTRY_FIELDS + VALID_COMPUTED_FIELDS
)
VALID_OUTPUT_FIELD_SET: Set[str] = set(VALID_OUTPUT_FIELDS)


# -----------------------------
# Field definitions（用于 UI/列控制/校验）
# -----------------------------
FIELD_DEFS: Dict[str, FieldDef] = {
    # placeholder
    "no": FieldDef("no", "No.", "placeholder", required_in_output=False),
    # entry
    "word_original": FieldDef("word_original", "英文单词", "entry", required_in_output=True),
    "word_norm": FieldDef("word_norm", "英文单词", "entry", required_in_output=False),
    "word_display": FieldDef("word_display", "英文单词", "entry", required_in_output=False),
    "phonetic_uk": FieldDef("phonetic_uk", "英式音标", "entry", required_in_output=False),
    "phonetic_us": FieldDef("phonetic_us", "美式音标", "entry", required_in_output=False),
    "pos_cn": FieldDef("pos_cn", "中文解释", "entry", required_in_output=False),
    "definition_en": FieldDef("definition_en", "英文解释", "entry", required_in_output=False),
    "example": FieldDef("example", "例句", "entry", required_in_output=False),
    "example_cn": FieldDef("example_cn", "例句翻译", "entry", required_in_output=False),
    "synonyms": FieldDef("synonyms", "同义替换", "entry", required_in_output=False),
    # audio (entry)
    "audio_primary": FieldDef("audio_primary", "音频", "entry", required_in_output=False),
    "audio_uk": FieldDef("audio_uk", "音频(UK)", "entry", required_in_output=False),
    "audio_us": FieldDef("audio_us", "音频(US)", "entry", required_in_output=False),
    # computed
    "timer": FieldDef("timer", "timer(ms)", "computed", required_in_output=False),
    "combo": FieldDef("combo", "combo(repeat_count)", "computed", required_in_output=False),
}


# -----------------------------
# Presets（唯一入口：preset -> selected_fields）
# 注意：这里定义的是“字段 key”，不是表头中文
# -----------------------------
PRESETS: Dict[str, List[str]] = {
    # 词汇表（词汇笔记/词库导出）：是否要音频取决于你用途，这里默认加 audio_primary
    "vocab_note_excel": [
        "no",
        "word_original",
        "phonetic_uk",
        "phonetic_us",
        "pos_cn",
        "definition_en",
        "example",
        "example_cn",
        "synonyms",
        "audio_primary",
    ],
    # 乱序英译中（练习用）：强烈建议包含音频+timer+combo
    "shuffle_e2c_excel": [
        "no",
        "word_original",
        "pos_cn",
        "audio_primary",
        "timer",
        "combo",
    ],
}


# -----------------------------
# 校验与工具函数
# -----------------------------
def validate_selected_fields(selected_fields: List[str]) -> List[str]:
    """
    过滤非法字段，并去重但保留顺序。
    """
    if not selected_fields:
        return []

    seen: Set[str] = set()
    out: List[str] = []
    for k in selected_fields:
        if k not in VALID_OUTPUT_FIELD_SET:
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def get_fields_for_preset(preset_name: str) -> List[str]:
    if preset_name not in PRESETS:
        raise KeyError(f"Unknown preset: {preset_name}")
    return list(PRESETS[preset_name])


def get_vocab_note_excel_columns(selected_fields: List[str]) -> List[str]:
    """
    词汇表导出列：
    - 以 selected_fields 为准（由 preset/UI 传入）
    - 默认确保 word_original 在内（最基本可读性）
    """
    cols = validate_selected_fields(selected_fields)
    if "word_original" not in cols:
        cols.insert(0, "word_original")
    return cols


def get_shuffle_e2c_excel_columns(selected_fields: List[str]) -> List[str]:
    """
    乱序英译中练习导出列：
    - 必须包含 word_original（练习主体）
    - 建议包含 audio_primary + timer + combo（播放编排）
    """
    cols = validate_selected_fields(selected_fields)

    if "word_original" not in cols:
        cols.insert(0, "word_original")

    # 练习默认要的编排字段，如果你在 UI/preset 里没选，也给你补上（更“永远可用”）
    for must in ["audio_primary", "timer", "combo"]:
        if must not in cols:
            cols.append(must)

    return cols


# -----------------------------
# Sheet 分页规则（你新增：乱序英译中excel 每个sheet最多25条）
# -----------------------------
def chunk_size_for_output(output_kind: str) -> int:
    """
    给 generator 用的分页规则。
    - shuffle_e2c_excel: 25/Sheet
    - 其他：默认不分页（返回一个很大值）
    """
    if output_kind == "shuffle_e2c_excel":
        return 25
    return 10**9