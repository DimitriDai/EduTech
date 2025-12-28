# tools/missing_audio_enqueue.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _repo_root() -> str:
    # tools/.. -> project root
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _try_import_slug_utils():
    """
    尽量复用你项目里的 slug/normalize；如果不存在就 fallback。
    """
    sys.path.insert(0, _repo_root())
    try:
        from utils.slug import normalize_word, safe_filename_from_word  # type: ignore
        return normalize_word, safe_filename_from_word
    except Exception:
        return None, None


def _fallback_normalize_word(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKC", s)
    # collapse whitespace
    s = " ".join(s.split())
    return s


def _load_json(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _iter_entries(cache_obj: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """
    兼容你的 cache 结构：top-level key -> group {word_norm, entries:[...]}
    """
    for _, group in (cache_obj or {}).items():
        if not isinstance(group, dict):
            continue
        entries = group.get("entries", [])
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict):
                    yield e


def _url_to_local_path(audio_url: str, audio_root: str) -> Optional[str]:
    """
    支持两种 url_mode：
    - static: /static/audio/uk/xxx.mp3
    - relative: uk/xxx.mp3
    这里统一把它映射回本地 storage/audio_cache/{uk|us}/xxx.mp3
    """
    u = (audio_url or "").strip()
    if not u:
        return None

    u = u.replace("\\", "/")

    # find /uk/xxx.mp3 or /us/xxx.mp3
    if "/uk/" in u:
        tail = u.split("/uk/", 1)[1]
        return os.path.join(audio_root, "uk", tail)
    if "/us/" in u:
        tail = u.split("/us/", 1)[1]
        return os.path.join(audio_root, "us", tail)

    # relative form: uk/xxx.mp3 or us/xxx.mp3
    if u.startswith("uk/") or u.startswith("us/"):
        return os.path.join(audio_root, u.replace("/", os.sep))

    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_existing_queue_words(queue_path: str) -> Set[Tuple[str, str]]:
    """
    返回已存在的 (word_norm, accent) 集合，用于去重。
    """
    seen: Set[Tuple[str, str]] = set()
    if not os.path.exists(queue_path):
        return seen
    try:
        with open(queue_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    wn = str(obj.get("word_norm", "")).strip()
                    acc = str(obj.get("accent", "")).strip()
                    if wn and acc:
                        seen.add((wn, acc))
                except Exception:
                    continue
    except Exception:
        pass
    return seen


def _append_queue(queue_path: str, items: List[Dict[str, Any]]) -> int:
    if not items:
        return 0
    os.makedirs(os.path.dirname(queue_path), exist_ok=True)
    n = 0
    with open(queue_path, "a", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
            n += 1
    return n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--global_cache", required=True)
    parser.add_argument("--uploaded_cache", default="")
    parser.add_argument("--audio_root", required=True, help='e.g. "storage/audio_cache"')
    parser.add_argument("--queue_path", default="storage/missing_audio_queue.jsonl")
    parser.add_argument("--accent", choices=["uk", "us", "both"], default="both")
    parser.add_argument("--check_file_exists", action="store_true", help="also enqueue if url exists but file missing")
    parser.add_argument("--source", default="scan_cache", help="tag in queue entries")
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit")
    args = parser.parse_args()

    normalize_word, safe_filename_from_word = _try_import_slug_utils()
    if normalize_word is None:
        normalize_word = _fallback_normalize_word

    global_obj = _load_json(args.global_cache)
    uploaded_obj = _load_json(args.uploaded_cache) if args.uploaded_cache else {}

    # 去重：避免队列无限增长
    seen = _read_existing_queue_words(args.queue_path)

    want_accents: List[str]
    if args.accent == "both":
        want_accents = ["uk", "us"]
    else:
        want_accents = [args.accent]

    enqueue_items: List[Dict[str, Any]] = []

    def consider_entry(e: Dict[str, Any], origin: str):
        nonlocal enqueue_items, seen

        word_original = str(e.get("word_original", "") or "").strip()
        wn = str(e.get("word_norm", "") or "").strip()
        if not wn and word_original:
            wn = normalize_word(word_original)

        if not wn:
            return

        for acc in want_accents:
            key = f"audio_{acc}"
            url = str(e.get(key, "") or "").strip()

            need = False
            reason = ""

            if not url:
                need = True
                reason = "missing_field"
            elif args.check_file_exists:
                local = _url_to_local_path(url, args.audio_root)
                if local and (not os.path.exists(local)):
                    need = True
                    reason = "missing_file"

            if need:
                sig = (wn, acc)
                if sig in seen:
                    continue
                seen.add(sig)

                enqueue_items.append({
                    "word_norm": wn,
                    "word_original": word_original,
                    "accent": acc,
                    "reason": reason,
                    "source": args.source,
                    "origin": origin,
                    "ts": _now_iso(),
                })

    # 先扫 global，再扫 uploaded（你策略里 global 是权威；uploaded 只是补池）
    count = 0
    for e in _iter_entries(global_obj):
        consider_entry(e, origin="global_cache")
        count += 1
        if args.limit and len(enqueue_items) >= args.limit:
            break

    if (not args.limit) or (len(enqueue_items) < args.limit):
        for e in _iter_entries(uploaded_obj):
            consider_entry(e, origin="uploaded_cache")
            count += 1
            if args.limit and len(enqueue_items) >= args.limit:
                break

    added = _append_queue(args.queue_path, enqueue_items)

    print(f"[DONE] scanned_entries≈{count}, queued_added={added}, queue_path={args.queue_path}")


if __name__ == "__main__":
    main()