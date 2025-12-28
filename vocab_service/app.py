# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path


import subprocess

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from core.logging_setup import setup_logging
from core.limiter.factory import build_rate_limiter
from api.routes_vocab import router as vocab_router

# 新增
from api.routes_files import router as files_router
from api.routes_export import router as export_router
from api.routes_practice import router as practice_router
from api.routes_grading import router as grading_router
from api.routes_audio import router as audio_router

from dotenv import load_dotenv
load_dotenv()

logger = setup_logging()

app = FastAPI(title="Vocab Service", version="0.2.0")

from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True, "service": "vocab"}

from fastapi.staticfiles import StaticFiles
import os

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

app.mount(
    "/static/audio",
    StaticFiles(directory=os.getenv("AUDIO_ROOT")),
    name="static-audio"
)

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 上线再收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RATE_LIMITER = build_rate_limiter()

PIPELINE_CONCURRENCY = int((__import__("os").getenv("PIPELINE_CONCURRENCY", "3")))
app.state.pipeline_semaphore = asyncio.Semaphore(PIPELINE_CONCURRENCY)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path

    # 只限流核心接口（你也可以改成全站）
    if path.startswith("/v1/vocab/pipeline") or path.startswith("/v1/vocab/match"):
        client_ip = request.client.host if request.client else "unknown"
        res = RATE_LIMITER.allow(client_ip)
        if not res.allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests", "retry_after": res.retry_after},
                headers={"Retry-After": str(res.retry_after)},
            )

    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok", "pipeline_concurrency": PIPELINE_CONCURRENCY}

@app.on_event("startup")
def cleanup_out_on_startup():
    try:
        subprocess.run(
            [
                sys.executable,
                "tools/cleanup_out_runs.py",
                "--keep", "50",
                "--threshold", "50",
                "--min-age-minutes", "60",
            ],
            cwd=str(ROOT),  # 你的 app.py 里 ROOT 已经定义
            check=False,
        )
    except Exception as e:
        logger.warning(f"[cleanup] startup cleanup failed: {e}")


app.include_router(vocab_router, prefix="/v1/vocab")

# 新增
app.include_router(export_router, prefix="/v1/export")
app.include_router(practice_router, prefix="/v1/practice")
app.include_router(grading_router, prefix="/v1/grading")
app.include_router(audio_router, prefix="/v1/audio")
app.include_router(files_router, prefix="/v1/files")