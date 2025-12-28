# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from api.schemas import PipelineRequest, PipelineResponse, MatchRequest, MatchResponse
from services.pipeline_service import run_pipeline
from services.match_service import MatchService, CacheStores
from core.logging_setup import setup_logging

logger = setup_logging()
router = APIRouter()


def _stores() -> CacheStores:
    # 保持你原来的相对路径策略（跟你现有 services 一致）
    return CacheStores(
        global_cache_path=os.path.join("storage", "global_cache.json"),
        uploaded_vocab_cache_path=os.path.join("storage", "uploaded_vocab_cache.json"),
    )

@router.post("/pipeline", response_model=PipelineResponse)
async def vocab_pipeline(req: PipelineRequest, request: Request):
    import os
    import json
    import uuid
    from datetime import datetime
    from pathlib import Path

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

        # ===== 探针：计算路径 =====
        cwd = os.getcwd()
        this_file = Path(__file__).resolve()
        ROOT = this_file.parents[1]            # 期望：.../vocab_service
        run_dir = ROOT / "storage" / "run_cache"
        run_dir.mkdir(parents=True, exist_ok=True)

        run_id = getattr(out, "run_id", None)
        if not run_id:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
            setattr(out, "run_id", run_id)

        run_cache_path = run_dir / f"{run_id}.json"

        # ===== 写入 run_cache =====
        entries_payload = []
        for e in out.enriched_entries:
            entries_payload.append(e.to_dict() if hasattr(e, "to_dict") else getattr(e, "__dict__", {}))

        run_data = {
            "run_id": run_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
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

        # ===== 再写一个探针文件，方便你在资源管理器里确认目录 =====
        probe_path = run_dir / "_PROBE.txt"
        probe_path.write_text(
            f"OK\ncwd={cwd}\nthis_file={this_file}\nROOT={ROOT}\n",
            encoding="utf-8"
        )

        # ===== 验证写入结果 =====
        exists_json = run_cache_path.exists()
        exists_probe = probe_path.exists()

        logger.info(f"[{rid}] run_cache_write path={run_cache_path.as_posix()} exists={exists_json}")
        logger.info(f"[{rid}] probe_write path={probe_path.as_posix()} exists={exists_probe}")

        # ===== 正常返回 =====
        resp = PipelineResponse.from_pipeline_output(out)

        # ⚠️ 临时：把探针信息塞进响应（不影响你后面逻辑）
        # 如果你的 PipelineResponse 不允许动态字段，这里会报错；那就只看日志。
        try:
            resp_dict = resp.model_dump()
            resp_dict["_debug"] = {
                "cwd": cwd,
                "this_file": str(this_file),
                "ROOT": str(ROOT),
                "run_cache_path": str(run_cache_path),
                "probe_path": str(probe_path),
                "exists_json": exists_json,
                "exists_probe": exists_probe,
            }
            return resp_dict
        except Exception:
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