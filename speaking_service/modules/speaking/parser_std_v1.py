# parser_std.py
# ============================================================
# 作用：
#   解析 STD txt（支持多 SEGMENT）
#
# 输入 STD 示例：
#   SEGMENT: 1
#   TOPIC: Friends
#   PART: 1
#   Q: ...
#   SEGMENT: 2
#   TOPIC: 家中重要老物件
#   PART: 2
#   CUE: ...
#   BULLET: ...
#   PART: 3
#   Q: ...
#
# 输出结构（dict）：
#   {
#     "segments": [
#       {
#         "segment_id": 1,
#         "topic": "Friends",
#         "part1": [...],
#         "part2": {"cue": "...", "bullets": [...] } or None,
#         "part3": [...]
#       },
#       ...
#     ]
#   }
# ============================================================

import re
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Any


@dataclass
class Part2CueCard:
    cue: str
    bullets: List[str]


@dataclass
class SegmentParsed:
    segment_id: int
    topic: str
    part1: List[str]
    part2: Optional[Part2CueCard]
    part3: List[str]


SEGMENT_RE = re.compile(r"^\s*SEGMENT:\s*(\d+)\s*$", re.I)
TOPIC_RE = re.compile(r"^\s*TOPIC:\s*(.+?)\s*$", re.I)
PART_RE = re.compile(r"^\s*PART:\s*([123])\s*$", re.I)
Q_RE = re.compile(r"^\s*Q:\s*(.+?)\s*$", re.I)
CUE_RE = re.compile(r"^\s*CUE:\s*(.+?)\s*$", re.I)
BULLET_RE = re.compile(r"^\s*BULLET:\s*(.+?)\s*$", re.I)


def parse_std_prefill_text(text: str) -> Dict[str, Any]:
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]

    segments: List[SegmentParsed] = []

    cur_seg_id = 0
    cur_topic = ""
    cur_part = 0

    cur_p1: List[str] = []
    cur_p3: List[str] = []
    cur_p2_cue = ""
    cur_p2_bullets: List[str] = []
    seen_bullets = set()

    def flush_segment():
        nonlocal cur_seg_id, cur_topic, cur_p1, cur_p2_cue, cur_p2_bullets, cur_p3, seen_bullets
        if cur_seg_id == 0:
            return
        part2_obj = None
        if cur_p2_cue or cur_p2_bullets:
            part2_obj = Part2CueCard(cue=cur_p2_cue, bullets=cur_p2_bullets)

        segments.append(
            SegmentParsed(
                segment_id=cur_seg_id,
                topic=cur_topic,
                part1=cur_p1,
                part2=part2_obj,
                part3=cur_p3,
            )
        )
        # reset for next
        cur_p1 = []
        cur_p3 = []
        cur_p2_cue = ""
        cur_p2_bullets = []
        seen_bullets = set()

    for ln in lines:
        m = SEGMENT_RE.match(ln)
        if m:
            # 新 segment 开始
            flush_segment()
            cur_seg_id = int(m.group(1))
            cur_topic = ""
            cur_part = 0
            continue

        m = TOPIC_RE.match(ln)
        if m:
            # 允许同 segment 内更新 topic：以“最后一次 TOPIC”为准
            cur_topic = m.group(1).strip()
            continue

        m = PART_RE.match(ln)
        if m:
            cur_part = int(m.group(1))
            continue

        if cur_part == 1:
            m = Q_RE.match(ln)
            if m:
                cur_p1.append(m.group(1).strip())
            continue

        if cur_part == 2:
            m = CUE_RE.match(ln)
            if m:
                cur_p2_cue = m.group(1).strip()
                continue
            m = BULLET_RE.match(ln)
            if m:
                b = m.group(1).strip()
                key = b.lower()
                if key not in seen_bullets:
                    cur_p2_bullets.append(b)
                    seen_bullets.add(key)
            continue

        if cur_part == 3:
            m = Q_RE.match(ln)
            if m:
                cur_p3.append(m.group(1).strip())
            continue

        # 其他忽略

    flush_segment()

    return {"segments": [asdict(s) for s in segments]}


def parse_std_prefill_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return parse_std_prefill_text(f.read())


def to_dict(parsed: Dict[str, Any]) -> Dict[str, Any]:
    return parsed


if __name__ == "__main__":
    import argparse, json

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="*_STD.txt 文件路径")
    args = ap.parse_args()

    data = parse_std_prefill_file(args.input)
    print(json.dumps(data, ensure_ascii=False, indent=2))
