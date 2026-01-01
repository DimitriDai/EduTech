# -*- coding: utf-8 -*-
"""
tools/clean_global_cache_display.py

一次性清洗 global_cache.json 的 group.word_display：
- 纠正“句首误大写”污染
- 处理少量高确定性专名/缩写
- 生成备份 + 输出变更统计
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from typing import Dict, Any, Tuple


def compute_word_display_simple(word_display: str, word_norm: str) -> str:
    """
    极保守：只在高度确定时返回规范展示，否则返回 "" 表示“不覆盖”
    这里用现有 display + norm 判断（因为我们在清洗 group 层）
    """
    wn = (word_norm or "").strip()
    if not wn:
        return ""

    # 用 display 作为“原形态线索”，但不信任它；只是辅助判断句首误大写
    wd = (word_display or "").strip()

    UPPER = {
        "uk": "UK",
        "us": "US",
        "usa": "USA",
        "eu": "EU",
        "un": "UN",
        "u.k.": "UK",
        "u.s.": "US",
        "u.s.a.": "USA",
    }
    if wn in UPPER:
        return UPPER[wn]

    if wn.startswith("the "):
        tail = wn[4:].strip()
        if tail in UPPER:
            return "the " + UPPER[tail]
        if tail in ("united states", "united kingdom"):
            return "the " + tail.title()

    PROPER = {
        "france": "France",
        "china": "China",
        "australia": "Australia",
        "chinese": "Chinese",
        "australian": "Australian",
        "british": "British",
        "french": "French",
        "english": "English",
        "american": "American",
    }
    if wn in PROPER:
        return PROPER[wn]

    parts = wn.split()
    if len(parts) >= 2 and parts[0] in PROPER:
        parts[0] = PROPER[parts[0]]
        return " ".join(parts)

    # 句首误大写污染：display 是 Apple 但 norm 是 apple
    if len(wd) >= 2 and wd[0].isupper() and wd[1:].islower() and wn == wd.lower():
        return ""  # 不覆盖，让外层回退到 wn

    return ""


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def clean_global_cache(path: str, dry_run: bool = False) -> Tuple[int, int]:
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError("global_cache.json root must be an object/dict")

    changed = 0
    total = 0

    for wn, group in data.items():
        if not isinstance(group, dict):
            continue
        total += 1

        old = (group.get("word_display") or "").strip()
        wn2 = (group.get("word_norm") or wn or "").strip()

        # 目标 display：默认用 wn（稳定）
        new_display = wn2

        # 如果旧 display 存在，先判断它是否是“句首误大写污染”
        if old:
            # 若 old 不是明显污染，保守地先保留 old
            new_display = old
            if len(old) >= 2 and old[0].isupper() and old[1:].islower() and wn2 == old.lower():
                new_display = wn2  # 回退到 norm

        # 专名/缩写：若能高度确定则覆盖
        better = compute_word_display_simple(old, wn2)
        if better:
            new_display = better

        if new_display != old:
            group["word_display"] = new_display
            data[wn] = group
            changed += 1

    if not dry_run:
        save_json(path, data)
    return total, changed


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)  # vocab_service/
    cache_path = os.path.join(root, "storage", "global_cache.json")

    if not os.path.exists(cache_path):
        print("[SKIP] global_cache.json not found:", cache_path)
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = cache_path + f".bak_{ts}"
    shutil.copy2(cache_path, backup)
    print("[BACKUP]", backup)

    total, changed = clean_global_cache(cache_path, dry_run=True)
    print(f"[DRY_RUN] total_groups={total} would_change={changed}")
    print("[NO WRITE] global_cache.json NOT modified")


if __name__ == "__main__":
    main()
