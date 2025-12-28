# speaking_service/modules/speaking/cache.py
import os
import json
import hashlib
from typing import Optional, Dict, Any

def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def normalize_question(text: str) -> str:
    t = (text or "").strip().lower()
    t = " ".join(t.split())  # 压缩空格
    # 去掉常见的编号前缀
    # 例如 "1. xxx" / "Q1 xxx"
    for prefix in ["q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "q9"]:
        if t.startswith(prefix + " "):
            t = t[len(prefix)+1:].strip()
    # 去掉开头的 "1." "2." 这类
    if len(t) >= 2 and t[0].isdigit() and t[1] in [".", "、", ")"]:
        t = t[2:].strip()
    return t

def make_key(exam: str, topic: str, part: str, question_text: str, score: str) -> str:
    base = f"{exam}|{topic}|{part}|{normalize_question(question_text)}|{score}"
    return _sha1(base)

def cache_path(cache_dir: str, key: str) -> str:
    return os.path.join(cache_dir, f"{key}.json")

def get(cache_dir: str, key: str) -> Optional[Dict[str, Any]]:
    path = cache_path(cache_dir, key)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def set(cache_dir: str, key: str, payload: Dict[str, Any]) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    path = cache_path(cache_dir, key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
