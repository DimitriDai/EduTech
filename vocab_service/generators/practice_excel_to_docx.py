# generators/practice_excel_to_docx.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import load_workbook
from docx import Document
from docx.enum.section import WD_ORIENTATION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.table import _Cell, Table

from core import field_definitions as fd


# =========================
# 固定样式（原样复制自 docx_generator.py）
# =========================
FONT_NAME = "等线"
FONT_SIZE = 11
TITLE_SIZE = 16

ROW_HEIGHT_HEADER_CM = 1.0
ROW_HEIGHT_BODY_CM = 2.0

# A4 边距（显式写死，避免 Word 默认边距导致“看起来列宽不对”）
MARGIN_LEFT_CM = 1.7
MARGIN_RIGHT_CM = 1.7
MARGIN_TOP_CM = 1.8
MARGIN_BOTTOM_CM = 1.8


# =========================
# docx 样式/固定列宽（关键：原样复制自 docx_generator.py）
# =========================
def _set_run_font(run, size_pt: int = FONT_SIZE, bold: Optional[bool] = None):
    run.font.name = FONT_NAME
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    run.font.size = Pt(size_pt)
    if bold is not None:
        run.bold = bold

def _set_cell_text(cell: _Cell, text: str, size_pt: int = FONT_SIZE, bold: Optional[bool] = None):
    cell.text = "" if text is None else str(text)
    for p in cell.paragraphs:
        if not p.runs:
            r = p.add_run("")
            _set_run_font(r, size_pt=size_pt, bold=bold)
        for r in p.runs:
            _set_run_font(r, size_pt=size_pt, bold=bold)

def _set_row_height(row, cm: float):
    tr = row._tr
    trPr = tr.get_or_add_trPr()
    trHeight = trPr.find(qn("w:trHeight"))
    if trHeight is None:
        trHeight = OxmlElement("w:trHeight")
        trPr.append(trHeight)
    trHeight.set(qn("w:val"), str(int(Cm(cm).twips)))
    trHeight.set(qn("w:hRule"), "exact")

def _set_document_base_style(doc: Document):
    styles = doc.styles

    # Normal
    normal = styles["Normal"]
    normal.font.name = FONT_NAME
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    normal.font.size = Pt(FONT_SIZE)

    # Heading 2（用于标题）
    if "Heading 2" in styles:
        h2 = styles["Heading 2"]
        h2.font.name = FONT_NAME
        h2._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
        h2.font.size = Pt(TITLE_SIZE)
        h2.font.bold = True

def _apply_page_setup(doc: Document, total_width_cm: float):
    """
    ✅ 练习版规则（按你最新口径锁死）：
    - 总列宽 > 19.5 -> 横版
    - 总列宽 <= 19.5 -> 竖版
    """
    section = doc.sections[0]
    section.left_margin = Cm(MARGIN_LEFT_CM)
    section.right_margin = Cm(MARGIN_RIGHT_CM)
    section.top_margin = Cm(MARGIN_TOP_CM)
    section.bottom_margin = Cm(MARGIN_BOTTOM_CM)

    if total_width_cm > 19.5:
        section.orientation = WD_ORIENTATION.LANDSCAPE
        new_width, new_height = section.page_height, section.page_width
        section.page_width = new_width
        section.page_height = new_height
    else:
        section.orientation = WD_ORIENTATION.PORTRAIT

def _set_table_fixed_layout(table: Table):
    table.style = "Table Grid"
    table.autofit = False

    tbl = table._tbl
    tblPr = tbl.tblPr

    # tblLayout fixed
    tblLayout = tblPr.find(qn("w:tblLayout"))
    if tblLayout is None:
        tblLayout = OxmlElement("w:tblLayout")
        tblPr.append(tblLayout)
    tblLayout.set(qn("w:type"), "fixed")

def _set_tbl_grid(table: Table, col_widths_cm: List[float]):
    """
    Word 最吃这一段：tblGrid
    """
    tbl = table._tbl
    grid = tbl.tblGrid
    if grid is None:
        grid = OxmlElement("w:tblGrid")
        tbl.insert(0, grid)
    else:
        for child in list(grid):
            grid.remove(child)

    for w_cm in col_widths_cm:
        gridCol = OxmlElement("w:gridCol")
        gridCol.set(qn("w:w"), str(int(Cm(w_cm).twips)))
        grid.append(gridCol)

def _set_cell_width(cell: _Cell, w_cm: float):
    """
    写 tcW，防止 Word 对单元格再次“自适应”
    """
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcW = tcPr.find(qn("w:tcW"))
    if tcW is None:
        tcW = OxmlElement("w:tcW")
        tcPr.append(tcW)
    tcW.set(qn("w:type"), "dxa")
    tcW.set(qn("w:w"), str(int(Cm(w_cm).twips)))

def _apply_column_widths(table: Table, col_widths_cm: List[float]):
    _set_table_fixed_layout(table)
    _set_tbl_grid(table, col_widths_cm)

    # columns width + each cell tcW 双保险
    for i, w_cm in enumerate(col_widths_cm):
        table.columns[i].width = Cm(w_cm)

    for row in table.rows:
        for i, w_cm in enumerate(col_widths_cm):
            _set_cell_width(row.cells[i], w_cm)


# =========================
# practice：只做“视图 + 留空列”
# =========================
def _stamp(ts: str, run_id: str) -> str:
    ts2 = ts.strip()
    rid = run_id.strip()
    if rid:
        return f"{ts2}_{rid}"
    return ts2

def _get_header_keys(ws) -> List[str]:
    header = [c.value for c in ws[1]]
    out: List[str] = []
    for h in header:
        if h is None:
            out.append("")
        else:
            out.append(str(h).strip())
    return out

def _label_of(key: str) -> str:
    # 表头显示 label（来自 field_definitions）
    if key in fd.FIELD_DEFS:
        return fd.FIELD_DEFS[key].label
    return key

def _sheet_has_required_keys(ws, col_defs: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    header_keys = _get_header_keys(ws)
    missing: List[str] = []
    for c in col_defs:
        if c.get("blank", False):
            continue
        k = c["key"]
        if k not in header_keys:
            missing.append(k)
    return (len(missing) == 0, missing)

def _build_one_docx_from_workbook(
    wb,
    output_path: str,
    doc_title: str,
    col_defs: List[Dict[str, Any]],
    col_widths_cm: List[float],
    body_row_height_cm: float,
):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    doc = Document()
    _set_document_base_style(doc)

    # 标题（可删：如果你希望练习 docx 没有标题，就把这一段注释掉）
    p = doc.add_heading(doc_title, level=2)
    for r in p.runs:
        _set_run_font(r, size_pt=TITLE_SIZE, bold=True)

    total_w = sum(col_widths_cm)
    _apply_page_setup(doc, total_w)

    # 每个 sheet -> 一张表
    for sheet in wb.sheetnames:
        ws = wb[sheet]

        ok, missing = _sheet_has_required_keys(ws, col_defs)
        if not ok:
            # 如果某 sheet 缺字段：直接报错（更符合“契约一致”）
            raise ValueError(f"[practice_docx] sheet={sheet} missing columns: {missing}")

        header_keys = _get_header_keys(ws)
        idx: Dict[str, int] = {}
        for c in col_defs:
            if c.get("blank", False):
                continue
            k = c["key"]
            idx[k] = header_keys.index(k)

        table = doc.add_table(rows=1, cols=len(col_defs))
        _apply_column_widths(table, col_widths_cm)

        # header row
        hdr = table.rows[0].cells
        for j, c in enumerate(col_defs):
            k = c["key"]
            w_cm = col_widths_cm[j]
            _set_cell_width(hdr[j], w_cm)
            _set_cell_text(hdr[j], _label_of(k), size_pt=FONT_SIZE, bold=True)
        _set_row_height(table.rows[0], ROW_HEIGHT_HEADER_CM)

        # body rows
        for row_no, r in enumerate(ws.iter_rows(min_row=2, values_only=True), start=1):
            row = table.add_row()
            cells = row.cells
            for j, c in enumerate(col_defs):
                k = c["key"]

                if k == "no":
                    text = str(row_no)   # ✅ 永远按顺序生成 1,2,3…

                elif c.get("blank"):
                    text = ""            # ✅ 学生填写列留空（表头以下）

                else:
                    v = r[idx[k]]
                    text = "" if v is None else str(v)

                _set_cell_text(cells[j], text, size_pt=FONT_SIZE, bold=False)
                _set_cell_width(cells[j], col_widths_cm[j])
            _set_row_height(row, cm=body_row_height_cm)

        doc.add_page_break()

    doc.save(output_path)
    print(f"[DONE] {output_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_xlsx", required=True, help="shuffle 后的 master xlsx（表头为 key）")
    parser.add_argument("--output_dir", required=True, help="输出目录")
    parser.add_argument("--run_id", default="", help="可选：run_id（例如 run01）")
    parser.add_argument("--timestamp", default="", help="可选：timestamp（默认当前时间）")
    args = parser.parse_args()

    ts = args.timestamp.strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = _stamp(ts, args.run_id)

    in_xlsx = args.input_xlsx
    out_dir = args.output_dir
    os.makedirs(out_dir, exist_ok=True)

    wb = load_workbook(in_xlsx, data_only=True)

    # =========================
    # 4 类练习（学生版）
    # 只允许你调整：col_widths_cm 和 body_row_height_cm
    # =========================

    # 单词 英译中：给英文单词；中文解释留空让学生写
    spec_word_e2c = {
        "title": "单词 英译中（学生版）",
        "filename": f"练习_单词_英译中_{suffix}.docx",
        "col_defs": [
            {"key": "no", "blank": False},
            {"key": "word_original", "blank": False},
            {"key": "pos_cn", "blank": True},   # ✅ 留空
        ],
        "col_widths_cm": [1.2, 9.0, 9.0],      # 你可自行调整
        "body_row_height_cm": 1.0,             # 按你要求
    }

    # 单词 中译英：给中文解释；英文单词留空让学生写
    spec_word_c2e = {
        "title": "单词 中译英（学生版）",
        "filename": f"练习_单词_中译英_{suffix}.docx",
        "col_defs": [
            {"key": "no", "blank": False},
            {"key": "pos_cn", "blank": False},
            {"key": "word_original", "blank": True},  # ✅ 留空
        ],
        "col_widths_cm": [1.2, 9.0, 9.0],
        "body_row_height_cm": 1.0,
    }

    # 例句 英译中：给英文例句；例句翻译留空让学生写
    spec_sent_e2c = {
        "title": "例句 英译中（学生版）",
        "filename": f"练习_例句_英译中_{suffix}.docx",
        "col_defs": [
            {"key": "no", "blank": False},
            {"key": "example", "blank": False},
            {"key": "example_cn", "blank": True},  # ✅ 留空
        ],
        "col_widths_cm": [1.2, 9.0, 9.0],
        "body_row_height_cm": 2.0,  # 按你要求
    }

    # 例句 中译英：给例句翻译；英文例句留空让学生写
    spec_sent_c2e = {
        "title": "例句 中译英（学生版）",
        "filename": f"练习_例句_中译英_{suffix}.docx",
        "col_defs": [
            {"key": "no", "blank": False},
            {"key": "example_cn", "blank": False},
            {"key": "example", "blank": True},  # ✅ 留空
        ],
        "col_widths_cm": [1.2, 9.0, 9.0],
        "body_row_height_cm": 2.0,
    }

    specs = [spec_word_e2c, spec_word_c2e, spec_sent_e2c, spec_sent_c2e]

    # 如果某类练习需要的字段在 Excel 根本不存在：跳过并提示（符合你“前端不展示缺失选项”的方向）
    # 判断方式：只看第一个 sheet 的 header 是否具备 required key
    first_ws = wb[wb.sheetnames[0]]
    header_keys = set(_get_header_keys(first_ws))

    for sp in specs:
        required = [c["key"] for c in sp["col_defs"] if not c.get("blank", False)]
        missing_req = [k for k in required if k not in header_keys]
        if missing_req:
            print(f"[SKIP] {sp['filename']} missing required columns in excel: {missing_req}")
            continue

        out_path = os.path.join(out_dir, sp["filename"])
        _build_one_docx_from_workbook(
            wb=wb,
            output_path=out_path,
            doc_title=sp["title"],
            col_defs=sp["col_defs"],
            col_widths_cm=sp["col_widths_cm"],
            body_row_height_cm=sp["body_row_height_cm"],
        )

    print(f"[DONE] wrote practice docx to: {os.path.abspath(out_dir)}")


if __name__ == "__main__":
    main()