from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import json
from pathlib import Path

from utils.path_utils import build_run_category_output_path

from generators.excel_generator import export_vocab_note_excel
from generators.docx_generator import build_vocab_note_docx

router = APIRouter(tags=["export"])


class ExportVocabNoteRequest(BaseModel):
    run_id: str = Field(..., description="pipeline 返回的 run_id")
    sheet_name: str = "vocab"
    output_xlsx: str = ""
    output_docx: str = ""
    variant: str = "all_fields"


@router.post("/vocab_note")
def export_vocab_note(req: ExportVocabNoteRequest):
    # ===============================
    # 1. 读取 run_cache（pipeline 产物）
    # ===============================
    ROOT = Path(__file__).resolve().parents[1]  # .../vocab_service
    run_cache_path = ROOT / "storage" / "run_cache" / f"{req.run_id}.json"

    if not run_cache_path.exists():
        raise HTTPException(status_code=400, detail=f"run_id not found: {req.run_id}")

    with run_cache_path.open("r", encoding="utf-8") as f:
        run_data = json.load(f)

    entries = run_data.get("entries", [])
    if not entries:
        raise HTTPException(status_code=400, detail="run_cache entries is empty")

    selected_fields = run_data.get("selected_fields") or []
    if not selected_fields:
        raise HTTPException(
            status_code=400,
            detail="run_cache missing selected_fields; please rerun /pipeline to regenerate run_cache"
        )

    # ===============================
    # 1.5 生成 word_display（只用于导出，不写回 run_cache）
    # ===============================
    entries_for_export = []
    for e in entries:
        d = dict(e)  # 浅拷贝，避免影响原始 entries
        word_norm = (d.get("word_norm") or "").strip()
        word_original = (d.get("word_original") or "").strip()

        # 展示规则：优先 word_norm；没有就回退 word_original
        d["word_display"] = word_norm if word_norm else word_original

        entries_for_export.append(d)

    # ===============================
    # 2. 规范输出目录：storage/out/{run_id}/vocab_note/
    # ===============================
    CATEGORY = "vocab_note"

    if not (req.output_xlsx or "").strip():
        req.output_xlsx = str(
            build_run_category_output_path(req.run_id, CATEGORY, f"vocab_note__{req.run_id}.xlsx")
        )

    if not (req.output_docx or "").strip():
        req.output_docx = str(
            build_run_category_output_path(req.run_id, CATEGORY, f"vocab_note__{req.run_id}.docx")
        )

    # routes_export.py
    # 大约在 export_vocab_note 内，生成 Excel 之前

    display_mode = "norm"  # 以后可以前端传

    for e in entries:
        if display_mode == "norm":
            e["word_display"] = e.get("word_norm") or e.get("word_original")
        elif display_mode == "original":
            e["word_display"] = e.get("word_original")
        else:
            e["word_display"] = e.get("word_norm") or e.get("word_original")

    # ===============================
    # 3. 生成 Excel
    # ===============================
    export_vocab_note_excel(
        entries=entries_for_export,
        selected_fields=selected_fields,
        output_path=req.output_xlsx,
        sheet_name=req.sheet_name,
    )

    # ===============================
    # 4. 生成 Docx（直接用 entries_for_export）
    # ===============================
    build_vocab_note_docx(
        entries=entries_for_export,
        output_path=req.output_docx,
        variant=req.variant,
    )

    # ===============================
    # 5. 写 meta（放在 vocab_note 目录内）
    # ===============================
    meta_path = Path(req.output_xlsx).with_suffix(".meta.json")

    meta = {
        "run_id": req.run_id,
        "sheet_name": req.sheet_name,
        "variant": req.variant,
        "selected_fields": selected_fields,
        "output_xlsx": req.output_xlsx,
        "output_docx": req.output_docx,
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "run_id": req.run_id,
        "excel": req.output_xlsx,
        "docx": req.output_docx,
        "variant": req.variant,
        "count": len(entries),
    }