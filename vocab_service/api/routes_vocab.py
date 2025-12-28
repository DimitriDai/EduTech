# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import uuid

from datetime import datetime, timezone

from pydantic import BaseModel
from typing import List
from fastapi import HTTPException

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from api.schemas import PipelineRequest, PipelineResponse, MatchRequest, MatchResponse
from services.pipeline_service import run_pipeline
from services.match_service import MatchService, CacheStores
from core.logging_setup import setup_logging

logger = setup_logging()
router = APIRouter()

VOCAB_DEBUG = os.getenv("VOCAB_DEBUG", "0") == "1"

def _stores() -> CacheStores:
    # 保持你原来的相对路径策略（跟你现有 services 一致）
    return CacheStores(
        global_cache_path=os.path.join("storage", "global_cache.json"),
        uploaded_vocab_cache_path=os.path.join("storage", "uploaded_vocab_cache.json"),
    )

class TagsRequest(BaseModel):
    tags: List[str]

@router.post("/run/{run_id}/tags")
def add_tags(run_id: str, req: TagsRequest, request: Request):
    """
    给某次 vocab run 增加 tags，并记录事件（events.jsonl）
    """
    run_track_dir = os.path.join("storage", "run_cache_vocab", run_id)
    meta_path = os.path.join(run_track_dir, "meta.json")
    events_path = os.path.join(run_track_dir, "events.jsonl")

    if not os.path.exists(meta_path):
        raise HTTPException(status_code=404, detail=f"run_id not found: {run_id}")

    # 1) 读 meta
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # 2) 合并 tags（去重、保序）
    old = meta.get("tags", []) or []
    new = req.tags or []
    merged = list(dict.fromkeys(old + new))
    meta["tags"] = merged

    # 3) 写回 meta
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 4) 追加事件
    ts = datetime.now(timezone.utc).isoformat()
    request_id = getattr(request.state, "request_id", "") if request else ""
    evt = {
        "ts": ts,
        "type": "tags_added",
        "run_id": run_id,
        "request_id": request_id,
        "tags": new,
    }
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(evt, ensure_ascii=False) + "\n")

    return {"ok": True, "run_id": run_id, "tags": merged}

@router.post("/pipeline", response_model=PipelineResponse)
async def vocab_pipeline(req: PipelineRequest, request: Request):
    rid = getattr(request.state, "request_id", "no-rid")
    cache_mode = req.resolved_cache_mode() if hasattr(req, "resolved_cache_mode") else (req.cache_mode or "USE_CACHE")

    logger.info(f"[{rid}] /pipeline count={req.count} cache_mode={cache_mode} fields={len(req.selected_fields)}")

    try:
        sem = getattr(request.app.state, "pipeline_semaphore", None)

        if sem is None:
            out = run_pipeline(
                passage_text=req.passage_text or "",
                scattered_text=req.scattered_text or "",
                count=req.count,
                selected_fields=req.selected_fields,
                cache_mode=cache_mode,
                stores=_stores(),
            )
        else:
            async with sem:
                out = run_pipeline(
                    passage_text=req.passage_text or "",
                    scattered_text=req.scattered_text or "",
                    count=req.count,
                    selected_fields=req.selected_fields,
                    cache_mode=cache_mode,
                    stores=_stores(),
                )

        # ---------- ensure run_id ----------
        run_id = getattr(out, "run_id", None)
        if not run_id:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
            setattr(out, "run_id", run_id)

        # ---------- run tracking: meta + events ----------
        run_track_dir = os.path.join("storage", "run_cache_vocab", run_id)
        os.makedirs(run_track_dir, exist_ok=True)

        def _iso_now():
            return datetime.now(timezone.utc).isoformat()

        h = request.headers
        meta = {
            "run_id": run_id,
            "module": "vocab",
            "created_at": _iso_now(),
            "source": "gateway",
            "request_id": getattr(request.state, "request_id", ""),
            "user_key": h.get("x-user-key", "anonymous"),
            "tags": [],
            "billing": {
                "plan": h.get("x-plan", "free"),
                "paid": h.get("x-paid", "false").lower() == "true",
                "order_id": None
            }
        }

        with open(os.path.join(run_track_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        with open(os.path.join(run_track_dir, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"ts": meta["created_at"], "type": "run_created", "run_id": run_id, "request_id": meta["request_id"]},
                ensure_ascii=False
            ) + "\n")
        # ----------------------------------------------


        # ===== 探针：计算路径 =====
        cwd = os.getcwd()
        this_file = Path(__file__).resolve()
        ROOT = this_file.parents[1]            # 期望：.../vocab_service
        run_cache_dir = ROOT / "storage" / "run_cache"
        run_cache_dir.mkdir(parents=True, exist_ok=True)
        run_cache_path = run_cache_dir / f"{run_id}.json"

        # ===== 写入 run_cache =====
        entries_payload = []
        for e in out.enriched_entries:
            entries_payload.append(e.to_dict() if hasattr(e, "to_dict") else getattr(e, "__dict__", {}))

        run_data = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "words_merged": out.words_merged,
            "entries": entries_payload,
            # ✅ 关键：把“用户这次勾选的字段”落盘，供 export 用
            "selected_fields": req.selected_fields,
            # 如果你还有 preset（可选）
            "field_preset": getattr(req, "field_preset", None),
        }

        run_cache_path.write_text(
            json.dumps(run_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # ===== 再写一个探针文件（仅 debug 模式）=====
        probe_path = None
        if VOCAB_DEBUG:
            probe_path = run_cache_dir / "_PROBE.txt"
            probe_path.write_text(
                f"OK\ncwd={cwd}\nthis_file={this_file}\nROOT={ROOT}\n",
                encoding="utf-8"
            )

        # ===== 验证写入结果 =====
        exists_json = run_cache_path.exists()
        exists_probe = bool(probe_path and probe_path.exists())

        logger.info(f"[{rid}] run_cache_write path={run_cache_path.as_posix()} exists={exists_json}")
        logger.info(f"[{rid}] probe_write path={(probe_path.as_posix() if probe_path else '')} exists={exists_probe}")

        # ===== 正常返回 =====
        resp = PipelineResponse.from_pipeline_output(out)

        # ⚠️ 临时：把探针信息塞进响应（不影响你后面逻辑）
        # 如果你的 PipelineResponse 不允许动态字段，这里会报错；那就只看日志。
        if VOCAB_DEBUG:
            try:
                resp_dict = resp.model_dump()
                resp_dict["_debug"] = {
                    "cwd": cwd,
                    "this_file": str(this_file),
                    "ROOT": str(ROOT),
                    "run_cache_path": str(run_cache_path),
                    "probe_path": str(probe_path) if probe_path else "",
                    "exists_json": exists_json,
                    "exists_probe": exists_probe,
                }
                return resp_dict
            except Exception:
                pass
        return resp

    except ValueError as e:
        logger.info(f"[{rid}] /pipeline bad_request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception(f"[{rid}] /pipeline error")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.post("/match", response_model=MatchResponse)
def vocab_match(req: MatchRequest):
    try:
        svc = MatchService(_stores())
        items = svc.match_words(
            words=req.words,
            selected_fields=req.selected_fields,
            cache_mode=req.cache_mode,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return MatchResponse.from_items(items)