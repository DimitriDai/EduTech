# -*- coding: utf-8 -*-
"""
extract_service.py

Step 1: 提取英文词汇（只返回英文单词/短语列表）
- 支持篇章文本提取重点词（LLM 可选）
- 支持零散词汇输入提取/清洗（默认本地解析，不依赖 LLM）
- 输出必须是 list[str]
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from modules.deepseek_client import DeepSeekClient


# 允许的数量选项（按需求）
ALLOWED_COUNTS = {10, 15, 20, 25, 30, 50}

# Debug 开关：线上默认关闭；本地需要看日志就 set VOCAB_DEBUG=1
DEBUG = os.getenv("VOCAB_DEBUG", "0").strip() == "1"


@dataclass
class ExtractConfig:
    default_count: int = 25
    # 最大 token 数量（用于 LLM）
    max_tokens: int = 1000
    temperature: float = 0.2
    # passage 抽取是否启用 LLM（建议 True；如果你想完全离线，可置 False）
    enable_llm_for_passage: bool = True
    # scattered 是否启用 LLM（建议 False；本地解析更稳定更快）
    enable_llm_for_scattered: bool = True


def _log(*args) -> None:
    if DEBUG:
        print(*args)


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        key = (x or "").lower().strip()
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

    # 去掉常见编号/前缀： 1. / - / * / (1) 等
    s = re.sub(r"^[\-\*\d\.\)\]\：\:\s]+", "", s).strip()

    # 去掉结尾多余标点
    s = re.sub(r"[，,。\.!！\?？;；:\s]+$", "", s).strip()
    return s

def _is_english_dominant_term(s: str) -> bool:
    """
    True: 看起来是英文词/短语（允许连字符、空格、点号缩写、撇号等）
    False: 英文只是少量缩写，主体是中文（eg.时间 / abc中文）
    """
    s = (s or "").strip()
    if not s:
        return False

    # 至少要有一个英文单词（含连字符）
    # 例如: "brother-in-law", "crack the code", "eg." 也算单词但太短
    words = re.findall(r"[A-Za-z][A-Za-z\-']*", s)
    if not words:
        return False

    # 英文字母数量 vs 中文数量
    en = len(re.findall(r"[A-Za-z]", s))
    zh = len(re.findall(r"[\u4e00-\u9fff]", s))

    # 如果含中文且英文很少（典型 eg.时间），判为 False
    if zh > 0 and en < 4:
        return False

    # 如果中文数量 >= 英文数量，也判为 False（英文不占主导）
    if zh >= en:
        return False

    return True

def _norm(s: str) -> str:
    """用于包含关系判断：小写 + 归一空格"""
    return re.sub(r"\s+", " ", (s or "").strip().lower())
def _looks_like_english(x: str) -> bool:
    """判断是否像一个英文词/短语（而不是中文解释）"""
    if not x:
        return False
    if not re.search(r"[A-Za-z]", x):
        return False
    # 含中文就不要拆（避免把解释拆碎）
    if re.search(r"[\u4e00-\u9fff]", x):
        return False
    return True


def _split_connectors(p: str) -> List[str]:
    """
    拆类似：
    - word vs word
    - word = word
    - word -> word
    - word - word（谨慎：左右都像英文才拆）
    """
    p = re.sub(r"\s+", " ", (p or "").strip())
    if not p:
        return []

    # 统一 vs / vs.
    p = re.sub(r"\bvs\.?\b", " vs ", p, flags=re.IGNORECASE)
    # 统一箭头
    p = p.replace("→", "->")

    patterns = [
        r"\s+vs\s+",
        r"\s*=\s*",
        r"\s*->\s*",
        r"\s+[—\-]\s+",   # 破折号/连字符
    ]

    for pat in patterns:
        if re.search(pat, p, flags=re.IGNORECASE):
            chunks = [c.strip() for c in re.split(pat, p) if c.strip()]
            if len(chunks) >= 2 and all(_looks_like_english(c) for c in chunks):
                return chunks

    return [p]


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


# 结构符号：bullet / 缩进 / 序号
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•]+|\d+[.)、])\s*")

# 英文括号 + 中文括号，抓取括号内内容（不做贪婪跨行）
_PAREN_CONTENT_RE = re.compile(r"\(([^()]{1,200})\)|（([^（）]{1,200})）")

# 冒号结构：保留冒号右侧
_COLON_SPLIT_RE = re.compile(r"[:：]")

# 你原来的 stop substrings 保留即可
_STOP_SUBSTRINGS = {
    "pure json", "json array", "no extra text", "rules", "task",
    "extract", "passage", "input", "output", "strings",
    "collocation", "collocations", "important english",
    "useful collocations", "high-value vocabulary",
}

def _looks_like_prompt_noise(s: str) -> bool:
    low = s.lower()
    return any(x in low for x in _STOP_SUBSTRINGS)

def _strip_structure_prefix(line: str) -> str:
    line = line.strip()
    line = _BULLET_PREFIX_RE.sub("", line)
    # 去掉像 "中文：" "英文：" 这种标签前缀（只去掉最前面的）
    line = re.sub(r"^(?:中文|英文|释义|翻译)\s*[:：]\s*", "", line.strip())
    return line.strip()

def _extract_english_from_parentheses(line: str) -> List[str]:
    """
    提取 () 或 （） 内的内容；只保留含英文字母的片段
    """
    hits: List[str] = []
    for m in _PAREN_CONTENT_RE.finditer(line):
        cand = (m.group(1) or m.group(2) or "").strip()
        if not cand:
            continue
        if not re.search(r"[A-Za-z]", cand):
            continue
        hits.append(cand)
    return hits

def _split_scattered_locally(raw_words: str) -> List[str]:
    """
    零散输入本地解析（增强版）
    - 兼容大纲/中文标题 + 括号英文清单 + 冒号结构
    - 优先提取括号内英文；冒号结构只取右侧
    """
    t = (raw_words or "").strip()
    if not t:
        return []

    parts: List[str] = []

    for raw_line in t.splitlines():
        line = _strip_structure_prefix(raw_line)
        if not line:
            continue

        # 去掉引号
        line = line.strip().strip('"').strip("'").strip()
        if not line:
            continue

        # 0) 强力过滤 prompt 噪音（避免把指令词塞进词汇）
        if _looks_like_prompt_noise(line):
            continue

        # 1) 优先：抽取括号内英文（() / （））
        paren_hits = _extract_english_from_parentheses(line)
        for h in paren_hits:
            # 括号内通常是清单：speed，efficiency，economy...
            # 先把省略号/……/... 清掉，避免残留
            h = re.sub(r"\.{2,}|…{1,}|，\s*\.\.\.|,\s*\.\.\.", "", h)
            # 基础分隔符拆分（保留你原逻辑）
            for p in re.split(r"[;；|｜/／,，、\n]+", h):
                p = _clean_candidate(p)
                if not p:
                    continue
                if _looks_like_prompt_noise(p):
                    continue
                for item in _split_connectors(p):
                    item = _clean_candidate(item)
                    if not item:
                        continue
                    # 必须含英文字母
                    if not _is_english_dominant_term(item):
                        continue
                    parts.append(item)

        # 2) 其次：处理冒号结构（只取右侧）
        # 例如：individuals: penalty, raise eco-friendly awareness
        # 注意：如果这一行主要信息在括号里，已经在上面抽过了；这里再兜底。
        if _COLON_SPLIT_RE.search(line):
            segs = _COLON_SPLIT_RE.split(line, maxsplit=1)
            rhs = segs[1].strip() if len(segs) == 2 else ""
            if rhs and re.search(r"[A-Za-z]", rhs):
                for p in re.split(r"[;；|｜/／,，、\n]+", rhs):
                    p = _clean_candidate(p)
                    if not p:
                        continue
                    if _looks_like_prompt_noise(p):
                        continue
                    for item in _split_connectors(p):
                        item = _clean_candidate(item)
                        if not item:
                            continue
                        if not _is_english_dominant_term(item):
                            continue
                        parts.append(item)

        # 3) 最后：整行兜底（只在“整行本身含英文”时才拆）
        # 这样可以避免把“核心话题/科技与社媒”这种中文标题塞进去
        if re.search(r"[A-Za-z]", line):
            # 去掉括号内容后再拆，避免重复
            no_paren = _PAREN_CONTENT_RE.sub(" ", line)
            for p in re.split(r"[;；|｜/／,，、\n]+", no_paren):
                p = _clean_candidate(p)
                if not p:
                    continue
                if _looks_like_prompt_noise(p):
                    continue
                for item in _split_connectors(p):
                    item = _clean_candidate(item)
                    if not item:
                        continue
                    if not _is_english_dominant_term(item):
                        continue
                    parts.append(item)

    return _dedupe_keep_order(parts)

def _scattered_llm_extract_english_prefix(s: str) -> str:
    """
    从 LLM 返回的字符串里，尽可能“只取英文部分”。
    例：
      "Brother-in-law 姐妹的老公" -> "Brother-in-law"
      "Remian 作的是系动词+adj." -> "Remian"  （但后续会因为疑似拼写/过短/噪音被过滤可选）
      "Crack the code 破解密码" -> "Crack the code"
    """
    s = (s or "").strip()
    if not s:
        return ""

    # 先去掉 tab 右侧（scattered 常见）
    s = s.split("\t", 1)[0].strip()

    # 若包含中文，优先截断到中文前
    m_ch = re.search(r"[\u4e00-\u9fff]", s)
    if m_ch:
        s = s[:m_ch.start()].strip()

    # 再用“英文前缀”正则兜底：允许字母数字、空格、连字符、撇号、点、斜杠
    m = re.match(r"^[A-Za-z0-9][A-Za-z0-9\s\-\’\'\.\,/]*", s)
    if not m:
        return ""
    out = _clean_candidate(m.group(0))

    # 去掉结尾杂符号
    out = out.strip(" ,.;:/\\-").strip()
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _normalize_scattered_llm_item(s: str) -> str:
    """
    LLM 纠错/筛选后的最终归一化：
    - 只保留英文前缀
    - 可选：去掉冠词 the/a/an（避免 The lead 这种变体造成去重困难）
    """
    s = _clean_candidate(s)
    s = _scattered_llm_extract_english_prefix(s)

    # 去掉冠词（可选，但很推荐，能显著减少“the + 名词”重复）
    s = re.sub(r"^(?:the|a|an)\s+", "", s, flags=re.IGNORECASE).strip()

    # 再 clean 一次
    s = _clean_candidate(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_valid_scattered_llm_item(s: str) -> bool:
    """
    强过滤：宁可少收，不要脏数据。
    """
    if not s:
        return False

    # 必须含英文字母
    if not re.search(r"[A-Za-z]", s):
        return False

    # 绝对不允许中文
    if re.search(r"[\u4e00-\u9fff]", s):
        return False

    # 长度上限：过长一般是句子/解释
    if len(s) > 60:
        return False

    # 明显结构噪音/提示语碎片
    low = s.lower()
    if "eg." in low or "e.g." in low:
        return False

    # 含过多符号（像句子/代码/说明）
    if re.search(r"[：:（）\(\)\[\]{}<>]", s):
        return False

    # 太短（1-2字符）不要
    if len(s) <= 2:
        return False

    # 复用你已有的 prompt 噪音黑名单
    if _looks_like_prompt_noise(s):
        return False

    return True


def _filter_scattered_llm_words(words: List[str]) -> List[str]:
    cleaned: List[str] = []
    for w in words or []:
        w2 = _normalize_scattered_llm_item(w)
        if not w2:
            continue
        if not _is_valid_scattered_llm_item(w2):
            continue
        cleaned.append(w2)
    return _dedupe_keep_order(cleaned)


def _is_garbage_token(x: str) -> bool:
    nx = _norm(x)
    if not nx:
        return True
    # 太短的一般无意义（比如单个字母）
    if len(nx) <= 1:
        return True
    # 不能包含明显的 JSON/指令片段
    for bad in _STOP_SUBSTRINGS:
        if bad in nx:
            return True
    return False


def _filter_passage_words_by_text(words: List[str], passage_text: str) -> List[str]:
    """
    关键保险丝：passage 抽取结果必须“确实出现在 passage 里”
    （这样模型复述指令、胡写都不会进入系统）
    """
    low = _norm(passage_text)
    out: List[str] = []
    for w in words:
        w2 = _clean_candidate(w)
        if _is_garbage_token(w2):
            continue
        if not w2:
            continue
        if _norm(w2) in low:
            out.append(w2)
    return _dedupe_keep_order(out)


def _salvage_list(text: str) -> List[str]:
    """
    JSON 解析失败时兜底：从文本里捞出可能的词汇项
    ⚠️ 注意：兜底后的结果仍需进一步过滤（尤其 passage 必须出现在原文中）
    """
    t = (text or "").strip()
    if not t:
        return []

    parts: List[str] = []
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        line = line.strip().strip('"').strip("'")

        # 一行里多个分隔符
        if re.search(r"[;；|｜/／,，]", line):
            for p in re.split(r"[;；|｜/／,，]", line):
                p = _clean_candidate(p)
                if p:
                    parts.append(p)
        else:
            p = _clean_candidate(line)
            if p:
                parts.append(p)

    parts = [p for p in parts if re.search(r"[A-Za-z]", p)]
    parts = [p for p in parts if not _is_garbage_token(p)]
    return _dedupe_keep_order(parts)


def build_passage_prompt(passage: str, count: int) -> str:
    # 更“强硬”的约束：system + user 合并为一个 prompt 时，也要写清楚“只允许 JSON”
    return f"""
You are a strict JSON generator.

Extract EXACTLY {count} English vocabulary items (single words or multi-word phrases) from the passage.

Hard rules:
- Output ONLY a valid JSON array of strings. No extra text, no markdown, no code fences.
- Each item MUST appear in the passage verbatim (case-insensitive is OK).
- Do NOT include meta words like "JSON", "array", "task", "rules", "extract", "passage", etc.
- Prefer key words, usually nouns, verbs, phrases, and adjs (that have a point) that reflect the main ideas and important messages of the passage.
- Prefer useful collocations and higher-value vocabulary. Do not emphasize on very common words or names of people/places.

Passage:
<<<
{passage}
>>>
""".strip()

def build_scattered_norm_prompt(words: list[str]) -> str:
    return f"""
You are a strict JSON generator.

Task:
Normalize each item in the input list WITHOUT removing any item.

Rules:
- Output MUST be a pure JSON array of objects. No extra text.
- Output length MUST equal input length.
- Each object MUST have keys: "input", "norm".
- "input" MUST exactly equal the corresponding input string.
- "norm" is the best normalized English word/phrase (typo fix, remove Chinese explanation, remove leading articles like "the/a/an" if appropriate).
- If you are not sure, set "norm" to "" (empty string), but NEVER drop an item.
- Do NOT add new items. Do NOT deduplicate. Do NOT reorder.

Input JSON:
{json.dumps(words, ensure_ascii=False)}
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
        - scattered_words: 从零散输入清洗得到的词（去重）
        """
        _log("[EXTRACT_IN]", (passage_text or "")[:60], "|", scattered_text)

        if count not in ALLOWED_COUNTS:
            count = self.config.default_count

        passage_words: List[str] = []
        scattered_words: List[str] = []

        # 1) scattered：must-keep，本地先解析成词条列表；LLM 只能做规范化映射，不能删
        if scattered_text and scattered_text.strip():
            local_words = _split_scattered_locally(scattered_text)
            _log("[EXTRACT_S_WORDS_HEAD_LOCAL]", local_words[:10], "len=", len(local_words))

            # must-keep：默认直接保留全部（顺序不变）
            scattered_words = local_words[:]

            # LLM：只做逐项规范化（映射回填），失败则保持 local_words 原样
            if self.config.enable_llm_for_scattered and local_words:
                prompt = build_scattered_norm_prompt(local_words)
                raw = self.client.call_model(
                    prompt,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                )

                try:
                    obj = json.loads((raw or "").strip())
                except Exception:
                    obj = None

                ok = True
                norms: List[str] = []

                if not isinstance(obj, list) or len(obj) != len(local_words):
                    ok = False
                else:
                    for i, item in enumerate(obj):
                        if not isinstance(item, dict):
                            ok = False
                            break
                        if item.get("input") != local_words[i]:
                            ok = False
                            break
                        norm = (item.get("norm") or "").strip()
                        norms.append(norm)

                if ok:
                    scattered_words = [
                        norms[i] if norms[i] else local_words[i]
                        for i in range(len(local_words))
                    ]

            _log("[EXTRACT_S_WORDS_HEAD_NORMED]", scattered_words[:10], "len=", len(scattered_words))

        # 2) passage：可用 LLM 抽取，但必须“落回原文验证”
        if passage_text and passage_text.strip() and self.config.enable_llm_for_passage:
            prompt = build_passage_prompt(passage_text.strip(), count=count)
            raw = self.client.call_model(
                prompt,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature
            )

            parsed = _parse_json_list(raw)
            if parsed is None:
                # 兜底可以保留，但最终会被“必须出现在原文”过滤
                parsed = _salvage_list(raw)

            cand = _dedupe_keep_order([_clean_candidate(x) for x in parsed])
            passage_words = _filter_passage_words_by_text(cand, passage_text)
            _log("[EXTRACT_P_WORDS_HEAD_PRE_COUNT]", passage_words[:10], "len=", len(passage_words))

        # ---- 防御：passage 抽取绕开 scattered，不让 scattered 占用 passage 的 count 名额 ----
        def _norm_key_inline(s: str) -> str:
            return " ".join((s or "").strip().lower().split())

        scattered_set = set(
            _norm_key_inline(x)
            for x in (scattered_words or [])
            if (x or "").strip()
        )

        # passage 先保序去重，再排除与 scattered 重复的，再截断 count
        passage_words = _dedupe_keep_order(passage_words or [])
        passage_words = [w for w in passage_words if _norm_key_inline(w) not in scattered_set]
        passage_words = passage_words[:count]

        _log("[EXTRACT_P_WORDS_HEAD_POST_DEFENSE]", passage_words[:10], "len=", len(passage_words))
        _log("[EXTRACT_S_WORDS_HEAD_FINAL]", (scattered_words or [])[:10], "len=", len(scattered_words or []))

        return passage_words, scattered_words

    def merge_two_sources(self, passage_words: List[str], scattered_words: List[str]) -> List[str]:
        """
        需求案：两个输入框得到的英文单词合并成一个 list。
        保序去重（先 passage，再 scattered）
        """
        merged = _dedupe_keep_order((passage_words or []) + (scattered_words or []))
        _log("[EXTRACT_MERGED_HEAD]", merged[:20], "len=", len(merged))
        return merged