# speaking_service/modules/speaking/parser.py
import re
from typing import Dict, List, Any

def parse_prefill(text: str) -> List[Dict[str, Any]]:
    """
    返回结构：
    [
      {
        "topic": "Friends",
        "parts": {
          "Part 1": ["Do you ...?", "What ...?"],
          "Part 2": ["Describe ...", "You should say: ..."],
          "Part 3": ["What kind ...?", "Why ...?"]
        }
      },
      ...
    ]
    约定：Topic 标题是一行独立文本；Part 1/2/3 标识为独立行。
    """
    lines = [ln.strip() for ln in text.splitlines()]
    # 去空行，但保留结构
    lines = [ln for ln in lines if ln]

    topics: List[Dict[str, Any]] = []
    cur = None
    cur_part = None

    part_pat = re.compile(r"^part\s*[123]$", re.I)

    def start_topic(title: str):
        nonlocal cur, cur_part
        cur = {"topic": title.strip(), "parts": {"Part 1": [], "Part 2": [], "Part 3": []}}
        cur_part = None
        topics.append(cur)

    for ln in lines:
        if part_pat.match(ln):
            pnum = ln.lower().replace(" ", "")
            cur_part = {"part1": "Part 1", "part2": "Part 2", "part3": "Part 3"}[pnum]
            continue

        # 如果这一行不是 Part，且当前没有 topic，则认为是 topic 标题
        if cur is None:
            start_topic(ln)
            continue

        # 如果当前有 topic，但还没进入 part，则遇到新的标题行：开启新 topic
        if cur_part is None:
            start_topic(ln)
            continue

        # 进入了某个 part：收集问题/要点行
        # 去掉编号前缀： "1. " / "2) " / "1、"
        cleaned = re.sub(r"^\s*\d+\s*[\.\)\、]\s*", "", ln).strip()
        if cleaned:
            cur["parts"][cur_part].append(cleaned)

    return topics