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
    # 1.3 读取 global_cache（用于 display 注入）
    # ===============================
    global_cache_path = ROOT / "storage" / "global_cache.json"
    global_cache = {}
    if global_cache_path.exists():
        with global_cache_path.open("r", encoding="utf-8") as gf:
            global_cache = json.load(gf) or {}

    # ===============================
    # 1.5 生成 word_display（只用于导出，不写回 run_cache）
    # 规则（最终版）：
    # - display 只来自 group.word_display
    # - fallback 到 group.word_norm
    # - entry 不参与 display 决策
    # ===============================
    entries_for_export = []

    for e in entries:
        if not isinstance(e, dict):
            continue

        d = dict(e)  # 浅拷贝，避免污染 run_cache

        word_norm = (d.get("word_norm") or "").strip()
        if not word_norm:
            continue

        group = global_cache.get(word_norm, {})
        if isinstance(group, dict):
            display = (group.get("word_display") or "").strip() or word_norm
        else:
            display = word_norm

        d["word_display"] = display
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