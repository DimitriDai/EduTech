import os
import httpx
import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi import UploadFile
from fastapi.responses import FileResponse, Response

app = FastAPI()

# 你当前的端口映射（只改这里就行）
UPSTREAMS = {
    "vocab": os.getenv("VOCAB_URL", "http://127.0.0.1:8000"),
    "speaking": os.getenv("SPEAKING_URL", "http://127.0.0.1:8001"),
    "writing": os.getenv("WRITING_URL", "http://127.0.0.1:8003"),
}

PORTAL_PATH = os.getenv("PORTAL_PATH", "gateway/static/index.html")


async def _proxy_get_json(url: str):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
        # 这里不强制 r.raise_for_status()，让上游的非 200 也能原样返回
        return r.status_code, r.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Upstream unreachable: {e}")
    except ValueError:
        raise HTTPException(status_code=502, detail="Upstream returned non-JSON response")


@app.get("/health")
def health():
    return {"ok": True, "service": "gateway"}


@app.get("/")
def portal():
    # 没有 portal 文件也没关系：避免影响其它路由
    if os.path.exists(PORTAL_PATH):
        return FileResponse(PORTAL_PATH)
    return {"ok": True, "hint": "portal not found", "path": PORTAL_PATH}


# ====== 直连 health（更易排查）======
@app.get("/vocab/health")
async def vocab_health():
    code, data = await _proxy_get_json(f"{UPSTREAMS['vocab'].rstrip('/')}/health")
    return data


@app.get("/speaking/health")
async def speaking_health():
    code, data = await _proxy_get_json(f"{UPSTREAMS['speaking'].rstrip('/')}/health")
    return data


@app.get("/writing/health")
async def writing_health():
    code, data = await _proxy_get_json(f"{UPSTREAMS['writing'].rstrip('/')}/health")
    return data

# ====== 通用代理 ======
# ====== 通用代理（恢复版：适合 JSON/普通请求）======
async def _proxy(request: Request, upstream_base: str, subpath: str) -> Response:
    """
    将 /{service}/{subpath} 代理到 upstream_base/{subpath}
    """
    upstream_url = upstream_base.rstrip("/") + "/" + subpath.lstrip("/")

    if request.url.query:
        upstream_url += "?" + request.url.query

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.setdefault("x-request-id", str(uuid.uuid4()))
    headers.setdefault("x-user-key", "anonymous")
    headers.setdefault("x-plan", "free")
    headers.setdefault("x-paid", "false")

    body = await request.body()

    try:
        timeout = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0)
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            r = await client.request(
                method=request.method,
                url=upstream_url,
                headers=headers,
                content=body,
            )
    except httpx.RequestError as e:
        detail = f"{type(e).__name__}: {repr(e)}"
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {detail}")

    excluded = {"content-encoding", "transfer-encoding", "connection", "keep-alive",
                "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"}
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded}

    return Response(
        content=r.content,
        status_code=r.status_code,
        headers=resp_headers,
        media_type=r.headers.get("content-type"),
    )

async def _proxy_writing_upload(request: Request) -> Response:
    upstream_url = UPSTREAMS["writing"].rstrip("/") + "/api/grade/files"

    headers = dict(request.headers)
    # 这些头让 httpx 自己生成，避免 multipart 出错
    for k in ["host", "content-length", "content-type", "accept-encoding", "connection"]:
        headers.pop(k, None)

    headers.setdefault("x-request-id", str(uuid.uuid4()))
    headers.setdefault("x-user-key", "anonymous")
    headers.setdefault("x-plan", "free")
    headers.setdefault("x-paid", "false")

    form = await request.form()
    items = form.getlist("files")

    if not items:
        return Response(
            content=b'{"detail":"Field \\"files\\" is required"}',
            status_code=422,
            media_type="application/json",
        )

    # 重新组装 multipart：("files", (filename, bytes, content_type))
    files = []
    for it in items:
        if hasattr(it, "filename") and hasattr(it, "read"):
            data = await it.read()
            ct = getattr(it, "content_type", None) or "application/octet-stream"
            files.append(("files", (it.filename, data, ct)))

    try:
        timeout = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0)
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            r = await client.post(upstream_url, headers=headers, files=files)
    except httpx.RequestError as e:
        detail = f"{type(e).__name__}: {repr(e)}"
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {detail}")

    excluded = {"content-encoding", "transfer-encoding", "connection", "keep-alive",
                "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"}
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded}

    return Response(
        content=r.content,
        status_code=r.status_code,
        headers=resp_headers,
        media_type=r.headers.get("content-type"),
    )

# vocab
@app.api_route("/vocab/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_vocab(request: Request, path: str):
    return await _proxy(request, UPSTREAMS["vocab"], path)


# speaking
@app.api_route("/speaking/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_speaking(request: Request, path: str):
    return await _proxy(request, UPSTREAMS["speaking"], path)


@app.api_route("/writing/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_writing(request: Request, path: str):
    # 只拦截批量上传接口
    if request.method == "POST" and path == "api/grade/files":
        return await _proxy_writing_upload(request)

    return await _proxy(request, UPSTREAMS["writing"], path)
