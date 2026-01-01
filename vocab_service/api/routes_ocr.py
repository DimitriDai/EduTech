# api/routes_ocr.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from services.ocr_service import extract_text_from_file, OCRConfig, OCRServiceError

router = APIRouter(tags=["ocr"])

# 你前面定的推荐限制
MAX_FILE_MB_PDF = 15
MAX_FILE_MB_IMAGE = 5
MAX_PDF_PAGES = 10

ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class OCRResponse(BaseModel):
    ok: bool = True
    passage_text: str
    filename: str
    content_type: Optional[str] = None
    file_bytes: int
    pdf_pages: Optional[int] = None


def _ext_from_upload(f: UploadFile) -> str:
    return Path((f.filename or "").strip()).suffix.lower()


def _size_limit_bytes(ext: str) -> int:
    if ext == ".pdf":
        return MAX_FILE_MB_PDF * 1024 * 1024
    return MAX_FILE_MB_IMAGE * 1024 * 1024


def _count_pdf_pages(pdf_path: Path) -> int:
    """
    只用于页数限制（防止小体积高页数PDF拖垮服务）
    依赖 poppler 的 pdfinfo。
    """
    try:
        from pdf2image.pdf2image import pdfinfo_from_path  # type: ignore
    except Exception as e:
        raise HTTPException(status_code=500, detail="pdf2image is required for PDF page counting") from e

    poppler_bin = os.getenv("POPPLER_PATH", "").strip() or None
    try:
        info = pdfinfo_from_path(str(pdf_path), poppler_path=poppler_bin)
        pages = int(info.get("Pages", 0) or 0)
        return pages
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to read PDF page count. Check POPPLER_PATH. Detail: {e}"
        )


@router.post("/passagetext", response_model=OCRResponse)
async def ocr_to_passage_text(file: UploadFile = File(...)):
    """
    单文件 OCR：PDF/图片 -> passage_text
    - 不清洗、不截断（前端负责“追加策略 + 最大长度”）
    """
    ext = _ext_from_upload(file)
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    limit = _size_limit_bytes(ext)

    tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_upload_"))
    tmp_path = tmp_dir / f"upload{ext}"

    total = 0
    try:
        # 流式落盘 + size cap（避免一次性读入内存）
        with tmp_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Limit={limit//(1024*1024)}MB for {ext}"
                    )
                f.write(chunk)

        pdf_pages = None
        if ext == ".pdf":
            pdf_pages = _count_pdf_pages(tmp_path)
            if pdf_pages <= 0:
                raise HTTPException(status_code=400, detail="PDF has 0 pages or cannot be read.")
            if pdf_pages > MAX_PDF_PAGES:
                raise HTTPException(
                    status_code=413,
                    detail=f"PDF too many pages. Limit={MAX_PDF_PAGES}, got={pdf_pages}"
                )

        try:
            cfg = OCRConfig(lang="eng", pdf_dpi=300)
            text = extract_text_from_file(str(tmp_path), cfg)
        except OCRServiceError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OCR failed: {e}")

        # 这里不做“空文本当错误”，因为有时用户也希望看到“没识别到”
        # 但为了前端好处理，我们仍返回 ok=True，只是 passage_text 可能为空串
        return OCRResponse(
            ok=True,
            passage_text=(text or ""),
            filename=file.filename or tmp_path.name,
            content_type=file.content_type,
            file_bytes=total,
            pdf_pages=pdf_pages,
        )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)