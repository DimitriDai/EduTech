"""
grader.py

职责：
- 把“写作批改”的核心逻辑封装成一个稳定入口：grade_ielts_essay(text, task_type=None)
- 供 FastAPI/前端调用，不直接依赖 Streamlit
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from tokenizer import basic_stats
from vocab_analysis import load_wordlist, analyze_vocab_levels
from coherence_analysis import load_linking_words, analyze_coherence
from scoring_engine import load_scoring_matrix, evaluate_band_scores

# formatter_ai.py 在你的项目里可能放在不同位置，这里做兼容导入
try:
    from Wrt_requirements.modules.formatter_ai import (
        guess_task_type,
        get_feedback_from_deepseek,
        get_ai_band_scores_from_deepseek,
        save_feedback_to_docx,
    )
except Exception:
    from formatter_ai import (
        guess_task_type,
        get_feedback_from_deepseek,
        get_ai_band_scores_from_deepseek,
        save_feedback_to_docx,
    )

BASE_DIR = os.path.dirname(__file__)

# 资源路径（按你当前项目结构：streamlit_wrt_app/Wrt_requirements/...）
WORDLIST_DIR = os.path.join(BASE_DIR, "Wrt_requirements", "wordlists")
CRITERIA_DIR = os.path.join(BASE_DIR, "Wrt_requirements", "criteria")

OX3000_PATH = os.path.join(WORDLIST_DIR, "oxford_3000.json")
C1C2_PATH = os.path.join(WORDLIST_DIR, "oxford5000_c1_c2.json")
LINKING_WORDS_PATH = os.path.join(WORDLIST_DIR, "linking_words.json")
SCORING_MATRIX_PATH = os.path.join(CRITERIA_DIR, "scoring_matrix.xlsx")

# 预加载（加速）
ox3000 = load_wordlist(OX3000_PATH)
c1c2 = load_wordlist(C1C2_PATH)
linking_words = load_linking_words(LINKING_WORDS_PATH)
scoring_df = load_scoring_matrix(SCORING_MATRIX_PATH)


def grade_ielts_essay(text: str, task_type: Optional[str] = None) -> Dict[str, Any]:
    """
    返回结构（关键字段）：
    - task_type: "Task 1"/"Task 2"
    - basic: 基础统计（含 word_count/paragraph_count）
    - metrics: 规则引擎需要的指标汇总（供你调参）
    - rule_band_scores: 规则引擎评分（参考/兜底）
    - ai_band_scores: DeepSeek 评分（主裁）
    - band_scores: 兼容字段（= ai_band_scores）
    - feedback_text: DeepSeek 逐句反馈（用于写入 docx）
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text 不能为空，且必须是字符串")

    text = text.strip()

    if task_type is None:
        task_type = guess_task_type(text)

    # 1) 基础统计
    basic = basic_stats(text)
    word_list = basic.get("word_list", [])

    # 2) 词汇分析
    vocab = analyze_vocab_levels(word_list, ox3000, c1c2)

    # 3) 衔接/连贯
    coherence = analyze_coherence(text, linking_words)

    # 4) 规则评分（参考/兜底）
    metrics = {
        **(basic or {}),
        **(vocab or {}),
        **(coherence or {}),
    }
    rule_band_scores = {}
    try:
        rule_band_scores = evaluate_band_scores(metrics, scoring_df) or {}
    except Exception:
        rule_band_scores = {}

    # 5) AI 主裁分（只要失败就返回空 dict，不要让 API 500）
    ai_band_scores = {}
    try:
        ai_band_scores = get_ai_band_scores_from_deepseek(text, task_type) or {}
    except Exception:
        ai_band_scores = {}

    # 6) 逐句反馈（也不要导致 500）
    feedback_text = ""
    try:
        feedback_text = get_feedback_from_deepseek(text, api_key=None, task_type=task_type) or ""
    except Exception:
        feedback_text = ""

    return {
        "task_type": task_type,
        "basic": {
            "word_count": basic.get("word_count"),
            "paragraph_count": basic.get("paragraph_count"),
        },
        "metrics": metrics,
        "ai_band_scores": ai_band_scores,      # 主裁
        "rule_band_scores": rule_band_scores,  # 参考/兜底
        "band_scores": ai_band_scores,         # 兼容旧字段名
        "feedback_text": feedback_text,
    }


def generate_ielts_report_docx(
    original_text: str,
    feedback_text: str,
    out_path: str,
    task_type: Optional[str] = None,
    band_scores: Optional[dict] = None,
) -> str:
    """
    兼容旧版接口：直接给原文+反馈，生成 docx
    """
    save_feedback_to_docx(
        feedback_text=feedback_text or "",
        output_path=out_path,
        task_type=task_type,
        band_scores=band_scores,
        original_text=original_text or "",
    )
    return out_path
