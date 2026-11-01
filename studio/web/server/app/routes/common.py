"""Shared route helpers: error shape, service access, file serving with caching headers."""
from __future__ import annotations

import asyncio
import json

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from ..projects import NotFound, find_version, vkey
from ..util import content_type_for, safe_rel_path

IMMUTABLE = "private, max-age=31536000, immutable"
NO_STORE = "no-store"


def err(status: int, code: str, message: str, **details) -> HTTPException:
    d = {"code": code, "message": message}
    if details:
        d["details"] = details
    return HTTPException(status_code=status, detail=d)


def svc_of(request: Request):
    return request.app.state.svc


async def load_project(request: Request, pid: str, reconcile: bool = True) -> dict:
    svc = svc_of(request)
    try:
        p = await svc.repo.aget(pid)
    except NotFound:
        raise err(404, "not_found", "Project not found.") from None
    if reconcile:
        p = await svc.builds.reconcile(p)
    return p


async def serve_store_file(request: Request, key: str, *, immutable: bool, download: bool = False,
                           filename: str | None = None) -> Response:
    svc = svc_of(request)
    st = await asyncio.to_thread(svc.store.stat, key)
    if st is None:
        raise err(404, "not_found", "File not found.")
    etag = f'"{st.etag}"'
    headers = {"Cache-Control": IMMUTABLE if immutable else NO_STORE, "ETag": etag}
    if download or filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename or key.rsplit("/", 1)[-1]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    ctype = content_type_for(key)
    local = svc.store.local_path(key)
    if local is not None:
        return FileResponse(local, media_type=ctype, headers=headers)
    if st.size <= 8 << 20:
        data = await asyncio.to_thread(svc.store.read_bytes, key)
        return Response(content=data, media_type=ctype, headers=headers)
    headers["Content-Length"] = str(st.size)
    return StreamingResponse(svc.store.iter_chunks(key), media_type=ctype, headers=headers)


async def serve_version_file(request: Request, p: dict, n: int, path: str, download: bool = False) -> Response:
    rel = safe_rel_path(path)
    if rel is None:
        raise err(404, "not_found", "File not found.")
    v = find_version(p, n)
    if v is None:
        raise err(404, "not_found", "Version not found.")
    immutable = v.get("state") == "done" and v.get("finalized") and rel != "status.json"
    return await serve_store_file(request, vkey(p["id"], n, rel), immutable=bool(immutable), download=download)


async def serve_export(request: Request, p: dict, n: int) -> Response:
    v = find_version(p, n)
    if v is None:
        raise err(404, "not_found", "Version not found.")
    spec = await asyncio.to_thread(svc_of(request).store.read_json, vkey(p["id"], n, "spec.json")) or {}
    slug = spec.get("slug") or "gadget"
    return await serve_store_file(request, vkey(p["id"], n, "export.zip"), immutable=v.get("state") == "done",
                                  filename=f"{slug}-v{n}.zip")


CONCEPT_FILES = {"render.png", "photo.png", "photo.jpg", "photo.webp", "preview.glb", "spec.json"}


async def serve_concept_file(request: Request, p: dict, cid: str, name: str) -> Response:
    if name not in CONCEPT_FILES or not any(c["id"] == cid for c in p.get("concepts") or []):
        raise err(404, "not_found", "File not found.")
    return await serve_store_file(request, f"projects/{p['id']}/concepts/{cid}/{name}", immutable=True)


def shared_view(p: dict) -> dict:
    token = (p.get("share") or {}).get("token")
    q = {k: v for k, v in p.items() if k not in ("order_requests", "share", "turn")}
    q["read_only"] = True
    raw = json.dumps(q, ensure_ascii=False).replace(f"/api/projects/{p['id']}/", f"/api/share/{token}/")
    return json.loads(raw)
