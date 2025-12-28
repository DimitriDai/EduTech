# vocab_service/tools/grade_practice_docx_batch.py
from __future__ import annotations

import os
import json
import uuid
import argparse
import tempfile
from datetime import datetime
from typing import List, Optional, Tuple

from services.grading_service import (
    build_example_index_from_shuffle_master_xlsx,
    build_example_index_from_cache,
    grade_docx,
    is_zip,
    extract_zip,
    ensure_dir,
    make_zip,
)
from generators.grading_docx_generator import write_feedback_docx


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="A .docx file OR a .zip containing .docx files")

    ap.add_argument("--shuffle_master_xlsx", default="", help="Path to shuffle_e2c_master.xlsx (recommended for speed)")

    ap.add_argument("--out_dir", default="storage/out/grading", help="output directory")
    ap.add_argument("--global_cache", default="storage/global_cache.json", help="global cache json path (fallback)")
    ap.add_argument("--uploaded_cache", default="storage/uploaded_vocab_cache.json", help="uploaded vocab cache json path (fallback)")

    ap.add_argument("--timeout", type=int, default=30, help="DeepSeek timeout seconds")
    ap.add_argument("--retries", type=int, default=2, help="DeepSeek retries for each request")
    ap.add_argument("--backoff", type=float, default=1.0, help="retry backoff base seconds")

    ap.add_argument("--zip_output", action="store_true", help="Always output zip even for single docx")
    args = ap.parse_args()

    run_id = str(uuid.uuid4())
    stamp = now_stamp()
    ensure_dir(args.out_dir)

    # Build answer index (FAST first)
    example_index = None
    example_index_by_no = None
    index_meta = {}

    if args.shuffle_master_xlsx and os.path.exists(args.shuffle_master_xlsx):
        idx_cn, idx_no_cn, meta = build_example_index_from_shuffle_master_xlsx(args.shuffle_master_xlsx)
        example_index = idx_cn
        example_index_by_no = idx_no_cn
        index_meta = {"mode": "shuffle_master_xlsx", "meta": meta}
    else:
        uploaded_cache = args.uploaded_cache if os.path.exists(args.uploaded_cache) else None
        example_index = build_example_index_from_cache(args.global_cache, uploaded_cache)
        example_index_by_no = None
        index_meta = {"mode": "cache_json", "meta": {"global_cache": args.global_cache, "uploaded_cache": uploaded_cache}}

    input_path = args.input
    out_files: List[str] = []
    meta_all: List[dict] = []

    def process_one_docx(docx_path: str):
        request_id = str(uuid.uuid4())
        results, meta = grade_docx(
            docx_path=docx_path,
            example_index=example_index,
            example_index_by_no=example_index_by_no,
            request_id=request_id,
            timeout_s=args.timeout,
            retries=args.retries,
            retry_backoff_sec=args.backoff,
            temperature=0.2,
            max_tokens=320,
            use_ref_when_blank=True,
        )
        meta["answer_index"] = index_meta
        return results, meta

    if is_zip(input_path):
        with tempfile.TemporaryDirectory() as td:
            docxs = extract_zip(input_path, td)
            if not docxs:
                raise RuntimeError("No .docx found in zip.")

            for p in docxs:
                results, meta = process_one_docx(p)
                meta_all.append(meta)

                base = os.path.splitext(os.path.basename(p))[0]
                out_docx = os.path.join(
                    args.out_dir,
                    f"批改反馈_例句中译英_{base}_{stamp}_{run_id}.docx"
                )
                write_feedback_docx(p, out_docx, results)
                out_files.append(out_docx)

    else:
        results, meta = process_one_docx(input_path)
        meta_all.append(meta)

        base = os.path.splitext(os.path.basename(input_path))[0]
        out_docx = os.path.join(
            args.out_dir,
            f"批改反馈_例句中译英_{base}_{stamp}_{run_id}.docx"
        )
        write_feedback_docx(input_path, out_docx, results)
        out_files.append(out_docx)

    meta_path = os.path.join(args.out_dir, f"grading_meta_{stamp}_{run_id}.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_all, f, ensure_ascii=False, indent=2)

    if len(out_files) == 1 and not args.zip_output:
        print(out_files[0])
        print(meta_path)
        return

    zip_path = os.path.join(args.out_dir, f"批改反馈_例句中译英_{stamp}_{run_id}.zip")
    make_zip(zip_path, out_files + [meta_path])
    print(zip_path)


if __name__ == "__main__":
    main()
