"""
app.py

FastAPI 版本：IELTS 写作批改服务
支持：
1) 单篇：粘贴文本 -> 返回 JSON（含 report_id） -> 下载反馈 docx
2) 批量：上传 txt/docx 多文件 -> Stage 1 返回 batch_id+file_id 列表 -> Stage 2 选择若干 -> 下载 zip（含多份 docx）

运行：
python -m uvicorn app:app --reload
"""

from __future__ import annotations

import os
import io
import time
import zipfile
from uuid import uuid4
from typing import Any, Dict, Optional

from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(BASE_DIR, "Wrt_requirements", "modules")
if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)

from grader import grade_ielts_essay

# save_feedback_to_docx 在你的项目里可能在不同位置，这里做兼容导入
try:
    from Wrt_requirements.modules.formatter_ai import save_feedback_to_docx  # 推荐路径
except Exception:
    from formatter_ai import save_feedback_to_docx  # 兼容：如果你把 formatter_ai.py 放在根目录


# =========================
# 基础配置
# =========================

app = FastAPI(title="IELTS Writing API")

BASE_DIR = os.path.dirname(__file__)
REPORT_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORT_DIR, exist_ok=True)

# 是否在服务器侧保留 docx/zip 文件（True=保留并按数量清理；False=下载后后台删除）
KEEP_REPORT_FILES = True

# 只保留最近 N 份报告（docx）。zip 也会按需删除
MAX_REPORT_FILES = 50
REPORT_PREFIX = "feedback_"

# 单篇缓存（用于 /api/grade/report/{report_id}）
SINGLE_CACHE: Dict[str, Dict[str, Any]] = {}
SINGLE_TTL_SECONDS = 60 * 30  # 30分钟
MAX_SINGLE_REPORTS = 200

# 批量缓存（Stage 1 -> Stage 2）
BATCH_CACHE: Dict[str, Dict[str, Any]] = {}
BATCH_TTL_SECONDS = 60 * 60  # 60分钟
MAX_BATCHES = 30


# =========================
# 工具函数
# =========================

def safe_delete_file(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def cleanup_old_reports(report_dir: str, max_keep: int, prefix: str) -> None:
    """
    只清理 report_dir 中以 prefix 开头的 .docx 文件，避免误删其它内容
    """
    try:
        files = []
        for name in os.listdir(report_dir):
            if not name.startswith(prefix):
                continue
            if not name.lower().endswith(".docx"):
                continue
            full = os.path.join(report_dir, name)
            try:
                files.append((os.path.getmtime(full), full))
            except Exception:
                continue

        if len(files) <= max_keep:
            return

        files.sort(key=lambda x: x[0])  # 最旧在前
        for _, fp in files[: len(files) - max_keep]:
            safe_delete_file(fp)
    except Exception:
        pass


def cleanup_single_cache() -> None:
    now = time.time()

    expired = [rid for rid, v in SINGLE_CACHE.items() if now - v.get("created_at", now) > SINGLE_TTL_SECONDS]
    for rid in expired:
        SINGLE_CACHE.pop(rid, None)

    if len(SINGLE_CACHE) > MAX_SINGLE_REPORTS:
        order = sorted(SINGLE_CACHE.items(), key=lambda kv: kv[1].get("created_at", 0))
        for rid, _ in order[: len(SINGLE_CACHE) - MAX_SINGLE_REPORTS]:
            SINGLE_CACHE.pop(rid, None)


def cleanup_batches() -> None:
    now = time.time()

    expired = [bid for bid, v in BATCH_CACHE.items() if now - v.get("created_at", now) > BATCH_TTL_SECONDS]
    for bid in expired:
        BATCH_CACHE.pop(bid, None)

    if len(BATCH_CACHE) > MAX_BATCHES:
        order = sorted(BATCH_CACHE.items(), key=lambda kv: kv[1].get("created_at", 0))
        for bid, _ in order[: len(BATCH_CACHE) - MAX_BATCHES]:
            BATCH_CACHE.pop(bid, None)


def make_batch_id() -> str:
    return time.strftime("BATCH_%Y%m%d_%H%M%S")


def read_txt_bytes(data: bytes) -> str:
    # 常见编码兜底
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    # 最后兜底：忽略错误
    return data.decode("utf-8", errors="ignore")


def extract_text_from_upload(file: UploadFile) -> tuple[Optional[str], Optional[str]]:
    """
    返回 (text, error)
    支持：.txt / .docx
    不支持：.doc（提示即可）
    """
    name = (file.filename or "").strip()
    ext = os.path.splitext(name.lower())[1]

    raw = file.file.read()

    if ext == ".txt":
        return read_txt_bytes(raw).strip(), None

    if ext == ".doc":
        return None, "暂不支持 .doc（请另存为 .docx 或导出为 .txt）"

    if ext == ".docx":
        try:
            from docx import Document  # python-docx
            doc = Document(io.BytesIO(raw))
            parts = []
            for p in doc.paragraphs:
                t = (p.text or "").strip()
                if t:
                    parts.append(t)
            return "\n".join(parts).strip(), None
        except Exception as e:
            return None, f"读取 docx 失败：{e}"

    return None, "仅支持 .txt 或 .docx（.doc 暂不支持）"


def pick_dim_scores(band: dict):
    """
    兼容多种 band_scores 结构：
    1) {"Task Response": {"score": 6}, ...}
    2) {"Task Response": 6, ...}
    3) {"TR": 6, "CC": 6, "LR": 6, "GRA": 6, "overall": 6}
    """
    if not isinstance(band, dict):
        return None, None, None, None, None

    def as_score(v):
        if isinstance(v, dict) and isinstance(v.get("score"), (int, float)):
            return float(v["score"])
        if isinstance(v, (int, float)):
            return float(v)
        return None

    def pick(*names):
        for n in names:
            if n in band:
                sc = as_score(band.get(n))
                if sc is not None:
                    return sc
        return None

    # 兼容各种 key
    TR = pick("Task Response", "Task Achievement", "TR")
    CC = pick("Coherence and Cohesion", "CC")
    LR = pick("Lexical Resource", "LR")
    GRA = pick("Grammatical Range & Accuracy", "Grammatical Range and Accuracy", "GRA")

    # overall 优先取现成的
    overall = pick("overall", "Overall", "OVERALL")

    # 没给 overall 就按四项均值算（0.5 档）
    if overall is None:
        parts = [x for x in (TR, CC, LR, GRA) if isinstance(x, (int, float))]
        overall = round((sum(parts) / len(parts)) * 2) / 2 if parts else None

    return TR, CC, LR, GRA, overall

# =========================
# Pydantic models
# =========================

class Essay(BaseModel):
    text: str
    task_type: str | None = None


class EssayTextRequest(BaseModel):
    text: str
    task_type: str | None = None  # "Task 1" / "Task 2" / None(自动)


class ExportSelectedRequest(BaseModel):
    batch_id: str
    file_ids: list[str]


# =========================
# 路由：单篇
# =========================

@app.post("/api/grade/text")
def grade_text(req: EssayTextRequest):
    cleanup_single_cache()

    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": "text 为空，请粘贴作文内容。"}, status_code=400)

    full = grade_ielts_essay(text, req.task_type)

    # 主裁：ai_band_scores；grader.py 也会提供 band_scores 兼容字段
    band = (full.get("ai_band_scores") or full.get("band_scores") or {}) if isinstance(full, dict) else {}

    basic = full.get("basic", {}) or {}
    TR, CC, LR, GRA, overall = pick_dim_scores(band)

    report_id = uuid4().hex

    SINGLE_CACHE[report_id] = {
        "created_at": time.time(),
        "text": text,
        "task_type": full.get("task_type"),
        "band_scores": band,
        "feedback_text": full.get("feedback_text"),
        "filename_hint": "single_paste",
    }

    return JSONResponse({
        "report_id": report_id,
        "task_type": full.get("task_type"),
        "overall": overall,
        "TR": TR,
        "CC": CC,
        "LR": LR,
        "GRA": GRA,
        "word_count": basic.get("word_count"),
        "paragraph_count": basic.get("paragraph_count"),
        "feedback_text": full.get("feedback_text", ""),
    })


@app.get("/api/grade/report/{report_id}")
def download_single_report(report_id: str, background_tasks: BackgroundTasks):
    cleanup_single_cache()

    payload = SINGLE_CACHE.get(report_id)
    if not payload:
        return JSONResponse({"error": "report_id 不存在或已过期，请重新批改。"}, status_code=400)

    out_docx = os.path.join(REPORT_DIR, f"{REPORT_PREFIX}{uuid4().hex}.docx")

    save_feedback_to_docx(
        feedback_text=payload.get("feedback_text") or "",
        output_path=out_docx,
        task_type=payload.get("task_type"),
        band_scores=payload.get("band_scores"),
        original_text=payload.get("text") or "",
    )

    download_name = f"feedback_single_{report_id}.docx"

    if not KEEP_REPORT_FILES:
        background_tasks.add_task(safe_delete_file, out_docx)
    else:
        cleanup_old_reports(REPORT_DIR, MAX_REPORT_FILES, REPORT_PREFIX)

    return FileResponse(
        out_docx,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=download_name,
    )


# 可选：旧接口（直接返回 docx），保留但内部走同一条链路
@app.post("/api/grade/report")
def grade_report_api(req: Essay, background_tasks: BackgroundTasks):
    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": "text 为空，请粘贴作文内容。"}, status_code=400)

    full = grade_ielts_essay(text, req.task_type)
    band = (full.get("ai_band_scores") or full.get("band_scores") or {}) if isinstance(full, dict) else {}

    output_path = os.path.join(REPORT_DIR, f"{REPORT_PREFIX}{uuid4().hex}.docx")

    save_feedback_to_docx(
        feedback_text=full.get("feedback_text") or "",
        output_path=output_path,
        task_type=full.get("task_type"),
        band_scores=band,
        original_text=text,
    )

    if not KEEP_REPORT_FILES:
        background_tasks.add_task(safe_delete_file, output_path)
    else:
        cleanup_old_reports(REPORT_DIR, MAX_REPORT_FILES, REPORT_PREFIX)

    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="IELTS_Writing_Feedback.docx",
    )


# =========================
# 路由：批量（Stage 1 / Stage 2）
# =========================

@app.post("/api/grade/files")
async def grade_files(files: list[UploadFile] = File(...), task_type: str | None = None):
    """
    Stage 1：批量上传 -> 批改 -> 返回列表（含 batch_id + file_id）
    备注：会把每篇生成报告所需数据缓存起来，供 Stage 2 导出使用（不重复调用 DeepSeek）
    """
    cleanup_batches()

    batch_id = make_batch_id()
    batch: Dict[str, Any] = {"created_at": time.time(), "items": {}}
    results = []

    for f in files:
        file_id = uuid4().hex
        filename = f.filename or f"file_{file_id}"
        try:
            text, err = extract_text_from_upload(f)
        except Exception as e:
            text, err = None, f"读取文件失败：{e}"

        if err or not text:
            results.append({
                "file_id": file_id,
                "filename": filename,
                "ok": False,
                "error": err or "文件内容为空",
            })
            continue

        try:
            full = grade_ielts_essay(text, task_type)
            band = (full.get("ai_band_scores") or full.get("band_scores") or {}) if isinstance(full, dict) else {}
            basic = full.get("basic", {}) or {}
            TR, CC, LR, GRA, overall = pick_dim_scores(band)

            batch["items"][file_id] = {
                "filename": filename,
                "text": text,
                "task_type": full.get("task_type"),
                "band_scores": band,
                "feedback_text": full.get("feedback_text"),
            }

            results.append({
                "file_id": file_id,
                "filename": filename,
                "ok": True,
                "task_type": full.get("task_type"),
                "overall": overall,
                "TR": TR,
                "CC": CC,
                "LR": LR,
                "GRA": GRA,
                "word_count": basic.get("word_count"),
                "paragraph_count": basic.get("paragraph_count"),
            })

        except Exception as e:
            results.append({
                "file_id": file_id,
                "filename": filename,
                "ok": False,
                "error": f"批改失败：{e}",
            })

    BATCH_CACHE[batch_id] = batch

    return JSONResponse({"batch_id": batch_id, "results": results})


@app.post("/api/grade/report/selected")
def export_selected(req: ExportSelectedRequest, background_tasks: BackgroundTasks):
    """
    Stage 2：给 batch_id + 若干 file_id -> 生成所选 Word -> 打包 zip -> 下载
    不重复调用 DeepSeek（直接用 Stage 1 缓存的数据）
    """
    cleanup_batches()

    batch = BATCH_CACHE.get(req.batch_id)
    if not batch:
        return JSONResponse({"error": "batch_id 不存在或已过期，请重新上传批改。"}, status_code=400)

    items: Dict[str, Any] = batch.get("items", {}) or {}
    selected = [fid for fid in req.file_ids if fid in items]
    if not selected:
        return JSONResponse({"error": "file_ids 为空或不匹配当前 batch。"}, status_code=400)

    zip_filename = f"{req.batch_id}_Reports_Selected.zip"
    zip_path = os.path.join(REPORT_DIR, f"{req.batch_id}_{uuid4().hex}.zip")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fid in selected:
            payload = items[fid]
            original_name = payload["filename"]
            base = os.path.splitext(os.path.basename(original_name))[0]

            out_docx = os.path.join(REPORT_DIR, f"{REPORT_PREFIX}{uuid4().hex}.docx")

            save_feedback_to_docx(
                feedback_text=payload.get("feedback_text") or "",
                output_path=out_docx,
                task_type=payload.get("task_type"),
                band_scores=payload.get("band_scores"),
                original_text=payload.get("text") or "",
            )

            arcname = f"feedback_{base}_{req.batch_id}.docx"
            zf.write(out_docx, arcname=arcname)

            # docx 是临时文件：写入 zip 后即可删（避免 reports 爆炸）
            safe_delete_file(out_docx)

    if not KEEP_REPORT_FILES:
        background_tasks.add_task(safe_delete_file, zip_path)
    else:
        cleanup_old_reports(REPORT_DIR, MAX_REPORT_FILES, REPORT_PREFIX)

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=zip_filename,
    )


# =========================
# 静态页面
# =========================

# 你的 batch_grade.html / single_grade.html 在根目录
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
def index():
    return FileResponse("batch_grade.html")

@app.get("/single")
def single_page():
    return FileResponse("single_grade.html")
