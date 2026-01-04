# run_generate_answers.py
# ============================================================
# 输入：*_STD.txt（支持多 SEGMENT）
# 输出：按 segment 平铺文件（方案 B）
#   S01_P1_<topic>.txt
#   S02_P23_<topic>.txt
#
# 缓存：question 级缓存（cache.py）
# ============================================================

import os
import argparse
from typing import Dict, Any, List
import re

from .cache import JsonlCache, make_cache_key
from .generator import generate_answer_part1_or_3, generate_answer_part2
from .parser_std import parse_std_prefill_file  # 返回 {"segments":[...]} 结构

EXAM = "IELTS_Speaking"
BAND = "6.5-7"
STYLE = "natural_native"

# 全局缓存目录（可通过环境变量覆盖）
GLOBAL_CACHE_DIR = os.getenv(
    "SPEAKING_GLOBAL_CACHE_DIR",
    os.path.join(os.getcwd(), "cache_global")
)

# =========================
# 文本清洗 + 句数控制（展示层补丁）
# =========================

def split_sentences(text: str) -> List[str]:
    """
    英文句子粗切分：以 . ! ? 作为句界
    注意：这只是“上限控制”的工具，不追求 NLP 完美。
    """
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def clamp_sentences(text: str, min_n: int = None, max_n: int = None) -> str:
    """
    兼容两种调用方式（避免你其他地方再炸）：
    1) clamp_sentences(text, 5)          -> 视为 max_n=5
    2) clamp_sentences(text, 3, 5)       -> 视为 min_n=3, max_n=5（这里只做上限控制）
    """
    # 兼容 clamp_sentences(text, 5)
    if max_n is None and min_n is not None:
        max_n = min_n

    sents = split_sentences(text)
    if max_n is None or len(sents) <= max_n:
        return (text or "").strip()
    return " ".join(sents[:max_n]).strip()


def clean_llm_output(text: str) -> str:
    """
    展示层清洗：
    - 删除只包含 ** 的行
    - 压缩连续空行（>=3 → 1）
    - 去掉行尾多余空格
    """
    text = text or ""
    lines = text.splitlines()
    cleaned = []
    for ln in lines:
        s = ln.rstrip()
        if re.fullmatch(r"\*{2,}", s):  # 只剩 ** 或 ****
            continue
        cleaned.append(s)

    out = "\n".join(cleaned)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()

def _safe_filename(s: str) -> str:
    s = (s or "").strip()
    for ch in r'\/:*?"<>|':
        s = s.replace(ch, "_")
    s = s.replace("\n", " ").replace("\r", " ")
    s = s.strip()
    return (s[:60] if len(s) > 60 else s) or "UnknownTopic"

def ensure_dirs(base_runs_dir: str, run_id: str) -> Dict[str, str]:
    run_dir = os.path.join(base_runs_dir, run_id)
    answers_dir = os.path.join(run_dir, "answers")
    run_override_cache_dir = os.path.join(run_dir, "cache_override")

    os.makedirs(answers_dir, exist_ok=True)
    os.makedirs(run_override_cache_dir, exist_ok=True)
    os.makedirs(GLOBAL_CACHE_DIR, exist_ok=True)

    return {
        "run_dir": run_dir,
        "answers_dir": answers_dir,
        "run_override_cache_dir": run_override_cache_dir,
        "global_cache_dir": GLOBAL_CACHE_DIR,
    }

# 加载统一的 Cache 对象（方案 4：智能路由）
class CacheRouter:
    """
    方案4：全局缓存 + 本次运行覆盖
    - 读：优先 run_override，再全局（force_regen=True 时直接当未命中）
    - 写：only_for_this_run=True -> 写 run_override；否则写全局
    """
    def __init__(self, global_cache: JsonlCache, run_override_cache: JsonlCache,
                 force_regen: bool = False, only_for_this_run: bool = False):
        self.global_cache = global_cache
        self.run_override_cache = run_override_cache
        self.force_regen = force_regen
        self.only_for_this_run = only_for_this_run

    def get(self, key: str):
        if self.force_regen:
            return None
        v = self.run_override_cache.get(key)
        if v is not None:
            return v
        return self.global_cache.get(key)

    def set(self, key: str, payload: dict):
        if self.only_for_this_run:
            self.run_override_cache.set(key, payload)
        else:
            self.global_cache.set(key, payload)

def render_part1(topic: str, questions: List[str], cache: JsonlCache, segment_id: int) -> str:
    lines = [f"SEGMENT: {segment_id}", f"TOPIC: {topic}", "PART: 1", ""]
    for idx, q in enumerate(questions, start=1):
        key = make_cache_key(EXAM, BAND, STYLE, 1, topic, q)
        hit = cache.get(key)
        if hit:
            ans = hit
        else:
            ans = generate_answer_part1_or_3(topic, 1, q, BAND)
            cache.set(key, ans)

        lines.append(f"Q{idx}: {q}")
        lines.append("Outline:")
        outline = (ans.get("outline", "").strip() or "- (no outline)")
        outline = clean_llm_output(outline)
        lines.append(outline)

        lines.append("Full Answer:")
        full = (ans.get("full_answer", "") or "").strip()
        full = clamp_sentences(full, 5)          # Part 1：上限 5 句
        full = clean_llm_output(full)
        lines.append(full)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_part23(topic: str, part2: Dict[str, Any], part3_questions: List[str], cache: JsonlCache, segment_id: int) -> str:
    lines = [f"SEGMENT: {segment_id}", f"TOPIC: {topic}", ""]

    # Part 2（整体缓存：cue+bullets）
    cue = (part2.get("cue") or "").strip()
    bullets = part2.get("bullets") or []
    q_text = cue + "\n" + "\n".join(bullets)
    key = make_cache_key(EXAM, BAND, STYLE, 2, topic, q_text)

    hit = cache.get(key)
    if hit:
        ans2 = hit
    else:
        ans2 = generate_answer_part2(topic, cue, bullets, BAND)
        cache.set(key, ans2)

    lines.append("PART: 2")
    lines.append(f"CUE: {cue}")
    for b in bullets:
        lines.append(f"BULLET: {b}")
    lines.append("")
    lines.append("Outline:")
    outline2 = (ans2.get("outline", "").strip() or "- (no outline)")
    outline2 = clean_llm_output(outline2)
    lines.append(outline2)

    lines.append("Full Answer:")
    full2 = (ans2.get("full_answer", "") or "").strip()
    full2 = clamp_sentences(full2, 22)          # Part 2：上限 22 句
    full2 = clean_llm_output(full2)
    lines.append(full2)
    lines.append("")

    lines.append("PART: 3")
    lines.append("")

    # Part 3（逐题缓存）
    for idx, q in enumerate(part3_questions, start=1):
        key3 = make_cache_key(EXAM, BAND, STYLE, 3, topic, q)
        hit3 = cache.get(key3)
        if hit3:
            ans3 = hit3
        else:
            ans3 = generate_answer_part1_or_3(topic, 3, q, BAND)
            cache.set(key3, ans3)

        lines.append(f"Q{idx}: {q}")
        lines.append("Outline:")
        outline3 = (ans3.get("outline", "").strip() or "- (no outline)")
        outline3 = clean_llm_output(outline3)
        lines.append(outline3)

        lines.append("Full Answer:")
        full3 = (ans3.get("full_answer", "") or "").strip()
        full3 = clamp_sentences(full3, 5)       # Part 3：每题上限 5 句
        full3 = clean_llm_output(full3)
        lines.append(full3)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--std", required=True, help="*_STD.txt 的完整路径")
    ap.add_argument("--run_id", required=True, help="runs/<run_id>/ 目录名（例如 47da2fec）")
    ap.add_argument(
        "--runs_dir",
        default=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "runs"),
        help="speaking_service/runs 目录（默认自动推断）",
    )
    args = ap.parse_args()

    paths = ensure_dirs(args.runs_dir, args.run_id)
    cache = JsonlCache(paths["cache_dir"])

    parsed = parse_std_prefill_file(args.std)  # {"segments":[...]}
    segments = parsed.get("segments", []) or []

    if not segments:
        raise RuntimeError("No segments parsed from STD file. Check parser_std.py output.")

    out_files = []

    for seg_id, seg in enumerate(segments, start=1):
        # ⚠️ 注意：parser_std 输出的 segment 没有 segment_id
        # 所以这里必须用 enumerate 自行编号
        topic = (seg.get("topic") or "").strip() or f"UnknownTopic_S{seg_id:02d}"
        part1_qs = seg.get("part1") or []
        part2 = seg.get("part2")
        part3_qs = seg.get("part3") or []

        # Part 1 文件：只写 render_part1 的结果（不要在 main 里逐题生成）
        if part1_qs:
            fname = f"S{seg_id:02d}_P1_{_safe_filename(topic)}.txt"
            fpath = os.path.join(paths["answers_dir"], fname)
            content = render_part1(topic, part1_qs, cache, seg_id)
            content = clean_llm_output(content)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content + ("\n" if not content.endswith("\n") else ""))
            out_files.append(fpath)

        # Part 2&3 合并文件
        if (part2 is not None) or part3_qs:
            part2_obj = part2 if isinstance(part2, dict) else {"cue": "", "bullets": []}
            fname = f"S{seg_id:02d}_P23_{_safe_filename(topic)}.txt"
            fpath = os.path.join(paths["answers_dir"], fname)
            content = render_part23(topic, part2_obj, part3_qs, cache, seg_id)
            content = clean_llm_output(content)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content + ("\n" if not content.endswith("\n") else ""))
            out_files.append(fpath)

    print("[OK] Generated answer files:")
    for p in out_files:
        print(" -", p)
    print("[OK] Cache file:")
    print(" -", os.path.join(paths["cache_dir"], "answers_cache.jsonl"))


if __name__ == "__main__":
    main()

def generate_answers_for_run(std_path: str, runs_dir: str, run_id: str,
                             force_regen: bool = False, only_for_this_run: bool = False) -> Dict[str, Any]:
    paths = ensure_dirs(runs_dir, run_id)

    # ✅ 全局 cache 目录：runs/_global_cache（你也可以换成环境变量）
    global_cache_dir = os.path.join(runs_dir, "_global_cache")
    os.makedirs(global_cache_dir, exist_ok=True)

    global_cache = JsonlCache(global_cache_dir)
    print("DEBUG paths keys:", paths.keys())
    # ✅ run 级缓存目录（兜底：runs/<run_id>/cache），避免 KeyError
    run_override_dir = os.path.join(runs_dir, run_id, "cache_override")
    os.makedirs(run_override_dir, exist_ok=True)
    run_override_cache = JsonlCache(run_override_dir)

    # 先不接入开关的话就全 False（不会影响跑通）
    cache = CacheRouter(
    global_cache,
    run_override_cache,
    force_regen=bool(force_regen),
    only_for_this_run=bool(only_for_this_run),
)

    parsed = parse_std_prefill_file(std_path)
    segments = parsed.get("segments", []) or []

    if not segments:
        raise RuntimeError("No segments parsed from STD file.")

    out_files = []

    for seg_id, seg in enumerate(segments, start=1):
        topic = (seg.get("topic") or "").strip() or f"UnknownTopic_S{seg_id:02d}"
        part1_qs = seg.get("part1") or []
        part2 = seg.get("part2")
        part3_qs = seg.get("part3") or []

        if part1_qs:
            fname = f"S{seg_id:02d}_P1_{_safe_filename(topic)}.txt"
            fpath = os.path.join(paths["answers_dir"], fname)
            content = render_part1(topic, part1_qs, cache, seg_id)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content + "\n")
            out_files.append(fpath)

        if (part2 is not None) or part3_qs:
            part2_obj = part2 if isinstance(part2, dict) else {"cue": "", "bullets": []}
            fname = f"S{seg_id:02d}_P23_{_safe_filename(topic)}.txt"
            fpath = os.path.join(paths["answers_dir"], fname)
            content = render_part23(topic, part2_obj, part3_qs, cache, seg_id)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content + "\n")
            out_files.append(fpath)

    return {
        "run_id": run_id,
        "answers_dir": paths["answers_dir"],
        "files": out_files,
    }

