# vocab_service/api/routes_grading.py
from __future__ import annotations

import os
import uuid
import shutil
from typing import List, Any, Dict, Optional, Tuple

from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from utils.path_utils import ensure_run_out_dir

from services.grading_service import (
    is_zip,
    extract_zip,
    make_zip,
    build_example_index_from_shuffle_master_xlsx,
    build_example_index_from_cache,
    grade_docx,
)

from generators.grading_docx_generator import write_feedback_docx

router = APIRouter(tags=["grading"])

# -----------------------------
# Project paths
# -----------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STORAGE_DIR = os.path.join(PROJECT_ROOT, "storage")
TMP_DIR = os.path.join(STORAGE_DIR, "tmp")

# “乱序答案库”（优先用它，命中更快更准）
# 默认放在 storage/out/shuffle_e2c_master.xlsx（全局固定文件）
SHUFFLE_MASTER_XLSX = os.path.join(STORAGE_DIR, "out", "shuffle_e2c_master.xlsx")

# fallback：从缓存里抠 example/example_cn
GLOBAL_CACHE_JSON = os.path.join(STORAGE_DIR, "global_cache.json")
UPLOADED_CACHE_JSON = os.path.join(STORAGE_DIR, "uploaded_vocab_cache.json")


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _safe_filename(name: str) -> str:
    # 避免奇怪文件名导致路径问题
    name = (name or "").strip() or "upload.bin"
    return name.replace("\\", "_").replace("/", "_")


def _save_upload_to_tmp(upload: UploadFile, request_id: str, run_id: str) -> str:
    # tmp 也按 run_id 分一层，后续清理更舒服
    tmp_run_dir = os.path.join(TMP_DIR, run_id)
    _ensure_dir(tmp_run_dir)

    safe = _safe_filename(upload.filename or "")
    tmp_path = os.path.join(tmp_run_dir, f"{request_id}__{safe}")

    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(upload.file, f)

    return tmp_path


def _build_indexes(use_shuffle_master: bool) -> Tuple[Any, Any, Dict[str, Any], str]:
    """
    返回 (idx_cn, idx_no_cn, idx_meta, mode)
    """
    if use_shuffle_master and os.path.exists(SHUFFLE_MASTER_XLSX):
        idx_cn, idx_no_cn, idx_meta = build_example_index_from_shuffle_master_xlsx(SHUFFLE_MASTER_XLSX)
        return idx_cn, idx_no_cn, idx_meta, "shuffle_master"

    if not os.path.exists(GLOBAL_CACHE_JSON):
        raise FileNotFoundError(f"global cache not found: {GLOBAL_CACHE_JSON}")

    idx_cn = build_example_index_from_cache(
        global_cache_path=GLOBAL_CACHE_JSON,
        uploaded_cache_path=UPLOADED_CACHE_JSON if os.path.exists(UPLOADED_CACHE_JSON) else None,
    )
    return idx_cn, None, {"mode": "cache_json"}, "cache_json"


@router.post("/vocab/grading")
def vocab_grading(
    file: UploadFile = File(...),

    # ✅ 你要的：强制写入 storage/out/{run_id}/grading_results
    run_id: str = Query(..., description="本次操作的 run_id，用于输出目录 storage/out/{run_id}/grading_results"),

    use_shuffle_master: bool = Query(
        True,
        description="优先用 storage/out/shuffle_e2c_master.xlsx 构建例句索引；没有则 fallback 到 cache",
    ),

    # DeepSeek 调参（grading_service 已支持）
    timeout_s: int = Query(30, ge=5, le=120),
    retries: int = Query(2, ge=0, le=5),
    retry_backoff_sec: float = Query(1.0, ge=0.0, le=10.0),
    temperature: float = Query(0.2, ge=0.0, le=1.0),
    max_tokens: int = Query(320, ge=64, le=800),
    use_ref_when_blank: bool = Query(True, description="学生空白时是否用参考例句填入正确版本"),

    # 返回类型
    return_meta_only: bool = Query(False, description="仅返回 meta（不生成 docx），用于调试"),
):
    """
    支持：
    - 上传单个练习 .docx → 返回批改反馈 .docx
    - 上传 .zip（包含多个 docx）→ 返回批改反馈 zip
    输出目录（固定）：
      storage/out/{run_id}/grading_results/
    """
    request_id = str(uuid.uuid4())[:12]

    # 1) 基本校验
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")

    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in [".docx", ".zip"]:
        raise HTTPException(status_code=400, detail="only .docx or .zip supported")

    # 2) 保存上传到 tmp
    in_path = _save_upload_to_tmp(file, request_id=request_id, run_id=run_id)

    # 3) 构建索引
    try:
        idx_cn, idx_no_cn, idx_meta, idx_mode = _build_indexes(use_shuffle_master=use_shuffle_master)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"build example index failed: {e}")

    # 4) 输出目录：storage/out/{run_id}/grading_results
    run_out_dir = ensure_run_out_dir(run_id)
    grading_out_dir = (run_out_dir / "grading_results")
    grading_out_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------
    # 单 docx
    # -----------------------------
    if not is_zip(in_path):
        results, meta = grade_docx(
            docx_path=in_path,
            example_index=idx_cn,
            example_index_by_no=idx_no_cn,
            request_id=request_id,
            timeout_s=timeout_s,
            retries=retries,
            retry_backoff_sec=retry_backoff_sec,
            temperature=temperature,
            max_tokens=max_tokens,
            use_ref_when_blank=use_ref_when_blank,
        )

        meta["index_mode"] = idx_mode
        meta["index_meta"] = idx_meta
        meta["run_id"] = run_id

        if return_meta_only:
            return JSONResponse({"request_id": request_id, "meta": meta})

        stem = os.path.splitext(os.path.basename(file.filename))[0] or "practice"
        out_docx = grading_out_dir / f"{stem}__graded.docx"

        # ✅ 统一用 str(path)，避免 generator 内部如果做 os.path 操作出类型问题
        write_feedback_docx(
            input_docx_path=in_path,
            output_docx_path=str(out_docx),
            results=results,
        )

        return FileResponse(
            str(out_docx),
            filename=os.path.basename(str(out_docx)),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    # -----------------------------
    # zip 批量
    # -----------------------------
    extracted_dir = grading_out_dir / f"extracted__{request_id}"
    docx_list = extract_zip(in_path, str(extracted_dir))
    if not docx_list:
        raise HTTPException(status_code=400, detail="zip contains no .docx")

    out_files: List[str] = []
    all_meta: Dict[str, Any] = {
        "request_id": request_id,
        "run_id": run_id,
        "index_mode": idx_mode,
        "index_meta": idx_meta,
        "files_total": len(docx_list),
        "files": [],
    }

    for one in docx_list:
        base = os.path.splitext(os.path.basename(one))[0] or "practice"

        results, meta = grade_docx(
            docx_path=one,
            example_index=idx_cn,
            example_index_by_no=idx_no_cn,
            request_id=request_id,
            timeout_s=timeout_s,
            retries=retries,
            retry_backoff_sec=retry_backoff_sec,
            temperature=temperature,
            max_tokens=max_tokens,
            use_ref_when_blank=use_ref_when_blank,
        )

        out_docx = grading_out_dir / f"{base}__graded.docx"
        if not return_meta_only:
            write_feedback_docx(
                input_docx_path=one,
                output_docx_path=str(out_docx),
                results=results,
            )
            out_files.append(str(out_docx))

        all_meta["files"].append({"input": os.path.basename(one), "meta": meta})

    if return_meta_only:
        return JSONResponse(all_meta)

    zip_path = grading_out_dir / f"grading_results__{run_id}.zip"
    make_zip(str(zip_path), out_files)

    return FileResponse(
        str(zip_path),
        filename=os.path.basename(str(zip_path)),
        media_type="application/zip",
    )