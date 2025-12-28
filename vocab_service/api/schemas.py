# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

from services.match_service import CacheMode
from core.entry_schema import Entry

from pydantic import BaseModel

from pydantic import validator

class PipelineRequest(BaseModel):
    passage_text: Optional[str] = ""
    scattered_text: Optional[str] = ""
    count: int = Field(25)

    selected_fields: List[str] = Field(default_factory=lambda: ["pos_cn","phonetic_uk","example","example_cn","synonyms"])

    # 兼容两种方式：前端传 cache_mode 或传两个开关
    cache_mode: Optional[str] = None
    force_regen: Optional[bool] = None
    temp_only: Optional[bool] = None

    def resolved_cache_mode(self) -> str:
        if self.cache_mode:
            return self.cache_mode

        # 用两个开关推导
        if self.force_regen:
            return CacheMode.TEMP_ONLY if self.temp_only else CacheMode.FORCE_OVERWRITE
        return CacheMode.USE_CACHE

class MatchRequest(BaseModel):
    words: List[str]
    selected_fields: List[str]
    cache_mode: str = CacheMode.USE_CACHE

class MatchRequest(BaseModel):
    words: List[str]
    selected_fields: List[str]
    cache_mode: str = CacheMode.USE_CACHE

    @validator("selected_fields", pre=True, always=True)
    def normalize_selected_fields(cls, v):
        return _normalize_selected_fields(v)


# -------- Responses --------

class MatchItem(BaseModel):
    word_original: str
    word_norm: str
    source_stage: str
    missing_fields: List[str]
    chosen_rule: str


class MatchResponse(BaseModel):
    items: List[MatchItem]

    @classmethod
    def from_items(cls, items):
        return cls(items=[
            MatchItem(
                word_original=i.word_original,
                word_norm=i.word_norm,
                source_stage=i.source_stage,
                missing_fields=i.missing_fields,
                chosen_rule=i.chosen_rule,
            )
            for i in items
        ])


class EntryOut(BaseModel):
    word_original: str
    word_norm: str
    phonetic_uk: str = ""
    phonetic_us: str = ""
    pos_cn: str = ""
    definition_en: str = ""
    example: str = ""
    example_cn: str = ""
    synonyms: List[str] = []


class PipelineResponse(BaseModel):
    run_id: str
    words_merged: List[str]
    match_items: List[MatchItem]
    entries: List[EntryOut]

    @classmethod
    def from_pipeline_output(cls, out):
        # out.matched 和 out.enriched_entries 顺序一致（我们 pipeline 里就是按 matched 顺序组装的）
        match_items = [
            MatchItem(
                word_original=m.word_original,
                word_norm=m.word_norm,
                source_stage=m.source_stage,
                missing_fields=m.missing_fields,
                chosen_rule=m.chosen_rule,
            )
            for m in out.matched
        ]

        entries = [
            EntryOut(
                word_original=e.word_original,
                word_norm=e.word_norm,
                phonetic_uk=e.phonetic_uk,
                phonetic_us=e.phonetic_us,
                pos_cn=e.pos_cn,
                definition_en=e.definition_en,
                example=e.example,
                example_cn=e.example_cn,
                synonyms=e.synonyms or [],
            )
            for e in out.enriched_entries
        ]

        return cls(
            run_id=out.run_id,
            words_merged=out.words_merged,
            match_items=match_items,
            entries=entries,
        )

class PracticeMasterRequest(BaseModel):
    # 为了串联流水线保留 run_id
    run_id: str  # ⬅ 必填，用于区分任务归属

    # ✅ 必填：本次 export 生成的 vocab_note.xlsx
    vocab_excel: str

    # vocab_note 的 sheet（你 excel_generator 默认就是 "vocab"）
    sheet_name: str = "vocab"

    # 输出路径（可空 -> 默认 storage/out/{run_id}_practice_master_shuffle_e2c.xlsx）
    output_xlsx: Optional[str] = ""

    # meta.json 输出路径（可空 -> 与 output_xlsx 同名 .meta.json）
    meta_json: Optional[str] = ""

    # 与 excel_generator 参数保持一致
    max_rows_per_sheet: int = 25
    base_sheet_name: str = "shuffle_e2c"
    seed: int = 0


class PracticeMasterResponse(BaseModel):
    run_id: str
    vocab_excel: str
    output_xlsx: str
    meta_json: str
    rows: int
    present_keys: List[str]
    available_practice_types: List[str]


class PracticeDocxRequest(BaseModel):
    run_id: str

    # 必填：practice_master.xlsx（表头为 key）
    master_xlsx: str

    # 输出目录（可空 -> 默认 storage/out/{run_id}/practice_docx/）
    output_dir: str = ""

    # 生成哪些类型（可空 -> 默认全生成）
    # 可选值：word_e2c, word_c2e, sent_e2c, sent_c2e
    types: List[str] = []

    # 文件名后缀（可空 -> 默认当前时间戳）
    timestamp: str = ""


class PracticeDocxResponse(BaseModel):
    run_id: str
    master_xlsx: str
    output_dir: str
    generated: List[str]          # 生成成功的 docx 路径
    skipped: List[str]            # 因缺字段跳过的类型


# ===== Audio =====

class GenerateFromVocabNoteRequest(BaseModel):
    run_id: str
    vocab_excel: str
    sheet_name: str = "vocab"
    accents: List[str] = ["uk", "us"]
    force: bool = False

    # 你要求的默认播放编排（不写回 excel）
    combo: int = 2
    timer: int = 2000

    url_mode: str = "static"
    url_prefix: str = "/static/audio"


class GenerateFromVocabNoteResponse(BaseModel):
    run_id: str
    vocab_excel: str
    total_words: int
    ok_words: int
    failed_words: int
    results: List[Dict[str, Any]]


class AudioComposeRequest(BaseModel):
    run_id: str

    # 必填：practice_master.xlsx
    master_xlsx: str

    # master 的 sheet（可空 -> 使用 orchestrator 默认 "all sheets"）
    sheet: str = ""

    # 可选：是否生成 parts（每个 sheet 单独 mp3）
    make_parts: bool = True

    # 可选：文件名前缀（默认用 master 文件名 stem）
    prefix: str = ""

    # 输出目录（可空 -> 默认 storage/out/{run_id}/audio）
    output_dir: str = ""

    # 可选：音频根目录（对应你脚本里的 audio_root，默认 storage/audio_cache）
    audio_root: str = ""

    # 可选：指定 ffmpeg/ffprobe 路径（留空就用系统 PATH）
    ffmpeg_bin: str = ""
    ffprobe_bin: str = ""

    # 是否不生成 manifest.json
    no_manifest: bool = False


class AudioComposeResponse(BaseModel):
    run_id: str
    master_xlsx: str
    output_dir: str
    generated: List[str]
    meta_json: str

# 我实在要吐了，感觉是废弃的
class FillForVocabNoteRequest(BaseModel):
    run_id: Optional[str] = ""
    vocab_excel: str
    sheet_name: str = "vocab"

    # 默认补齐 uk+us
    accents: List[str] = ["uk", "us"]

    # 本次最多补齐多少个“缺失词”（防止一次请求太久）
    max_words: int = 200

    # 是否写回 uploaded_cache（你现在一般是 uploaded 优先池）
    write_uploaded: bool = True

class FillForVocabNoteResponse(BaseModel):
    run_id: str = ""
    vocab_excel: str
    sheet_name: str
    accents: List[str]

    missing_count: int
    missing_sample: List[str] = []

    processed_words: int
    queue_path: str = ""
    detail: str = ""


class VocabNoteAudioRequest(BaseModel):
    run_id: str
    vocab_excel: str  # vocab_note.xlsx
    only_sheets: Optional[str] = "vocab"
    audio_root: Optional[str] = "storage/audio_cache"
    output_mp3: Optional[str] = ""
    ffmpeg_bin: Optional[str] = "ffmpeg"
    ffprobe_bin: Optional[str] = "ffprobe"
    no_manifest: bool = False

    # ✅ 新增：从 cache 补 audio 的来源（给你默认值，线上也能用）
    global_cache: Optional[str] = "storage/global_cache.json"
    uploaded_cache: Optional[str] = "storage/uploaded_vocab_cache.json"

    # ✅ 选择口音：默认 uk（与你目前 audio_cache 结构更一致）
    accent: Optional[str] = "uk"  # "uk" or "us"


class VocabNoteAudioResponse(BaseModel):
    run_id: str
    vocab_excel: str
    output_mp3: str
    manifest_json: str = ""
