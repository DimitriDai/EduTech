# speaking_service/modules/speaking/parser_std.py
# ------------------------------------------------------------
# 功能：把 prefill.txt 转为 STD.txt（SEGMENT/TOPIC/PART/Q/CUE/BULLET）
#
# 修复点（你遇到的核心 bug）：
# - 在 Part 2 的 "You should say:" 模式下，直到 Part 3 之前的所有非空行
#   都必须当作 BULLET，绝对不能被当成 TOPIC。
#   否则就会出现：TOPIC: And explain ...
#
# 新增：prefill 阶段“语义清洗”（你要求的规则顺序）
# 1) 先处理 Part2/3：如果 segment 的 TOPIC 是英文且该 segment 的第一个 PART 为 2
#    -> 用“向上最近的中文 topic”替换该 TOPIC（避免 deepseek 擅自加 topic 导致缓存 miss）
# 2) 再处理 Part1：如果 segment 的 TOPIC 是中文且该 segment 的第一个 PART 为 1
#    -> 删除整个 segment（去掉冗余错误 segment，避免影响 export_docx/缓存）
#
# 命令行用法（示例）：
#   python parser_std.py --input "...\prefill\雅思_口语话题_预填_9162.txt"
# 会输出：
#   ..._STD.txt
# ------------------------------------------------------------

import os
import re
import argparse
from typing import List, Optional, Dict, Any


CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def is_chinese_text(s: str) -> bool:
    return bool(CJK_RE.search(s or ""))


def fix_spaced_cjk(s: str) -> str:
    return re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", s)


def normalize_lines(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = fix_spaced_cjk(text)
    lines = [ln.strip() for ln in text.split("\n")]
    # 保留空行用于分段判断，但后面会更严格控制
    return lines


def is_part_line(ln: str) -> Optional[int]:
    m = re.match(r"^Part\s*([123])\b", ln, re.I)
    if not m:
        return None
    return int(m.group(1))


def is_numbered_question(ln: str) -> Optional[str]:
    # "1. xxx"
    m = re.match(r"^\s*(\d+)\.\s*(.+?)\s*$", ln)
    if m:
        return m.group(2).strip()
    return None


def looks_like_title(ln: str) -> bool:
    if not ln:
        return False
    if "题卡" in ln:
        return False
    if re.search(r"\b(202\d|20\d{2})\b", ln):
        return False
    # ✅ 禁止 bullet 行被识别为标题（关键！）
    if ln.lstrip().startswith("-"):
        return False
    if is_part_line(ln) is not None:
        return False
    # 排除明显的 cue 句（但允许作为兜底 title 的情况，在外面做）
    if ln.lower().startswith("you should say"):
        return False
    # 太长一般不是标题
    if len(ln) > 90:
        return False
    return True


def convert_prefill_to_std(prefill_path: str) -> str:
    raw = open(prefill_path, "r", encoding="utf-8").read()
    lines = normalize_lines(raw)

    out: List[str] = []
    seg_id = 0

    cur_topic: Optional[str] = None
    cur_part: Optional[int] = None

    # Part2 模式标记
    in_part2_you_should_say = False
    part2_cue_written = False

    # 记录 out 里 TOPIC: 行的索引，方便在 Part2 进入时覆盖修正
    topic_out_idx: Optional[int] = None

    def start_new_segment(topic: str):
        nonlocal seg_id, cur_topic, cur_part, in_part2_you_should_say, part2_cue_written, topic_out_idx
        seg_id += 1
        cur_topic = (topic or "").strip()
        cur_part = None
        in_part2_you_should_say = False
        part2_cue_written = False
        out.append(f"SEGMENT: {seg_id}")
        topic_out_idx = len(out)
        out.append(f"TOPIC: {cur_topic}")

    # 简单 lookahead：判断某行是不是 “标题行”，条件是：它后面不远处出现 Part 1/2
    def find_next_nonempty(idx: int) -> Optional[str]:
        for k in range(idx + 1, len(lines)):
            if lines[k].strip():
                return lines[k].strip()
        return None

    # 向上找“最近的中文/英文标题行”，用于强制 Part2/Part1 的标题规则
    def find_prev_title(idx: int, want_chinese: bool) -> Optional[str]:
        for k in range(idx - 1, -1, -1):
            s = (lines[k] or "").strip()
            if not s:
                continue

            # 排除 Part 行、Describe cue、You should say、以及 bullet 行
            if is_part_line(s) is not None:
                continue
            if s.lower().startswith("describe "):
                continue
            if s.lower().startswith("you should say"):
                continue
            if s.lstrip().startswith("-"):
                continue

            # 只选“看起来像标题”的行
            if not looks_like_title(s):
                continue

            if want_chinese and is_chinese_text(s):
                return s
            if (not want_chinese) and (not is_chinese_text(s)):
                return s
        return None

    i = 0
    while i < len(lines):
        ln = lines[i].strip()

        if not ln:
            i += 1
            continue

        # 1) 识别 Part 行
        p = is_part_line(ln)
        if p is not None:
            cur_part = p
            out.append(f"PART: {p}")

            # Part 2：强制 TOPIC = 最近中文标题（且必须覆盖已写入 out 的 TOPIC 行）
            if p == 2:
                in_part2_you_should_say = False
                part2_cue_written = False

                bad_topic = (
                    (cur_topic is None)
                    or (not cur_topic.strip())
                    or cur_topic.lstrip().startswith("-")
                    or cur_topic.lower().startswith("describe ")
                    or (not is_chinese_text(cur_topic))
                )
                if bad_topic:
                    prev_cn = find_prev_title(i, want_chinese=True)
                    if prev_cn:
                        cur_topic = prev_cn.strip()
                        if topic_out_idx is not None:
                            out[topic_out_idx] = f"TOPIC: {cur_topic}"

            # Part 1：强制 TOPIC = 最近英文标题（如果你要这个硬规则）
            if p == 1:
                bad_topic = (cur_topic is None) or (not cur_topic.strip()) or is_chinese_text(cur_topic)
                if bad_topic:
                    prev_en = find_prev_title(i, want_chinese=False)
                    if prev_en:
                        cur_topic = prev_en.strip()
                        if topic_out_idx is not None:
                            out[topic_out_idx] = f"TOPIC: {cur_topic}"

            i += 1
            continue

        # 2) 如果还没 segment，或检测到新标题 -> start segment
        #    规则：当前行像标题，并且下一非空行是 Part 1/2
        if looks_like_title(ln):
            nxt = find_next_nonempty(i)
            part = is_part_line(nxt) if nxt else None
            if part in (1, 2):
                topic_line = ln

                # Part 2：标题必须中文 -> 向上回溯最近中文
                if part == 2 and (not is_chinese_text(topic_line)):
                    prev_cn = find_prev_title(i, want_chinese=True)
                    if prev_cn:
                        topic_line = prev_cn

                # Part 1：标题必须英文 -> 向上回溯最近英文
                if part == 1 and is_chinese_text(topic_line):
                    prev_en = find_prev_title(i, want_chinese=False)
                    if prev_en:
                        topic_line = prev_en

                start_new_segment(topic_line)
                i += 1
                continue

        # 兜底：如果到这里还没有 segment，但第一句就是 Describe...（标题缺失）
        if cur_topic is None:
            if ln.lower().startswith("describe "):
                # 直接拿 cue 当标题兜底
                start_new_segment(ln)
                i += 1
                continue
            # 再兜底：给一个 Unknown segment，避免崩
            start_new_segment("Unknown Topic")

        # 3) Part2 cue / bullets / Part1&3 questions
        if cur_part == 2:
            # cue：通常是 Describe...，只写一次
            if (not part2_cue_written) and ln.lower().startswith("describe "):
                out.append(f"CUE: {ln}")
                part2_cue_written = True
                i += 1
                continue

            # 进入 bullet 模式
            if ln.lower().startswith("you should say"):
                in_part2_you_should_say = True
                i += 1
                continue

            # 只要还在 Part2，并且已经 seen "You should say:"
            # 那么直到遇到 Part 3 之前的所有“非空非 cue”行，全部当 BULLET
            if in_part2_you_should_say:
                b = re.sub(r"^\-\s*", "", ln).strip()
                if b:
                    out.append(f"BULLET: {b}")
                i += 1
                continue

            # Part2 里偶尔还会出现未加 "-" 的 bullet（且 You should say 可能 OCR 丢了）
            # 兜底：如果 cue 已写入且当前行不是 Part 行，也不是空，就当 bullet
            if part2_cue_written and (is_part_line(ln) is None):
                b = re.sub(r"^\-\s*", "", ln).strip()
                if b:
                    out.append(f"BULLET: {b}")
                i += 1
                continue

            i += 1
            continue

        # Part1 / Part3：编号题目
        if cur_part in (1, 3):
            q = is_numbered_question(ln)
            if q:
                out.append(f"Q: {q}")
                i += 1
                continue

            # 非编号行：一般不该出现；但如果出现，尽量当作 Q（防 OCR 丢编号）
            if ln and not ln.lower().startswith(("you should say",)):
                out.append(f"Q: {ln}")
                i += 1
                continue

        i += 1

    # 输出 STD 文件路径
    base, ext = os.path.splitext(prefill_path)
    std_path = base + "_STD.txt"
    with open(std_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).strip() + "\n")

    return std_path

# ============================
# Compatibility: run_generate_answers.py expects this function
# ============================

def parse_std_prefill_file(path: str) -> dict:
    """
    兼容旧脚本：
    - 如果传入的是 *_STD.txt：直接解析
    - 如果传入的是 prefill.txt：先 convert_prefill_to_std() 再解析
    返回结构：{"segments":[{"topic":..., "part1":[...], "part2":{"cue":..., "bullets":[...]}, "part3":[...]}]}

    ✅ 新增：在返回 segments 前做“prefill 阶段清洗”（先修 P2 英文 topic，再删 P1 中文 topic segment）
    """
    if path.lower().endswith("_std.txt"):
        std_path = path
    else:
        # 允许用户把 prefill 传进来：先转成 STD
        std_path = convert_prefill_to_std(path)

    segs = _parse_std_file(std_path)
    segs = _clean_segments_prefill_stage(segs)
    # ✅ 覆盖写回原 STD 文件（不新增 _STD_CLEAN.txt，不用改任何下游）
    with open(std_path, "w", encoding="utf-8") as f:
        f.write(_segments_to_std_text(segs))
    return {"segments": segs}


def _parse_std_file(std_path: str) -> list:
    """
    解析 *_STD.txt 为 segments 列表。
    ✅ 改法A核心：
    - segment 初始化时 part2 = None
    - 只有遇到 CUE: / BULLET: 才创建 part2 字典
    这样 Part1-only 的题卡不会“凭空拥有 Part2”，也就不会触发生成伪 Part23。
    """
    segments: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    cur_part: Optional[int] = None

    def new_seg(topic: str) -> Dict[str, Any]:
        return {
            "topic": topic.strip(),
            "part1": [],
            "part2": None,      # ✅ 关键：默认没有 Part2
            "part3": [],
            "_first_part": None,  # ✅ 新增：记录该 segment 第一个出现的 PART（用于清洗顺序）
        }

    def ensure_part2():
        """只有真正读到 CUE/BULLET 时才创建 Part2 容器。"""
        nonlocal cur
        if cur is None:
            cur = new_seg("Unknown Topic")
        if cur.get("part2") is None:
            cur["part2"] = {"cue": "", "bullets": []}

    with open(std_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            if line.startswith("SEGMENT:"):
                if cur:
                    segments.append(cur)
                cur = new_seg("Unknown Topic")
                cur_part = None
                continue

            if line.startswith("TOPIC:"):
                if cur is None:
                    cur = new_seg("Unknown Topic")
                cur["topic"] = line[len("TOPIC:"):].strip()
                continue

            if line.startswith("PART:"):
                try:
                    cur_part = int(line.split(":", 1)[1].strip())
                except Exception:
                    cur_part = None

                # ✅ 记录该 segment 的“第一个 PART”
                if cur is None:
                    cur = new_seg("Unknown Topic")
                if cur_part in (1, 2, 3) and cur.get("_first_part") is None:
                    cur["_first_part"] = cur_part

                continue

            if cur is None:
                cur = new_seg("Unknown Topic")

            if line.startswith("Q:"):
                q = line[len("Q:"):].strip()
                if cur_part == 1:
                    cur["part1"].append(q)
                elif cur_part == 3:
                    cur["part3"].append(q)
                else:
                    # 兜底：没写 PART 的情况下，默认塞 part1
                    cur["part1"].append(q)
                continue

            if line.startswith("CUE:"):
                cue = line[len("CUE:"):].strip()
                ensure_part2()                     # ✅ 只有出现 CUE 才创建 Part2
                cur["part2"]["cue"] = cue
                continue

            if line.startswith("BULLET:"):
                b = line[len("BULLET:"):].strip()
                ensure_part2()                     # ✅ 只有出现 BULLET 才创建 Part2
                cur["part2"]["bullets"].append(b)
                continue

    if cur:
        segments.append(cur)

    return segments

def normalize_topic_to_cn(topic: str) -> str:
    """
    把 topic 规范化为“稳定中文锚点”：
    - 若含中文且存在常见中英分隔符（/ | ｜ - — ： :），优先取分隔符左侧
    - 去掉左侧可能出现的编号前缀： "6. xxx" / "6) xxx"
    - 最终返回 strip 后的文本
    """
    t = (topic or "").strip()
    if not t:
        return t

    # 去掉编号前缀（常见：6. / 6) / 6:）
    t = re.sub(r"^\s*\d{1,2}\s*[\.\)\:：、\-]\s*", "", t).strip()

    # 如果含中文，并且带中英混合常见分隔符，取左侧当“中文锚点”
    if is_chinese_text(t):
        # 注意：这里不要用单个 "-" 太激进，先覆盖你遇到的 "/"
        # 你后续如果发现其它分隔符，再加即可
        for sep in [" / ", "/", "｜", "|", " — ", " - ", "：", ":"]:
            if sep in t:
                left = t.split(sep, 1)[0].strip()
                # 只有左侧仍含中文才采用（避免误切）
                if left and is_chinese_text(left):
                    t = left
                break

    return t.strip()


def _clean_segments_prefill_stage(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    prefill 清洗规则（顺序很重要）：
    0) 先把所有 segment 的 topic 规范化为“稳定中文锚点”（解决：中文/英文混写导致的新 key）
    1) 再修 Part2 英文 topic：first_part==2 且 topic 不含中文 => 用“向上最近的纯中文 topic”替换
    2) 最后删 Part1 中文 topic segment：first_part==1 且 topic 含中文 => 删除整个 segment
    """

    # ---------- 0) 先规范化 topic（中英混写 -> 纯中文；去编号） ----------
    for seg in segments:
        seg["topic"] = normalize_topic_to_cn(seg.get("topic", ""))

    # ---------- 1) 第一遍：修 Part2 英文 topic（用“纯中文锚点”替换） ----------
    last_cn_topic: Optional[str] = None
    fixed_p2 = 0
    fix_p2_failed_no_anchor = 0

    for seg in segments:
        topic = (seg.get("topic") or "").strip()
        first_part = seg.get("_first_part")

        # 维护“纯中文锚点”：只要 topic 含中文就作为锚点
        # （此时 topic 已经过 normalize_topic_to_cn，基本不会再是 “中文/英文混写”）
        if is_chinese_text(topic):
            last_cn_topic = topic

        # 修 Part2 英文 topic（注意：要在维护锚点之后还是之前？）
        # 这里应当：先修当前段，再决定是否更新锚点更合理；
        # 但因为当前 topic 是英文时不会更新锚点，所以顺序无所谓。
        if first_part == 2 and (not is_chinese_text(topic)):
            if last_cn_topic:
                seg["topic"] = last_cn_topic
                fixed_p2 += 1
            else:
                fix_p2_failed_no_anchor += 1
                # 不强行写【未对齐】，避免污染；保持原样让你后续诊断

    # ---------- 2) 第二遍：删 Part1 中文 topic segment ----------
    cleaned: List[Dict[str, Any]] = []
    deleted_p1 = 0

    for seg in segments:
        topic = (seg.get("topic") or "").strip()
        first_part = seg.get("_first_part")

        if first_part == 1 and is_chinese_text(topic):
            deleted_p1 += 1
            continue

        cleaned.append(seg)

    # 清掉内部字段（避免影响缓存 key / 导出）
    for seg in cleaned:
        seg.pop("_first_part", None)

    # 你现在的调试打印
    print(f"[prefill_clean] fixed_p2={fixed_p2}, deleted_p1={deleted_p1}, "
          f"total_in={len(segments)}, total_out={len(cleaned)}, "
          f"fix_p2_failed_no_anchor={fix_p2_failed_no_anchor}")

    return cleaned

def _segments_to_std_text(segments: List[Dict[str, Any]]) -> str:
    """
    把 segments 结构重新序列化为 STD 文本（SEGMENT/TOPIC/PART/Q/CUE/BULLET）
    用于覆盖写回 *_STD.txt，让后续任何读 STD 的流程都拿到“清洗后的 topic”。
    """
    out: List[str] = []
    seg_id = 0

    for seg in segments:
        seg_id += 1
        out.append(f"SEGMENT: {seg_id}")
        out.append(f"TOPIC: {(seg.get('topic') or '').strip()}")

        # Part 1
        part1 = seg.get("part1") or []
        if part1:
            out.append("PART: 1")
            for q in part1:
                out.append(f"Q: {q}")

        # Part 2
        part2 = seg.get("part2")
        if part2:
            out.append("PART: 2")
            cue = (part2.get("cue") or "").strip()
            if cue:
                out.append(f"CUE: {cue}")
            for b in (part2.get("bullets") or []):
                out.append(f"BULLET: {b}")

        # Part 3
        part3 = seg.get("part3") or []
        if part3:
            out.append("PART: 3")
            for q in part3:
                out.append(f"Q: {q}")

    return "\n".join(out).strip() + "\n"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="prefill txt path")
    args = ap.parse_args()

    std_path = convert_prefill_to_std(args.input)
    print(std_path)


if __name__ == "__main__":
    main()
