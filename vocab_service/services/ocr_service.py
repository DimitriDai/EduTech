# services/ocr_service.py
# -*- coding: utf-8 -*-
"""
# IMPORTANT:
# This service is intentionally designed for SINGLE FILE ONLY.
# Do NOT batch files here. Batch control belongs to API/UI layer.

OCR Service (single-file)
- Input: one local file path (PDF or image)
- Output: extracted plain text (str)
- No LLM, no cleaning, no truncation (leave that to UI / upstream)

Dependencies (install locally):
  pip install pytesseract pillow pdf2image

System dependencies:
- Tesseract OCR installed and accessible
  - Windows: install tesseract, then set env TESSERACT_CMD="C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
  - macOS: brew install tesseract
  - Linux: apt-get install tesseract-ocr

- For PDF: poppler
  - Windows: download poppler, set env POPPLER_PATH="C:\\poppler\\Library\\bin"
  - macOS: brew install poppler
  - Linux: apt-get install poppler-utils
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PIL import Image


@dataclass(frozen=True)
class OCRConfig:
    # OCR language for Tesseract. Use "eng" for English.
    # If you need both English+Chinese, set: "eng+chi_sim" (requires language packs installed).
    lang: str = "eng"

    # For PDF rasterization (higher DPI = better accuracy but slower/more memory)
    pdf_dpi: int = 300

    # Optional: Tesseract engine mode and page segmentation mode
    # Common good default: psm=3 (auto), oem=3 (default)
    tesseract_oem: int = 3
    tesseract_psm: int = 3

    # Poppler bin path (Windows often needs it). If empty, read env POPPLER_PATH.
    poppler_path: str = ""

    # Tesseract executable path (Windows often needs it). If empty, read env TESSERACT_CMD.
    tesseract_cmd: str = ""


class OCRServiceError(RuntimeError):
    pass


def _is_pdf(p: Path) -> bool:
    return p.suffix.lower() == ".pdf"


def _is_image(p: Path) -> bool:
    return p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _ensure_deps(config: OCRConfig) -> None:
    try:
        import pytesseract  # noqa: F401
    except Exception as e:
        raise OCRServiceError(
            "Missing dependency: pytesseract. Install with: pip install pytesseract"
        ) from e

    # Apply tesseract path if provided / env
    import pytesseract  # type: ignore

    tcmd = (config.tesseract_cmd or os.getenv("TESSERACT_CMD", "")).strip()
    if tcmd:
        pytesseract.pytesseract.tesseract_cmd = tcmd

    # Basic “is tesseract callable” probe (best-effort)
    try:
        _ = pytesseract.get_tesseract_version()
    except Exception as e:
        msg = (
            "Tesseract is not available or not callable.\n"
            "- Ensure Tesseract is installed.\n"
            "- If on Windows, set env TESSERACT_CMD to tesseract.exe full path.\n"
            f"Detail: {e}"
        )
        raise OCRServiceError(msg) from e


def _tesseract_config_str(config: OCRConfig) -> str:
    # Example: "--oem 3 --psm 3"
    return f"--oem {int(config.tesseract_oem)} --psm {int(config.tesseract_psm)}"


def _pdf_to_images(pdf_path: Path, config: OCRConfig) -> List[Image.Image]:
    try:
        from pdf2image import convert_from_path  # type: ignore
    except Exception as e:
        raise OCRServiceError(
            "Missing dependency: pdf2image. Install with: pip install pdf2image"
        ) from e

    poppler_path = (config.poppler_path or os.getenv("POPPLER_PATH", "")).strip() or None

    try:
        # convert_from_path returns PIL Images
        images = convert_from_path(
            str(pdf_path),
            dpi=int(config.pdf_dpi),
            poppler_path=poppler_path,
        )
        if not images:
            raise OCRServiceError("PDF conversion returned 0 pages.")
        return images
    except OCRServiceError:
        raise
    except Exception as e:
        msg = (
            "Failed to convert PDF to images.\n"
            "- If on Windows, install poppler and set env POPPLER_PATH to poppler bin directory.\n"
            "- If on macOS, brew install poppler.\n"
            "- If on Linux, apt-get install poppler-utils.\n"
            f"Detail: {e}"
        )
        raise OCRServiceError(msg) from e


def _ocr_image(img: Image.Image, config: OCRConfig) -> str:
    import pytesseract  # type: ignore

    # Convert to RGB to avoid mode issues
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    txt = pytesseract.image_to_string(
        img,
        lang=config.lang,
        config=_tesseract_config_str(config),
    )
    return txt or ""


def extract_text_from_file(file_path: str, config: Optional[OCRConfig] = None) -> str:
    """
    Single-file OCR:
      - PDF: OCR each page (rasterized) then concatenate
      - Image: OCR once

    Returns:
      plain text (str), may include newlines and artifacts
    """
    cfg = config or OCRConfig()
    _ensure_deps(cfg)

    p = Path(file_path).expanduser().resolve()
    if not p.exists():
        raise OCRServiceError(f"File not found: {p}")

    if _is_pdf(p):
        pages = _pdf_to_images(p, cfg)
        texts: List[str] = []
        for idx, img in enumerate(pages, start=1):
            page_txt = _ocr_image(img, cfg)
            # Keep light separators so later UI can manage it
            texts.append(page_txt)
        return "\n\n".join(texts).strip()

    if _is_image(p):
        try:
            img = Image.open(str(p))
        except Exception as e:
            raise OCRServiceError(f"Failed to open image: {p}. Detail: {e}") from e
        return _ocr_image(img, cfg).strip()

    raise OCRServiceError(
        f"Unsupported file type: {p.suffix}. Supported: PDF or images (.png/.jpg/.jpeg/.webp/.bmp/.tif/.tiff)"
    )


# Optional: quick CLI for local testing
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="PDF or image path")
    ap.add_argument("--lang", default="eng", help="tesseract lang, e.g. eng or eng+chi_sim")
    ap.add_argument("--dpi", type=int, default=300, help="PDF dpi")
    ap.add_argument("--psm", type=int, default=3, help="tesseract psm")
    ap.add_argument("--oem", type=int, default=3, help="tesseract oem")
    args = ap.parse_args()

    cfg = OCRConfig(
        lang=args.lang,
        pdf_dpi=args.dpi,
        tesseract_psm=args.psm,
        tesseract_oem=args.oem,
    )
    text = extract_text_from_file(args.file, cfg)
    print(text)
