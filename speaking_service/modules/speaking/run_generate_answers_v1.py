# run_generate_answers.py
# ============================================================
# 用法（示例）：
#   python run_generate_answers.py --std "...\雅思_口语话题_预填_3989_STD.txt" --run_id 47da2fec
#
# 输出：
#   speaking_service/runs/<run_id>/answers/
#     Part1_<Topic>.txt
#     Part2_<Topic>.txt
#     Part3_<Topic>.txt
#
# 缓存：
#   speaking_service/runs/<run_id>/cache/answers_cache.jsonl
# ============================================================

import os
import argparse
from typing import Dict, Any

from cache import JsonlCache, make_cache_key
from generator import generate_answer_part1_or_3, generate_answer_part2
from parser_std import parse_std_prefill_file, to_dict


EXAM = "IELTS_Speaking"
BAND = "6.5-7"
STYLE = "natural_native"


def _safe_filename(s: str) -> str:
    # Windows 文件名安全处理
    s = (s or "").strip()
    for ch in r'\/:*?"<>|':
        s = s.replace(ch, "_")
    return s[:80] if len(s) > 80 else s


def ensure_dirs(base_runs_dir: str, run_id: str) -> Dict[str, str]:
    run_dir = os.path.join(base_runs_dir, run_id)
    answers_dir = os.path.join(run_dir, "answers")
    cache_dir = os.path.join(run_dir, "cache")
    os.makedirs(answers_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    return {"run_dir": run_dir, "answers_dir": answers_dir, "cache_dir": cache_dir}


def render_part1(topic: str, questions: list, cache: JsonlCache) -> str:
    lines = [f"TOPIC: {topic}", "PART: 1", ""]
    for q in questions:
        key = make_cache_key(EXAM, BAND, STYLE, 1, topic, q)
        hit = cache.get(key)
        if hit:
            ans = hit
        else:
            ans = generate_answer_part1_or_3(topic, 1, q, BAND)
            cache.set(key, ans)

        lines.append(f"Q: {q}")
        lines.append("Outline:")
        lines.append(ans.get("outline", "").strip() or "- (no outline)")
        lines.append("Full Answer:")
        lines.append(ans.get("full_answer", "").strip())
        lines.append("")  # spacer
    return "\n".join(lines).strip() + "\n"


def render_part3(topic: str, questions: list, cache: JsonlCache) -> str:
    lines = [f"TOPIC: {topic}", "PART: 3", ""]
    for q in questions:
        key = make_cache_key(EXAM, BAND, STYLE, 3, topic, q)
        hit = cache.get(key)
        if hit:
            ans = hit
        else:
            ans = generate_answer_part1_or_3(topic, 3, q, BAND)
            cache.set(key, ans)

        lines.append(f"Q: {q}")
        lines.append("Outline:")
        lines.append(ans.get("outline", "").strip() or "- (no outline)")
        lines.append("Full Answer:")
        lines.append(ans.get("full_answer", "").strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_part2(topic: str, cue: str, bullets: list, cache: JsonlCache) -> str:
    # Part2 作为一个整体缓存（cue+bullets 组合）
    q_text = cue + "\n" + "\n".join(bullets)
    key = make_cache_key(EXAM, BAND, STYLE, 2, topic, q_text)

    hit = cache.get(key)
    if hit:
        ans = hit
    else:
        ans = generate_answer_part2(topic, cue, bullets, BAND)
        cache.set(key, ans)

    lines = [f"TOPIC: {topic}", "PART: 2", ""]
    lines.append(f"CUE: {cue}")
    for b in bullets:
        lines.append(f"BULLET: {b}")
    lines.append("")
    lines.append("Outline:")
    lines.append(ans.get("outline", "").strip() or "- (no outline)")
    lines.append("Full Answer:")
    lines.append(ans.get("full_answer", "").strip())
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

    parsed = parse_std_prefill_file(args.std)
    data = to_dict(parsed)

    topic = data.get("topic", "").strip() or "UnknownTopic"
    part1_qs = data.get("part1", []) or []
    part3_qs = data.get("part3", []) or []
    part2 = data.get("part2", None)

    # 写 Part1
    p1_text = render_part1(topic, part1_qs, cache)
    p1_name = f"Part1_{_safe_filename(topic)}.txt"
    p1_path = os.path.join(paths["answers_dir"], p1_name)
    with open(p1_path, "w", encoding="utf-8") as f:
        f.write(p1_text)

    # 写 Part2
    p2_path = ""
    if part2 and isinstance(part2, dict):
        cue = (part2.get("cue") or "").strip()
        bullets = part2.get("bullets") or []
        p2_text = render_part2(topic, cue, bullets, cache)
        p2_name = f"Part2_{_safe_filename(topic)}.txt"
        p2_path = os.path.join(paths["answers_dir"], p2_name)
        with open(p2_path, "w", encoding="utf-8") as f:
            f.write(p2_text)

    # 写 Part3
    p3_text = render_part3(topic, part3_qs, cache)
    p3_name = f"Part3_{_safe_filename(topic)}.txt"
    p3_path = os.path.join(paths["answers_dir"], p3_name)
    with open(p3_path, "w", encoding="utf-8") as f:
        f.write(p3_text)

    print("[OK] Generated answers:")
    print(" -", p1_path)
    if p2_path:
        print(" -", p2_path)
    print(" -", p3_path)
    print("[OK] Cache file:")
    print(" -", os.path.join(paths['cache_dir'], 'answers_cache.jsonl'))


if __name__ == "__main__":
    main()
