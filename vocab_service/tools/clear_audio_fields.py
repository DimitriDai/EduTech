# tools/clear_audio_fields.py
# -*- coding: utf-8 -*-
"""
Clear audio-related fields in cache json, safely (atomic write).

It will:
- scan all entry-like dicts
- clear: audio_uk, audio_us, audio_provider, audio_version
- optionally remove keys entirely instead of empty string

Usage:
python .\tools\clear_audio_fields.py --cache "storage/global_cache.json" --mode empty
python .\tools\clear_audio_fields.py --cache "storage/global_cache.json" --mode delete
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


AUDIO_KEYS = ["audio_uk", "audio_us", "audio_provider", "audio_version"]

WORD_KEYS_CANDIDATES = [
    "word_norm",
    "word_original",
    "word",
    "english",
    "en",
    "英文单词",
    "English",
]


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def normalize_word(text: str) -> str:
    return (text or "").strip()


def extract_word_from_entry(obj: Dict[str, Any]) -> Optional[str]:
    for k in WORD_KEYS_CANDIDATES:
        v = obj.get(k)
        if isinstance(v, str):
            vv = normalize_word(v)
            if vv:
                return vv
    return None


def looks_like_entry(obj: Dict[str, Any]) -> bool:
    # avoid treating group dict with "entries" list as entry
    return extract_word_from_entry(obj) is not None and not isinstance(obj.get("entries"), list)


def find_entry_dicts(root: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if looks_like_entry(x):
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(root)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True, help="path to cache json, e.g. storage/global_cache.json")
    ap.add_argument("--mode", choices=["empty", "delete"], default="empty",
                    help='empty: set audio fields to ""; delete: remove keys entirely')
    ap.add_argument("--limit", type=int, default=0, help="only process first N entries (0=all)")
    ap.add_argument("--dry_run", action="store_true", help="do not write file, only report")
    args = ap.parse_args()

    cache_path = Path(args.cache)
    if not cache_path.exists():
        raise FileNotFoundError(f"cache not found: {cache_path}")

    data = load_json(cache_path)
    entries = find_entry_dicts(data)
    total = len(entries)

    if args.limit and args.limit > 0:
        entries = entries[:args.limit]

    changed_entries = 0
    cleared_fields = 0

    for e in entries:
        changed = False
        for k in AUDIO_KEYS:
            if k in e and e.get(k) not in ("", None):
                if args.mode == "empty":
                    e[k] = ""
                else:
                    e.pop(k, None)
                changed = True
                cleared_fields += 1
            elif args.mode == "empty":
                # ensure key exists as empty string (optional; helps keep schema stable)
                if k not in e:
                    e[k] = ""
                    changed = True
        if changed:
            changed_entries += 1

    print(f"[DONE] scanned_entries={total}, processed={len(entries)}, changed_entries={changed_entries}, cleared_fields={cleared_fields}, mode={args.mode}, dry_run={args.dry_run}")

    if not args.dry_run:
        atomic_write_json(cache_path, data)
        print(f"[WROTE] {cache_path}")


if __name__ == "__main__":
    main()