# parser_prefill_txt.py
# ============================================================
# 作用：
#   将“原始预填 txt（来源 OCR / 人工整理，格式不稳定）”
#   规范化为“STD txt（机器稳定可解析）”
#
# 新增能力：
#   - 支持多题卡（SEGMENT）
#   - 允许一个 run 内出现多个 Part 1 卡、多个 Part 2&3 卡
#
# STD 输出格式示例：
#   SEGMENT: 1
#   TOPIC: Friends
#   PART: 1
#   Q: ...
#   ...
#   SEGMENT: 2
#   TOPIC: 家中重要老物件
#   PART: 2
#   CUE: Describe ...
#   BULLET: ...
#   PART: 3
#   Q: ...
# ============================================================

import os
import re
import argparse
from typing import List, Tuple


# --------------------------
# 基础清洗
# --------------------------
def normalize_cn_spacing(s: str) -> str:
    """去掉中文字符之间的空格：'家 中 重 要 老 物 件' -> '家中重要老物件'"""
    s = (s or "").strip()
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)


def strip_wrapping_brackets(s: str) -> str:
    """把 [Friends] / 【Friends】 / (Friends) -> Friends"""
    s = (s or "").strip()
    s = re.sub(r"^[\[\(（【]\s*", "", s)
    s = re.sub(r"\s*[\]\)）】]$", "", s)
    return s.strip()


def clean_line(line: str) -> str:
    line = (line or "").strip().replace("\ufeff", "")
    line = strip_wrapping_brackets(line)
    line = normalize_cn_spacing(line)
    return line.strip()


def is_part_line(line: str) -> Tuple[bool, int]:
    m = re.match(r"^\s*Part\s*([123])\s*$", (line or "").strip(), re.I)
    if not m:
        return (False, 0)
    return (True, int(m.group(1)))


def strip_question_number(line: str) -> str:
    s = (line or "").strip()
    s = re.sub(r"^\s*\d{1,2}\s*[\.\)\-、:]\s*", "", s)
    s = re.sub(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩]\s*", "", s)
    return s.strip()


def looks_like_question(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if s.endswith("?"):
        return True
    if re.match(r"^(Do|Does|Did|Is|Are|Was|Were|Have|Has|Had|Can|Could|Would|Will|Should|What|Why|Where|When|How)\b", s, re.I):
        return True
    return False


def is_noise_line(line: str) -> bool:
    """过滤常见 UI 噪声/按钮/碎片（尽量不误杀标题）"""
    if not line:
        return True
    raw = (line or "").strip()
    if not raw:
        return True

    compact = re.sub(r"\s+", "", raw)
    compact = re.sub(r"[<>\[\]{}（）()【】]", "", compact)

    # 常见 UI / 标签噪声
    if re.search(r"(题卡|同步更新|参考答案|立即练习|上一题|下一题|我要补充|展开|收起)", compact, re.I):
        return True

    # P1 / P2&P3 题卡标记
    if re.match(r"^P\d", compact, re.I):
        return True
    if re.match(r"^P\d&P\d", compact, re.I):
        return True

    # 单个字母残片（Q/G）
    if re.match(r"^[A-Za-z]$", compact):
        return True

    # 时间/电量
    if re.match(r"^\d{1,2}:\d{2}$", compact):
        return True
    if re.match(r"^\d{1,3}%$", compact):
        return True

    # 太短一般是噪声（但保留 2+ 的可能标题）
    if len(compact) <= 1:
        return True

    return False


def is_banned_as_topic(line: str) -> bool:
    """这些行不可能是 topic"""
    s = (line or "").strip()
    if not s:
        return True
    if is_part_line(s)[0]:
        return True
    if re.match(r"^You\s+should\s+say\s*:?\s*$", s, re.I):
        return True
    if re.match(r"^Describe\b", s, re.I):
        return True
    # 看起来像 question，就不要当 topic
    if looks_like_question(strip_question_number(s)):
        return True
    return False


def find_topic_around(lines: List[str], idx_part_line: int, up_window: int = 6, down_window: int = 3) -> str:
    """
    在 Part 行附近寻找 topic：
    - 优先向上找最近的“非噪声且不像 question 的标题行”
    - 若向上找不到，再向下找（为了应对 UI 变动：标题跑到 Part 后面）
    """
    # 向上
    for j in range(idx_part_line - 1, max(-1, idx_part_line - up_window) - 1, -1):
        cand = lines[j]
        if not cand or is_noise_line(cand):
            continue
        cand = clean_line(cand)
        if cand and (not is_banned_as_topic(cand)):
            return cand

    # 向下
    for j in range(idx_part_line + 1, min(len(lines), idx_part_line + 1 + down_window)):
        cand = lines[j]
        if not cand or is_noise_line(cand):
            continue
        cand = clean_line(cand)
        if cand and (not is_banned_as_topic(cand)):
            return cand

    return ""


# --------------------------
# 核心：生成 STD（多 SEGMENT）
# --------------------------
def standardize_prefill_text(raw_text: str) -> str:
    raw_lines = raw_text.splitlines()
    lines = [clean_line(ln) for ln in raw_lines]

    out: List[str] = []

    segment_id = 0
    current_topic = ""
    current_part = 0
    in_part2_bullets = False

    # 记录当前 segment 已经写入了哪些 part（用于判断是否需要开启新 segment）
    seg_has_part1 = False
    seg_has_part2 = False
    seg_has_part3 = False

    def start_new_segment(new_topic: str):
        nonlocal segment_id, current_topic, current_part, in_part2_bullets
        nonlocal seg_has_part1, seg_has_part2, seg_has_part3

        segment_id += 1
        current_topic = (new_topic or "").strip() or f"UNKNOWN_TOPIC_S{segment_id:02d}"
        current_part = 0
        in_part2_bullets = False

        seg_has_part1 = False
        seg_has_part2 = False
        seg_has_part3 = False

        # segment header
        if out and out[-1].strip():
            out.append("")  # spacer
        out.append(f"SEGMENT: {segment_id}")
        out.append(f"TOPIC: {current_topic}")

    def ensure_segment_exists(topic_hint: str = ""):
        nonlocal segment_id
        if segment_id == 0:
            start_new_segment(topic_hint)

    def set_part(p: int):
        nonlocal current_part, in_part2_bullets
        nonlocal seg_has_part1, seg_has_part2, seg_has_part3

        current_part = p
        in_part2_bullets = False

        out.append(f"PART: {current_part}")
        if p == 1:
            seg_has_part1 = True
        elif p == 2:
            seg_has_part2 = True
        elif p == 3:
            seg_has_part3 = True

    for i, ln in enumerate(lines):
        if not ln or is_noise_line(ln):
            continue

        is_part, pnum = is_part_line(ln)
        if is_part:
            # 识别 topic（在 Part 附近找标题行）
            topic_found = find_topic_around(lines, i)

            # 何时开启新 segment？
            # - 遇到 Part 1，如果当前 segment 已经有内容（P1 或 P2/P3），则这是新题卡
            # - 遇到 Part 2，如果当前 segment 已经出现过 Part 2（说明新的一张 P2&P3 卡）
            # - 遇到 Part 2，且当前 segment 已经有 Part 1（说明从 P1 卡切到 P2&P3 卡，也应开新 segment）
            need_new_segment = False
            if segment_id == 0:
                need_new_segment = True
            else:
                if pnum == 1:
                    if seg_has_part1 or seg_has_part2 or seg_has_part3:
                        need_new_segment = True
                elif pnum == 2:
                    if seg_has_part2 or seg_has_part3 or seg_has_part1:
                        need_new_segment = True
                elif pnum == 3:
                    # Part 3 通常跟随 Part 2；若此前没有 Part 2 但已有 Part 3/P1，也当作新 segment
                    if (not seg_has_part2) and (seg_has_part1 or seg_has_part3):
                        need_new_segment = True

            if need_new_segment:
                start_new_segment(topic_found)
            else:
                ensure_segment_exists(topic_found)
                # 同一个 segment 内，如果找到了更合理的标题，允许更新 TOPIC（但不强制）
                if topic_found and topic_found.lower() != current_topic.lower():
                    current_topic = topic_found
                    out.append(f"TOPIC: {current_topic}")

            set_part(pnum)
            continue

        # 还没遇到 Part，但有可能先出现标题：用来预热 segment topic
        if current_part == 0:
            ensure_segment_exists(ln)
            # 如果这一行不像 question，允许把它当成更好的 topic（但不输出多次）
            if (not is_banned_as_topic(ln)) and ln.lower() != current_topic.lower():
                current_topic = ln
                # 更新 TOPIC 行（写一条新的 TOPIC，parser_std 会按最新的算）
                out.append(f"TOPIC: {current_topic}")
            continue

        # PART 2 规则（严格）
        if current_part == 2:
            # cue
            if re.match(r"^Describe\b", ln, re.I):
                out.append(f"CUE: {ln}")
                continue

            if re.match(r"^You\s+should\s+say\s*:?\s*$", ln, re.I):
                in_part2_bullets = True
                continue

            if in_part2_bullets:
                b = re.sub(r"^\s*[-•\*]\s*", "", ln).strip()
                b = strip_question_number(b)
                if b:
                    # 去重
                    if out and out[-1].strip() == f"BULLET: {b}":
                        continue
                    out.append(f"BULLET: {b}")
                continue

            # 未进入 bullets，但出现解释行：统一当 BULLET
            b = strip_question_number(ln).strip()
            if b:
                if out and out[-1].strip() == f"BULLET: {b}":
                    continue
                out.append(f"BULLET: {b}")
            continue

        # PART 1 / 3：只收 question
        if current_part in (1, 3):
            q = strip_question_number(ln)
            if looks_like_question(q):
                out.append(f"Q: {q}")
            else:
                # 非 question 丢弃，避免污染
                pass
            continue

        # 兜底
        out.append(ln)

    return "\n".join(out).strip() + "\n"


# --------------------------
# IO：单文件或目录批处理
# --------------------------
def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def collect_txt_files(input_path: str) -> List[str]:
    if os.path.isfile(input_path):
        return [input_path]
    files = []
    for name in os.listdir(input_path):
        if name.lower().endswith(".txt"):
            files.append(os.path.join(input_path, name))
    files.sort(key=lambda p: os.path.getmtime(p))
    return files


def main():
    ap = argparse.ArgumentParser(description="Standardize prefill txt into STD format with SEGMENT support.")
    ap.add_argument("--input", required=True, help="预填txt文件 或 包含txt的目录")
    ap.add_argument("--output", default="", help="输出文件路径（不填则同目录生成 *_STD.txt）")
    args = ap.parse_args()

    files = collect_txt_files(args.input)
    if not files:
        raise RuntimeError(f"No .txt files found: {args.input}")

    for fp in files:
        raw = read_text(fp)
        std = standardize_prefill_text(raw)

        if args.output:
            out_path = args.output
        else:
            base, ext = os.path.splitext(fp)
            out_path = base + "_STD" + ext

        write_text(out_path, std)
        print(f"[OK] {fp} -> {out_path}")


if __name__ == "__main__":
    main()
