# tools/audio_missing_fill.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Set, Tuple


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read_queue(queue_path: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not os.path.exists(queue_path):
        return items
    with open(queue_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    items.append(obj)
            except Exception:
                continue
    return items


def _write_queue(queue_path: str, items: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(queue_path), exist_ok=True)
    with open(queue_path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def _unique_by_word(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    去重策略：按 (word_norm, accent) 去重
    """
    seen: Set[Tuple[str, str]] = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        wn = str(it.get("word_norm", "")).strip()
        acc = str(it.get("accent", "")).strip()
        if not wn or not acc:
            continue
        sig = (wn, acc)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(it)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue_path", default="storage/missing_audio_queue.jsonl")
    parser.add_argument("--global_cache", required=True)
    parser.add_argument("--uploaded_cache", default="")
    parser.add_argument("--write_uploaded", action="store_true")
    parser.add_argument("--audio_root", required=True)
    parser.add_argument("--format", choices=["mp3", "wav"], default="mp3")

    parser.add_argument("--piper_bin", required=True)
    parser.add_argument("--uk_model", required=True)
    parser.add_argument("--us_model", required=True)

    parser.add_argument("--url_mode", choices=["static", "relative"], default="static")
    parser.add_argument("--url_prefix", default="/static/audio")

    parser.add_argument("--batch", type=int, default=200, help="how many queue items to process per run")
    parser.add_argument("--dry_run", action="store_true")
    args = parser.parse_args()

    repo_root = _repo_root()
    audio_build_py = os.path.join(repo_root, "tools", "audio_cache_build.py")
    if not os.path.exists(audio_build_py):
        raise FileNotFoundError(f"audio_cache_build.py not found: {audio_build_py}")

    all_items = _read_queue(args.queue_path)
    if not all_items:
        print("[DONE] queue empty. nothing to do.")
        return

    deduped = _unique_by_word(all_items)

    # 取本次 batch
    to_process = deduped[: args.batch]
    if not to_process:
        print("[DONE] nothing to process after dedupe.")
        return

    # ====== 新增：把本次 batch 的词写到临时文件，精确驱动 audio_cache_build ======
    words = []
    seen_w = set()
    for it in to_process:
        wn = str(it.get("word_norm", "")).strip()
        if wn and wn not in seen_w:
            seen_w.add(wn)
            words.append(wn)

    tmp_dir = os.path.join(repo_root, "storage", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    only_words_path = os.path.join(tmp_dir, "only_words__missing_fill.txt")
    with open(only_words_path, "w", encoding="utf-8") as f:
        for w in words:
            f.write(w + "\n")


    # 这里的策略：不按 accent 分两次跑（简化）
    # audio_cache_build 会同时生成 uk/us（你传入两个模型）
    # 所以本次只需要用 word_norm 集合去驱动生成即可。
    # 但 audio_cache_build 本身是“扫 cache 生成缺失”，因此我们用 --repair_missing_files + --limit 来控制工作量。
    cmd = [
        sys.executable, audio_build_py,
        "--global_cache", args.global_cache,
        "--audio_root", args.audio_root,
        "--format", args.format,
        "--piper_bin", args.piper_bin,
        "--uk_model", args.uk_model,
        "--us_model", args.us_model,
        "--url_mode", args.url_mode,
        "--url_prefix", args.url_prefix,
        "--repair_missing_files",
        "--only_words_file", only_words_path,
        "--limit", str(len(words) or 0),

    ]

    # 如果你也要补 uploaded（一般不建议写回 uploaded，但你这里提供开关）
    if args.uploaded_cache:
        cmd += ["--uploaded_cache", args.uploaded_cache]
        if args.write_uploaded:
            cmd += ["--write_uploaded"]

    if args.dry_run:
        cmd += ["--dry_run"]

    print("[INFO] running:", " ".join(cmd))

    # 直接调用现有生成器
    p = subprocess.run(cmd, capture_output=False)
    if p.returncode != 0:
        raise RuntimeError(f"audio_cache_build failed with code={p.returncode}")

    # 生成成功：移除本次 batch 对应的 (word_norm, accent) 项
    processed_set = {(str(it.get("word_norm","")).strip(), str(it.get("accent","")).strip()) for it in to_process}

    remaining: List[Dict[str, Any]] = []
    for it in all_items:
        wn = str(it.get("word_norm", "")).strip()
        acc = str(it.get("accent", "")).strip()
        if (wn, acc) in processed_set:
            continue
        remaining.append(it)

    # 备份旧队列
    backup = args.queue_path + ".bak"
    try:
        if os.path.exists(args.queue_path):
            os.replace(args.queue_path, backup)
    except Exception:
        pass

    _write_queue(args.queue_path, remaining)

    print(f"[DONE] processed={len(processed_set)}, remaining={len(remaining)}, queue_backup={backup}")


if __name__ == "__main__":
    main()