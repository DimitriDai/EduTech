# generator.py
# ============================================================
# 作用：
#   调用 DeepSeek 生成 IELTS Speaking 答案（6.5-7）
#
# 关键约束（已写死）：
# - Persona: 中国高中学生（在中国杭州）
# - 细节：地点/社交媒体/教育体系符合中国国情
# - 人名：PERSON'S NAME 占位符
# - 输出：Outline + Full Answer（顺序固定）
# ============================================================

import os
import re
import requests
from typing import Dict, Any, List

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


def _get_api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not found in environment variables")
    return key

def call_deepseek(
    prompt: str,
    max_tokens: int,
    temperature: float = 0.3,
    timeout: int = 90,
    frequency_penalty: float = 0.5,
    stop: list[str] | None = None,
) -> str:
    api_key = _get_api_key()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    data = {
        "model": "deepseek-chat",
        "max_tokens": int(max_tokens),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(temperature),
        "frequency_penalty": float(frequency_penalty),
    }
    if stop:
        data["stop"] = stop  # 可选：需要的话再开

    res = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=timeout)
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"].strip()

PERSONA_BLOCK = """
Persona (STRICT):
- You are a Chinese high school student studying in Hangzhou, China, preparing for IELTS exam.
- You speak in first-person ("I / my").
- If you need to mention another person's name, use exactly: PERSON'S NAME (do not invent real names).

China-context constraints (STRICT):
- Examples must be realistic in China: cities, neighborhoods, public places, school routines.
- If mentioning social media, prefer platforms commonly used in China (e.g., WeChat, QQ, Xiaohongshu). You may mention Instagram only if it is clearly framed as "some classmates use it", but do NOT rely on it.
- Education references should match a Chinese high school: classes, mock exams, coursework, teachers, clubs, school events, homework load, and NO SUCH THING AS PPTS IN CLASS.
- Avoid unrealistic Western college life, bars, or living overseas unless the question explicitly requires it.

Time-context constraits (STRICT):
- DO NOT use phrases that might be against the reality. For example, if today is January and in the answer "Well, the last time I had a few days off was during the National Day holiday last month", the "last month" will sound super weird.
- You are allowed to use "XXX Holiday" but NOT "XXX holiday last month/week"，because these words are out of context with the real situation!
- avoid details as such that might run counter to my current situation!

Fluency (LIGHT):
- Add a small amount of natural discourse markers but do NOT overuse and DO NOT keep using “Well" at the beginning!
""".strip()

import re

_END_MARK = "<<<END>>>"

def _strip_to_end_mark(text: str) -> str:
    if _END_MARK in text:
        return text.split(_END_MARK, 1)[0].strip()
    return text.strip()

def _looks_complete(text: str, part: int) -> bool:
    t = text.strip()
    if "Outline:" not in t or "Full Answer:" not in t:
        return False

    # 不允许断句：最后一个可见字符应该是 . ! ? 或者引号后面跟这些
    tail = re.sub(r"\s+", " ", t)[-3:]
    if not re.search(r"[.!?][\"')\]]?\s*$", t):
        return False

    # bullet 数量粗检
    outline_block = t.split("Outline:", 1)[1].split("Full Answer:", 1)[0]
    bullets = [ln for ln in outline_block.splitlines() if ln.strip().startswith("-")]
    if part == 2:
        if len(bullets) < 4:
            return False
    else:
        if len(bullets) < 3:
            return False

    return True

def hard_cut_part3_after_4_dots(text: str) -> str:
    if not text:
        return text

    paragraphs = text.split("\n\n")
    new_paras = []

    for p in paragraphs:
        dot_count = 0
        cut_pos = None

        for i, ch in enumerate(p):
            if ch == ".":
                dot_count += 1
                if dot_count == 4:
                    cut_pos = i
                    break

        if cut_pos is not None:
            p = p[:cut_pos + 1].rstrip()

        new_paras.append(p)

    return "\n\n".join(new_paras).strip()

def call_deepseek_checked(prompt: str, max_tokens: int, frequency_penalty: float, part: int, temperature: float = 0.3) -> str:
    # 1st try
    raw1 = call_deepseek(
        prompt,
        max_tokens=max_tokens,
        frequency_penalty=frequency_penalty,
        temperature=temperature,
        stop=[_END_MARK],   # ✅ 新增
    )
    raw1_cut = _strip_to_end_mark(raw1)
    if _looks_complete(raw1_cut, part=part):
        return raw1_cut

    # 2nd try (more conservative)
    tighten = """
If you were cut off before, fix it now:
- Keep sentences SHORT.
- Remove extra details.
- Ensure Outline + Full Answer are COMPLETE.
- Never end mid-sentence.
""".strip()

    raw2 = call_deepseek(
        prompt + "\n\n" + tighten,
        max_tokens=max_tokens,
        frequency_penalty=frequency_penalty,
        temperature=temperature,
        stop=[_END_MARK],   # ✅ 新增
    )
    raw2_cut = _strip_to_end_mark(raw2)
    return raw2_cut

def build_part1_or_3_prompt(topic: str, part: int, question: str, band: str) -> str:
    return f"""
You are an IELTS Speaking coach. Write a band {band} speaking response.

{PERSONA_BLOCK}

Task:
Topic: {topic}
Part: {part}
Question: {question}

Hard constraints (MUST follow):
- You must finish ALL required sections within the token limit.
- If you feel space is limited, shorten wording and use short sentences but DO NOT omit any required section.
- Never end mid-sentence.
- Don't keep adding extra examples.
- End your output with the exact marker: <<<END>>>

Output format (STRICT):
Outline:
- (3 bullet points, max 5 words each, key ideas only)

Full Answer (Part 1优先遵从中文的prompt):
雅思口语Part1问题回答模版：（三步走）
1. 第一时间给出明确的回答，一般疑问句类问题就直接给出肯定否定，特殊疑问句类问题直接给出所问信息；
2. 展开列举具体的表现，或者一句话简单解释原因；
3. 最后给出带有个人情绪的总结发言等，比如很放松，很有意义，觉的很麻烦，或者很有收获等。
Each answer must contain no fewer than 3 sentences and no more than 5 sentences.
注意语言要日常口语，不需要太难的词汇，用一些英文俗语表达，给出情绪和积极交流的氛围感。
请根据这样的模版标准帮我准备答案。

雅思口语Part3问题回答:
- one natural spoken answer, band {band}; concise; no extra sections;
- Each answer must contain no fewer than 3 sentences and no more than 4 sentences.
- Natural spoken style; avoid overly formal phrases and complex vocabulary;
- Use spoken idioms, slang, and spoken discourse markers appropriately (lightly).

<<<END>>>
""".strip()

def build_part2_prompt(topic: str, cue: str, bullets: List[str], band: str) -> str:
    bullets_text = "\n".join([f"- {b}" for b in bullets])
    return f"""
You are an IELTS Speaking coach. Write a band {band} Part 2 response.

{PERSONA_BLOCK}

Task:
Topic: {topic}
Cue card: {cue}
You should say:
{bullets_text}

Hard constraints (MUST follow):
- You must finish ALL required sections within the token limit.
- If you feel space is limited, keep 18 short sentences rather than chasing 210 words.
- Never end mid-sentence.
- Prioritize completeness over detail: finish the last sentence early rather than adding extra examples.
- End your output with the exact marker: <<<END>>>

Requirements:
- Outline: 4 bullet points (max 5 words each, key ideas only)
- Full Answer: one continuous talk (target 190-210 words OR 18-20 short sentences)
- Realistic, specific details but do not drag; consistent with a Chinese high school student's life
- 注意语言要日常口语，不需要太难的词汇，用一些英文俗语表达，给出情绪和积极交流的氛围感。
- Natural spoken style; avoid overly formal phrases and complex vocabulary
- Use spoken idioms, slang, and spoken discourse markers appropriately (lightly)
- Question points can be re-ordered if needed to make the narrative flow smoothly.

Output format (STRICT):
Outline:
- ...
- ...
- ...
- ...
Full Answer:
...

<<<END>>>
""".strip()


def parse_model_output(text: str) -> Dict[str, str]:
    t = (text or "").strip()
    m = re.search(r"(?is)\bOutline:\s*(.*?)\bFull Answer:\s*(.*)$", t)
    if m:
        outline = m.group(1).strip()
        full = m.group(2).strip()
        return {"outline": _clean_block(outline), "full_answer": _clean_block(full)}
    m2 = re.search(r"(?is)\bFull Answer:\s*(.*)$", t)
    if m2:
        return {"outline": "", "full_answer": _clean_block(m2.group(1).strip())}
    return {"outline": "", "full_answer": _clean_block(t)}


def _clean_block(s: str) -> str:
    # 基础清洗：去多余空行、统一 PERSON'S NAME
    s = (s or "").replace("PERSON’S NAME", "PERSON'S NAME").strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def generate_answer_part1_or_3(topic: str, part: int, question: str, band: str) -> Dict[str, Any]:
    # 为 Part 1 设置 max_tokens 为 200，Part 3 设置 max_tokens 为 220
    max_tokens = 200 if part == 1 else 220  # Part 1: 200 tokens, Part 3: 220 tokens
    # 构建生成Part 1或Part 3的Prompt
    prompt = build_part1_or_3_prompt(topic, part, question, band)
    # 调用 DeepSeek 生成答案
    raw = call_deepseek_checked(prompt, max_tokens=max_tokens, frequency_penalty=0.5, part=1 if part == 1 else 3)
    parsed = parse_model_output(raw)

    if part == 3:
        parsed["full_answer"] = hard_cut_part3_after_4_dots(
            parsed.get("full_answer", "")
        )

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
    # Part 2 设置 max_tokens 为 420
    raw = call_deepseek_checked(prompt, max_tokens=420, frequency_penalty=0.5, part=2)
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

# 展示层清洗（不改语义）

def clean_llm_output(text: str) -> str:
    """
    展示层清洗（不改语义）：
    - 删除只包含 ** 的行
    - 压缩连续空行（>=3 → 1）
    - 去掉行尾多余空格
    """
    lines = text.splitlines()
    cleaned = []
    for ln in lines:
        s = ln.rstrip()
        if re.fullmatch(r"\*{2,}", s):  # 只剩 ** 或 ****
            continue
        cleaned.append(s)

    out = "\n".join(cleaned)
    out = re.sub(r"\n{3,}", "\n\n", out)  # 连续空行压成 1 个空行
    return out.strip()