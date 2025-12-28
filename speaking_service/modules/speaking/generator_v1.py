# generator.py
# ============================================================
# 作用：根据 parser_std 的结构化数据，逐 question 调用 DeepSeek 生成答案
# - 风格固定：地道自然
# - 分数档固定：6.5-7（你给的）
# - 输出：Outline + Full Answer（顺序固定）
# - 人称：第一人称；如需人名，用 "PERSON'S NAME"
# - 口语特征：少量自然加入，不刻意
#
# 注意：这是“最稳、最省事”的 v1 prompt。
#       将来你要加 student profile / 目标分 / 时间挡位，都可以在这里扩展。
# ============================================================

import os
import json
import re
import requests
from typing import Dict, Any, List, Optional

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


def _get_api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not found in environment variables")
    return key


def call_deepseek(prompt: str, temperature: float = 0.35, timeout: int = 60) -> str:
    api_key = _get_api_key()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    res = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=timeout)
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"].strip()


def build_part1_or_3_prompt(topic: str, part: int, question: str, band: str) -> str:
    # 你要求：6.5-7，地道自然，第一人称，人名用占位符，少量自然口语特征
    return f"""
You are an IELTS Speaking coach.

Target band: {band}
Style: natural and native-sounding (not overly polished, not too academic).
Perspective: first-person ("I / my"). If you need to mention a person's name, use exactly: PERSON'S NAME.
Fluency features: add a small amount of natural fillers/discourse markers (e.g., "Well," "To be honest," "I guess"), but do NOT overuse them.

Task:
Generate an IELTS Speaking answer for:
Topic: {topic}
Part: {part}
Question: {question}

Output format (STRICT):
Outline:
- (3 to 5 bullet points, short and practical)
Full Answer:
(One natural spoken answer, coherent, band {band}. Do not label extra sections.)
""".strip()


def build_part2_prompt(topic: str, cue: str, bullets: List[str], band: str) -> str:
    bullets_text = "\n".join([f"- {b}" for b in bullets])
    return f"""
You are an IELTS Speaking coach.

Target band: {band}
Style: natural and native-sounding.
Perspective: first-person ("I / my"). If you need to mention a person's name, use exactly: PERSON'S NAME.
Fluency features: add a small amount of natural fillers/discourse markers, but do NOT overuse them.

Task:
Create a Part 2 cue-card response.

Topic: {topic}
Cue card: {cue}
You should say:
{bullets_text}

Requirements:
- Provide an Outline (4 to 6 bullet points).
- Provide a Full Answer: one continuous talk (about 75–110 seconds when spoken), with clear organization and natural language.
- Make it realistic and specific (a few vivid details), but not overly dramatic.

Output format (STRICT):
Outline:
- ...
Full Answer:
...
""".strip()


def parse_model_output(text: str) -> Dict[str, str]:
    """
    把模型输出拆成 Outline / Full Answer。
    允许模型偶尔多一点空行，但必须能解析出来，否则直接原样塞入 full_answer。
    """
    t = (text or "").strip()
    # 用最保守的切分：找 "Outline:" 和 "Full Answer:"
    m1 = re.search(r"(?is)\bOutline:\s*(.*?)\bFull Answer:\s*(.*)$", t)
    if m1:
        outline = m1.group(1).strip()
        full = m1.group(2).strip()
        return {"outline": outline, "full_answer": full}

    # fallback：找 Full Answer
    m2 = re.search(r"(?is)\bFull Answer:\s*(.*)$", t)
    if m2:
        return {"outline": "", "full_answer": m2.group(1).strip()}

    return {"outline": "", "full_answer": t}


def generate_answer_part1_or_3(topic: str, part: int, question: str, band: str) -> Dict[str, Any]:
    prompt = build_part1_or_3_prompt(topic, part, question, band)
    raw = call_deepseek(prompt)
    parsed = parse_model_output(raw)
    return {
        "topic": topic,
        "part": part,
        "question": question,
        "band": band,
        "style": "natural_native",
        "outline": parsed["outline"],
        "full_answer": parsed["full_answer"],
        "raw": raw,
    }


def generate_answer_part2(topic: str, cue: str, bullets: List[str], band: str) -> Dict[str, Any]:
    prompt = build_part2_prompt(topic, cue, bullets, band)
    raw = call_deepseek(prompt)
    parsed = parse_model_output(raw)
    return {
        "topic": topic,
        "part": 2,
        "cue": cue,
        "bullets": bullets,
        "band": band,
        "style": "natural_native",
        "outline": parsed["outline"],
        "full_answer": parsed["full_answer"],
        "raw": raw,
    }