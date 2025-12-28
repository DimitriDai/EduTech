# vocab_service/api/routes_files.py
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

from utils.path_utils import ensure_run_out_dir

router = APIRouter(tags=["files"])

ALLOWED_CATEGORIES = {
    "vocab_note",
    "practice_docx",
    "audio",
    "grading_results",
}
DENY_CATEGORIES = {"practice_master"}

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{6,80}$")
# Windows-safe filename (no slashes / reserved chars). Allow dots for extensions.
_FILENAME_RE = re.compile(r"^[^\\/:*?\"<>|]+$")


def _validate_run_id(run_id: str) -> None:
    if not run_id or not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="invalid run_id")


def _validate_category(category: str) -> None:
    if category in DENY_CATEGORIES:
        raise HTTPException(status_code=403, detail=f"category not allowed: {category}")
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"unknown category: {category}")


def _validate_filename(filename: str) -> None:
    """
    ✅ 允许 .docx/.xlsx/.mp3 等扩展名
    ❌ 禁止路径分隔符、.. 穿越、空名
    """
    if not filename or not filename.strip():
        raise HTTPException(status_code=400, detail="missing filename")

    # basic windows-safe check (no slashes and reserved chars)
    if not _FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")

    # prevent traversal-ish patterns
    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    if ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    # small sanity limits
    if len(filename) > 255:
        raise HTTPException(status_code=400, detail="invalid filename")


def _safe_join(base: Path, *parts: str) -> Path:
    p = (base.joinpath(*parts)).resolve()
    base_r = base.resolve()
    if base_r not in p.parents and p != base_r:
        raise HTTPException(status_code=403, detail="path traversal blocked")
    return p


def _media_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext == ".zip":
        return "application/zip"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".wav":
        return "audio/wav"
    return "application/octet-stream"


# ----------------------------
# Root fallback rules (compat)
# ----------------------------
def _list_vocab_note_files_fallback(run_out_dir: Path, run_id: str) -> List[str]:
    """
    兼容旧目录结构：vocab_note 文件落在 run 根目录
    规则：
    - 以 vocab_note__{run_id} 开头的文件
    - 或以 vocab_note_{run_id} 开头的文件（你截图里就是这个命名）
    - 以及 vocab_note_audio.meta.json / _vocab_note_audio_input__{run_id}.json
    - 不返回 practice_master*.xlsx/json
    """
    if not run_out_dir.exists():
        return []

    files: List[str] = []
    for p in run_out_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name

        # 明确屏蔽 master（不允许下载）
        if name.startswith("practice_master__") or name.startswith("practice_master_"):
            continue
        if name.endswith(".meta.json") and name.startswith("practice_master"):
            continue

        if name.startswith(f"vocab_note__{run_id}") or name.startswith(f"vocab_note_{run_id}"):
            files.append(name)
            continue

        if name == "vocab_note_audio.meta.json":
            files.append(name)
            continue
        if name.startswith(f"_vocab_note_audio_input__{run_id}"):
            files.append(name)
            continue

    files.sort()
    return files


def _resolve_category_dir_or_fallback(
    run_out_dir: Path, category: str, run_id: str
) -> Tuple[Optional[Path], List[str]]:
    """
    返回：
    - category_dir: 如果目录存在则返回 Path，否则 None
    - fallback_files: 当 category=vocab_note 且目录不存在时，返回根目录匹配到的文件名列表
    """
    category_dir = _safe_join(run_out_dir, category)
    if category_dir.exists() and category_dir.is_dir():
        return category_dir, []

    if category == "vocab_note":
        return None, _list_vocab_note_files_fallback(run_out_dir, run_id)

    return None, []


def _sanitize_prefix(name_prefix: Optional[str]) -> str:
    if not name_prefix:
        return ""
    s = name_prefix.strip()
    if not s:
        return ""
    # 给前端“自定义命名戳”用：尽量温和，但保证安全
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", "_", s)
    s = s[:50]
    return s


@router.get("/out/{run_id}/{category}")
def list_run_category_files(run_id: str, category: str):
    """
    返回该 category 下可下载文件列表（文件名数组）。
    vocab_note 兼容旧结构：若 vocab_note/ 不存在，则从 run 根目录筛选 vocab_note_* 文件。
    """
    _validate_run_id(run_id)
    _validate_category(category)

    run_out_dir = ensure_run_out_dir(run_id)
    category_dir, fallback_files = _resolve_category_dir_or_fallback(run_out_dir, category, run_id)

    if fallback_files:
        return {"run_id": run_id, "category": category, "files": fallback_files}

    if not category_dir or not category_dir.exists():
        return {"run_id": run_id, "category": category, "files": []}

    files = [p.name for p in category_dir.iterdir() if p.is_file()]
    files.sort()
    return {"run_id": run_id, "category": category, "files": files}


def _zip_stream(files: List[Tuple[Path, str]], zip_filename: str) -> StreamingResponse:
    """
    files: [(abs_path, arcname_in_zip), ...]
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p, arcname in files:
            if not p.exists() or not p.is_file():
                continue
            zf.write(str(p), arcname=arcname)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )

@router.get("/out/{run_id}/bundle.zip")
def download_run_bundle_zip(
    run_id: str,
    name_prefix: Optional[str] = Query(default=None, description="可选：zip 文件名前缀/命名戳（前端用）"),
):
    """
    下载该 run_id 下所有允许下载的 category 的 bundle.zip
    - 只打包 ALLOWED_CATEGORIES
    - vocab_note 支持根目录 fallback
    """
    _validate_run_id(run_id)
    prefix = _sanitize_prefix(name_prefix)

    run_out_dir = ensure_run_out_dir(run_id)
    to_zip: List[Tuple[Path, str]] = []

    for category in sorted(ALLOWED_CATEGORIES):
        category_dir, fallback_files = _resolve_category_dir_or_fallback(run_out_dir, category, run_id)

        if fallback_files:
            for fn in fallback_files:
                p = _safe_join(run_out_dir, fn)
                arc = f"{category}/{fn}"
                if prefix:
                    arc = f"{prefix}/{arc}"
                to_zip.append((p, arc))
            continue

        if category_dir and category_dir.exists() and category_dir.is_dir():
            for p in category_dir.iterdir():
                if p.is_file():
                    arc = f"{category}/{p.name}"
                    if prefix:
                        arc = f"{prefix}/{arc}"
                    to_zip.append((p, arc))

    if not to_zip:
        raise HTTPException(status_code=404, detail="no files")

    zip_name = f"{run_id}__bundle.zip"
    if prefix:
        zip_name = f"{prefix}__{zip_name}"
    return _zip_stream(to_zip, zip_name)

@router.get("/out/{run_id}/{category}/zip")
def download_category_zip(
    run_id: str,
    category: str,
    name_prefix: Optional[str] = Query(default=None, description="可选：zip 文件名前缀/命名戳（前端用）"),
):
    """
    下载某个 category 的 zip：
    - 默认打包 storage/out/{run_id}/{category}/ 下所有文件
    - vocab_note 支持根目录 fallback：打包根目录匹配到的 vocab_note 文件
    - name_prefix 不改后端产物，仅影响 zip 文件名 & zip 内部路径前缀
    """
    _validate_run_id(run_id)
    _validate_category(category)

    prefix = _sanitize_prefix(name_prefix)
    run_out_dir = ensure_run_out_dir(run_id)
    category_dir, fallback_files = _resolve_category_dir_or_fallback(run_out_dir, category, run_id)

    to_zip: List[Tuple[Path, str]] = []

    if fallback_files:
        for fn in fallback_files:
            p = _safe_join(run_out_dir, fn)
            arc = f"{category}/{fn}"
            if prefix:
                arc = f"{prefix}/{arc}"
            to_zip.append((p, arc))
    else:
        if not category_dir or not category_dir.exists():
            raise HTTPException(status_code=404, detail="category not found")
        for p in category_dir.iterdir():
            if p.is_file():
                arc = f"{category}/{p.name}"
                if prefix:
                    arc = f"{prefix}/{arc}"
                to_zip.append((p, arc))

    if not to_zip:
        raise HTTPException(status_code=404, detail="no files")

    zip_name = f"{run_id}__{category}.zip"
    if prefix:
        zip_name = f"{prefix}__{zip_name}"
    return _zip_stream(to_zip, zip_name)



@router.get("/out/{run_id}/{category}/{filename}")
def download_run_file(run_id: str, category: str, filename: str):
    """
    下载单个文件：
    - 默认从 storage/out/{run_id}/{category}/{filename} 取
    - 若 category=vocab_note 且 vocab_note/ 不存在，则允许从 run 根目录 fallback 下载
    """
    _validate_run_id(run_id)
    _validate_category(category)
    _validate_filename(filename)

    run_out_dir = ensure_run_out_dir(run_id)
    category_dir, fallback_files = _resolve_category_dir_or_fallback(run_out_dir, category, run_id)

    # fallback：vocab_note 的文件落在 run 根目录
    if fallback_files:
        if filename not in fallback_files:
            raise HTTPException(status_code=404, detail="file not found (fallback)")
        file_path = _safe_join(run_out_dir, filename)
    else:
        if not category_dir:
            raise HTTPException(status_code=404, detail="category not found")
        file_path = _safe_join(category_dir, filename)

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(
        path=str(file_path),
        media_type=_media_type_for(file_path),
        filename=file_path.name,
    )
