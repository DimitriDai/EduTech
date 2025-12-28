# speaking_service/app.py

# ===========================================================
# 功能：FastAPI 应用主入口
import os
import json
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from fastapi.staticfiles import StaticFiles

from modules.speaking.rename_screenshots import rename_screenshots
from modules.speaking.ocr_stage1 import run_stage1_ocr

from modules.speaking.parser_prefill_txt import standardize_prefill_text
from modules.speaking.parser_std import parse_std_prefill_file
import json

from modules.speaking.run_generate_answers import generate_answers_for_run

from fastapi import File, UploadFile
from typing import List
import shutil

# ---------- 全局配置 ----------
APP_NAME = "IELTS Speaking Service"
APP_VERSION = "0.1.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(BASE_DIR, "runs")
ENABLE_EXPORT_C = os.getenv("ENABLE_EXPORT_C", "1") == "1"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ---------- 基础校验 ----------
def check_env():
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not found in environment variables")
    return api_key


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError("config.json not found")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- FastAPI ----------
app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Independent IELTS Speaking Answer Generation Service"
)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

@app.on_event("startup")
def startup_check():
    # 只做最基础的检查，不跑任何 AI
    check_env()
    os.makedirs(RUNS_DIR, exist_ok=True)


# ---------- Schemas ----------
class HealthResponse(BaseModel):
    status: str
    has_api_key: bool
    runs_dir: str


class RunInitResponse(BaseModel):
    run_id: str
    run_path: str


# ---------- Routes ----------
@app.get("/speaking/health", response_model=HealthResponse)
def health_check():
    api_key = os.getenv("DEEPSEEK_API_KEY")
    return HealthResponse(
        status="ok",
        has_api_key=bool(api_key),
        runs_dir=RUNS_DIR
    )

# ---------- 上传图片接口 ----------
class UploadImagesResponse(BaseModel):
    run_id: str
    saved: int
    files: list[str]

@app.post("/speaking/run/upload_images", response_model=UploadImagesResponse)
async def upload_images(run_id: str, files: List[UploadFile] = File(...)):
    run_dir = os.path.join(RUNS_DIR, run_id)
    img_dir = os.path.join(run_dir, "img")
    if not os.path.isdir(img_dir):
        raise HTTPException(status_code=404, detail=f"run_id not found: {run_id}")

    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    saved_files = []
    for f in files:
        _, ext = os.path.splitext(f.filename.lower())
        if ext not in allowed:
            raise HTTPException(status_code=400, detail=f"Unsupported file: {f.filename}")

        dst = os.path.join(img_dir, f.filename)
        with open(dst, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved_files.append(f.filename)

    return UploadImagesResponse(run_id=run_id, saved=len(saved_files), files=saved_files)

# ---------- 初始化口语任务接口 ----------
@app.post("/speaking/run/init", response_model=RunInitResponse)
def init_run():
    """
    初始化一次口语任务（对应你未来的一整套截图 → 生成 → 导出）
    """
    run_id = uuid.uuid4().hex[:16]
    run_path = os.path.join(RUNS_DIR, run_id)

    os.makedirs(run_path, exist_ok=True)
    os.makedirs(os.path.join(run_path, "img"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "prefill"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "answers"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "cache"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "exports"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "cache_override"), exist_ok=True)

    return RunInitResponse(
        run_id=run_id,
        run_path=run_path
    )
# ---------- 重命名截图接口 ----------
@app.post("/speaking/run/rename")
def rename_run_screenshots(run_id: str):
    """
    对 runs/<run_id>/img 下的截图进行统一重命名
    """
    run_dir = os.path.join(RUNS_DIR, run_id)
    img_dir = os.path.join(run_dir, "img")

    if not os.path.isdir(img_dir):
        raise HTTPException(status_code=404, detail="img directory not found")

    mapping = rename_screenshots(img_dir)

    return {
        "run_id": run_id,
        "renamed": len(mapping),
        "mapping": mapping,
    }

# ---------- OCR 接口接入 ----------

from typing import Optional
from pydantic import BaseModel

from modules.speaking.ocr_stage1 import run_stage1_ocr as stage1_ocr_to_prefill

class OCRRequest(BaseModel):
    run_id: str
    prefix: str = "雅思话题"
    # 可选：你也可以直接传一个外部图片目录（不放进 runs/img）
    image_dir: Optional[str] = None
    # 可选：若你本机 tesseract 需要写死路径
    tesseract_cmd: Optional[str] = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    tessdata_prefix: Optional[str] = r"C:\Program Files\Tesseract-OCR\tessdata"

@app.post("/speaking/stage1/ocr")
def speaking_stage1_ocr(req: OCRRequest):
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="DEEPSEEK_API_KEY missing")

    # run 目录
    run_path = os.path.join(RUNS_DIR, req.run_id)
    if not os.path.isdir(run_path):
        raise HTTPException(status_code=404, detail=f"run_id not found: {req.run_id}")

    img_dir = req.image_dir or os.path.join(run_path, "img")
    out_dir = os.path.join(run_path, "prefill")

    result = stage1_ocr_to_prefill(
        api_key=api_key,
        run_dir=run_path,          # ✅ 新增这一行
        image_dir=img_dir,
        output_dir=out_dir,
        prefix=req.prefix,
        tesseract_cmd=req.tesseract_cmd,
        tessdata_prefix=req.tessdata_prefix,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)

    return result

# ---------- 解析 prefill 接口接入 ---------- 
@app.post("/speaking/stage2/parse")
def speaking_stage2_parse(run_id: str):
    """
    Stage 2: prefill.txt -> structured segments (JSON)
    """
    run_path = os.path.join(RUNS_DIR, run_id)
    prefill_dir = os.path.join(run_path, "prefill")

    if not os.path.isdir(prefill_dir):
        raise HTTPException(status_code=404, detail="prefill directory not found")

    # 找 prefill txt（默认只有一个）
    txt_files = [
        f for f in os.listdir(prefill_dir)
        if f.lower().endswith(".txt")
    ]
    if not txt_files:
        raise HTTPException(status_code=400, detail="no prefill txt found")

    prefill_path = os.path.join(prefill_dir, txt_files[0])

    # ✅ 核心：调用 parser
    result = parse_std_prefill_file(prefill_path)

    # 输出 JSON，供 generator 使用
    out_path = os.path.join(prefill_dir, "parsed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return {
        "run_id": run_id,
        "segments": len(result.get("segments", [])),
        "output": out_path
    }

# ---------- 单题生成接口接入 ----------
class SingleGenerateRequest(BaseModel):
    question_text: str
    prefix: str = "single"
    band: str = "6.5-7"
    role: str = "High School Student (Hangzhou, China)"
    style: str = "Natural & Native"

    # ✅ 新增：缓存控制
    force_regen: bool = False
    only_for_this_run: bool = False

class SingleGenerateResponse(BaseModel):
    run_id: str
    result: dict

import os
from fastapi import HTTPException
from pydantic import BaseModel

class CacheStats(BaseModel):
    cache_dir: str
    cache_file: str
    exists: bool
    file_size_bytes: int
    file_size_mb: float
    unique_keys: int
    max_keys: int
    usage_ratio: float
    near_limit: bool

@app.get("/speaking/cache/stats", response_model=CacheStats)
def speaking_cache_stats():
    cache_dir = os.getenv("SPEAKING_GLOBAL_CACHE_DIR", "").strip()
    if not cache_dir:
        raise HTTPException(status_code=400, detail="SPEAKING_GLOBAL_CACHE_DIR is not set")

    max_keys = int(os.getenv("GLOBAL_CACHE_MAX_KEYS", "20000"))
    cache_file = os.path.join(cache_dir, "answers_cache.jsonl")

    exists = os.path.isfile(cache_file)
    if not exists:
        return CacheStats(
            cache_dir=cache_dir,
            cache_file=cache_file,
            exists=False,
            file_size_bytes=0,
            file_size_mb=0.0,
            unique_keys=0,
            max_keys=max_keys,
            usage_ratio=0.0,
            near_limit=False,
        )

    file_size_bytes = os.path.getsize(cache_file)
    file_size_mb = round(file_size_bytes / (1024 * 1024), 3)

    # 统计 unique keys：按行读 jsonl，最后以 set 计数
    # 注意：这是 O(n) 操作，但只在你手动点 stats 时跑一次，够用。
    import json
    keys = set()
    bad_lines = 0
    with open(cache_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                bad_lines += 1
                continue
            k = obj.get("key")
            if k:
                keys.add(k)

    unique_keys = len(keys)
    usage_ratio = (unique_keys / max_keys) if max_keys > 0 else 0.0
    usage_ratio = round(usage_ratio, 4)

    # 是否接近上限：>= 0.85 你就该注意了（可自行调阈值）
    near_limit = (max_keys > 0 and usage_ratio >= 0.85)

    return CacheStats(
        cache_dir=cache_dir,
        cache_file=cache_file,
        exists=True,
        file_size_bytes=file_size_bytes,
        file_size_mb=file_size_mb,
        unique_keys=unique_keys,
        max_keys=max_keys,
        usage_ratio=usage_ratio,
        near_limit=near_limit,
    )

@app.post("/speaking/single/generate", response_model=SingleGenerateResponse)
def single_generate(req: SingleGenerateRequest):
    # 1) init run（复用你已有的目录结构）
    run_id = uuid.uuid4().hex[:16]
    run_path = os.path.join(RUNS_DIR, run_id)
    os.makedirs(os.path.join(run_path, "img"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "prefill"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "answers"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "cache"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "exports"), exist_ok=True)

    # 2) 写入 STD 文件（让 generate_answers_api 能找到它）
    std_dir = os.path.join(run_path, "prefill")
    std_path = os.path.join(std_dir, f"{req.prefix}_STD.txt")

    text = (req.question_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="question_text is empty")

    # 这里用你已 import 的 normalize_prefill_txt（你现在 app.py 已 import）:contentReference[oaicite:8]{index=8}
    cleaned = normalize_prefill_txt(text)

    with open(std_path, "w", encoding="utf-8") as f:
        f.write(cleaned)

    # 3) 直接复用你现有生成器
    result = generate_answers_for_run(std_path=std_path, runs_dir=RUNS_DIR, run_id=run_id)

    return SingleGenerateResponse(run_id=run_id, result=result)

# ---------- 生成答案接口接入 ----------

@app.post("/speaking/run/generate_answers")
def generate_answers_api(run_id: str, force_regen: bool = False, only_for_this_run: bool = False):
    run_path = os.path.join(RUNS_DIR, run_id)
    std_dir = os.path.join(run_path, "prefill")

    std_files = [f for f in os.listdir(std_dir) if f.endswith("_STD.txt")]
    if not std_files:
        raise HTTPException(status_code=400, detail="No *_STD.txt found for this run")

    std_path = os.path.join(std_dir, std_files[0])

    result = generate_answers_for_run(
        std_path=std_path,
        runs_dir=RUNS_DIR,
        run_id=run_id,
        force_regen=force_regen,
        only_for_this_run=only_for_this_run,
    )
    return result

# ---------- Export DOCX 接口接入 ----------

from pydantic import BaseModel
from typing import Optional, List

from modules.speaking.export_docx import (
    export_version_b_docx,
    export_version_a_docx,
    export_version_c_docx,
    ExportOptions,
    DEFAULT_ROLE,
)

class ExportDocxRequest(BaseModel):
    run_id: str
    merge_all: bool = False
    segments: Optional[List[int]] = None
    band: str = "6.5-7"
    role: str = DEFAULT_ROLE
    style: str = "Natural & Native"

    # ✅ 三个开关 + 分页模式
    show_outline: bool = True
    show_answers: bool = True
    page_break: str = "part"  # "none" / "segment" / "part"

@app.post("/speaking/export/docx")
def speaking_export_docx(req: ExportDocxRequest):
    # 1) run_dir 和你原来的接口保持一致
    runs_dir = os.path.join(os.path.dirname(__file__), "runs")
    run_dir = os.path.join(runs_dir, req.run_id)

    # 2) 核心改动：构造 ExportOptions 对象
    options = ExportOptions(
    show_outline=req.show_outline,
    show_answers=req.show_answers,
    page_break=req.page_break,
)
    # 3) 调用 Version B 导出函数
    export_dir, files = export_version_b_docx(
    run_dir=run_dir,
    band=req.band,
    role=req.role,
    style=req.style,
    merge_all=req.merge_all,
    selected_segments=req.segments,
    options=options,
)
    return {"export_dir": export_dir, "files": files}

@app.post("/speaking/export/docx_a")
def speaking_export_docx_a(req: ExportDocxRequest):
    # 1) run_dir 和你原来的接口保持一致
    run_dir = os.path.join(RUNS_DIR, req.run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(status_code=404, detail=f"run_id not found: {req.run_id}")
    # 2) 核心改动：构造 ExportOptions 对象
    options = ExportOptions(
    show_outline=req.show_outline,
    show_answers=req.show_answers,
    page_break=req.page_break,
)
    # 3) ✅ 唯一核心改动：调用 Version A 导出函数
    export_dir, files = export_version_a_docx(
    run_dir=run_dir,
    band=req.band,
    role=req.role or DEFAULT_ROLE,
    style=req.style,
    merge_all=req.merge_all,
    selected_segments=req.segments,
    options=options,
)

    # 3) 返回结构保持与你 Version B 一致（前端/Swagger 不用变脑子）
    return {
        "ok": True,
        "export_dir": export_dir,
        "files": files,
    }

class FrontendConfig(BaseModel):
    enable_export_c: bool

from fastapi.responses import JSONResponse

@app.get("/speaking/frontend/config")
def frontend_config():
    return JSONResponse(
        content={"enable_export_c": ENABLE_EXPORT_C},
        headers={"Cache-Control": "max-age=300"}
    )

@app.post("/speaking/export/docx_c")
def speaking_export_docx_c(req: ExportDocxRequest):
    run_dir = os.path.join(RUNS_DIR, req.run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(status_code=404, detail=f"run_id not found: {req.run_id}")

    if not ENABLE_EXPORT_C:
        raise HTTPException(status_code=403, detail="Export C is disabled")

    options = ExportOptions(
        show_outline=req.show_outline,
        show_answers=req.show_answers,
        page_break=req.page_break,
    )

    export_dir, files = export_version_c_docx(
        run_dir=run_dir,
        band=req.band,
        role=req.role or DEFAULT_ROLE,
        style=req.style,
        merge_all=req.merge_all,
        selected_segments=req.segments,
        options=options,  # ⚠️ 同上：前提是你 export_version_c_docx 已经加了 options 参数
    )

    return {"ok": True, "export_dir": export_dir, "files": files}

# ---------- 下载导出文件接口 ----------

from fastapi.responses import FileResponse

@app.get("/speaking/run/download")
def download_export(run_id: str, filename: str):
    run_dir = os.path.join(RUNS_DIR, run_id)
    export_dir = os.path.join(run_dir, "exports")
    if not os.path.isdir(export_dir):
        raise HTTPException(status_code=404, detail="exports directory not found")

    # 防路径穿越：只允许文件名，不允许带路径
    if filename != os.path.basename(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = os.path.join(export_dir, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(path, filename=filename)

@app.post("/speaking/run/cache_override/clear")
def clear_run_override(run_id: str):
    run_dir = os.path.join(RUNS_DIR, run_id)
    d = os.path.join(run_dir, "cache_override")
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d, exist_ok=True)
    return {"ok": True, "run_id": run_id}

@app.post("/speaking/cache/global/clear")
def clear_global_cache():
    f = os.path.join(GLOBAL_CACHE_DIR, "answers_cache.jsonl")
    if os.path.isfile(f):
        os.remove(f)
    os.makedirs(GLOBAL_CACHE_DIR, exist_ok=True)
    return {"ok": True}

@app.post("/speaking/cache/global/invalidate_topic")
def invalidate_topic(topic: str):
    cache = JsonlCache(GLOBAL_CACHE_DIR)
    cache.rewrite(lambda k, v: v.get("topic") != topic)
    return {"ok": True, "topic": topic}

