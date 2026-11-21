"""Shared route helpers: error shape, service access, file serving with caching headers."""
from __future__ import annotations

import asyncio
import gzip
import json
from collections import OrderedDict

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from ..projects import NotFound, find_version, vkey
from ..util import content_type_for, safe_rel_path

IMMUTABLE = "private, max-age=31536000, immutable"
NO_STORE = "no-store"

# Objects up to this size are read whole and sent with a Content-Length. Larger ones (export.zip is 40-60 MB) are
# streamed with chunked transfer encoding and NO Content-Length: Cloud Run rejects a response that declares more than
# 32 MiB ("Response size was too large", HTTP 500), while a chunked stream has no size cap.
WHOLE_MAX = 8 << 20

# gzip for the text-like and 3D files the viewer pulls (assembly.glb shrinks ~65%, JSON ~90%). Only whole, inline
# responses are compressed: never attachments, never streamed objects, never SSE (this is not a middleware).
GZIP_MIN = 1024
GZIP_TYPES = ("application/json", "model/gltf-binary", "model/gltf+json", "image/svg+xml", "text/")
_GZ_CACHE: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_GZ_CACHE_MAX = 48 << 20


def accepts_encoding(request: Request, coding: str) -> bool:
    """True when the Accept-Encoding header lists `coding` with a non-zero q value."""
    for part in (request.headers.get("accept-encoding") or "").lower().split(","):
        name, _, params = part.strip().partition(";")
        if name.strip() != coding:
            continue
        q = params.strip()
        if q.startswith("q="):
            try:
                return float(q[2:]) > 0
            except ValueError:
                return False
        return True
    return False


def _gzippable(ctype: str, size: int) -> bool:
    return GZIP_MIN <= size <= WHOLE_MAX and ctype.startswith(GZIP_TYPES)


async def _gzip_cached(key: str, etag: str, data: bytes) -> bytes:
    hit = _GZ_CACHE.get((key, etag))
    if hit is not None:
        _GZ_CACHE.move_to_end((key, etag))
        return hit
    out = await asyncio.to_thread(gzip.compress, data, 6)
    _GZ_CACHE[(key, etag)] = out
    total = sum(len(v) for v in _GZ_CACHE.values())
    while total > _GZ_CACHE_MAX and len(_GZ_CACHE) > 1:
        _, old = _GZ_CACHE.popitem(last=False)
        total -= len(old)
    return out


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
    from ..auth import current_session
    from ..guest import ACTING, owns

    gs = current_session(request)
    ACTING.set(gs)                          # builds this request starts are charged by it (guest.build_owner)
    if reconcile:                           # a guest reading a showcase never starts an AI call (as a share visit)
        p = await svc.builds.reconcile(p, auto_sound=not (gs is not None and gs.guest and not owns(gs, p)))
    return p


async def serve_store_file(request: Request, key: str, *, immutable: bool, download: bool = False,
                           filename: str | None = None) -> Response:
    svc = svc_of(request)
    st = await asyncio.to_thread(svc.store.stat, key)
    if st is None:
        raise err(404, "not_found", "File not found.")
    ctype = content_type_for(key)
    attach = bool(download or filename)
    varies = not attach and _gzippable(ctype, st.size)
    gz = varies and accepts_encoding(request, "gzip")
    etag = f'"{st.etag}-gz"' if gz else f'"{st.etag}"'
    headers = {"Cache-Control": IMMUTABLE if immutable else NO_STORE, "ETag": etag}
    if varies:
        headers["Vary"] = "Accept-Encoding"
    if attach:
        headers["Content-Disposition"] = f'attachment; filename="{filename or key.rsplit("/", 1)[-1]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    local = svc.store.local_path(key)
    if local is not None and not gz:
        return FileResponse(local, media_type=ctype, headers=headers)
    if st.size <= WHOLE_MAX:
        data = await asyncio.to_thread(svc.store.read_bytes, key)
        if data is None:
            raise err(404, "not_found", "File not found.")
        if gz:
            data = await _gzip_cached(key, st.etag, data)
            headers["Content-Encoding"] = "gzip"
        return Response(content=data, media_type=ctype, headers=headers)
    if request.method == "HEAD":  # no body travels, so the size can be declared
        headers["Content-Length"] = str(st.size)
        return Response(status_code=200, media_type=ctype, headers=headers)
    # No Content-Length on purpose (see WHOLE_MAX): uvicorn then uses chunked transfer encoding.
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
    # "uploads" (every image ever uploaded, sent or not, with file names) never goes out: the SPA builds image URLs
    # from the messages' attachments, and the share route serves only those
    q = {k: v for k, v in p.items() if k not in ("order_requests", "share", "turn", "device", "uploads", "owner")}
    if q.get("share_uploads") is not True:      # the owner's images stay private unless shared (app/uploads.py)
        from ..uploads import hide_uploads

        q = hide_uploads(q)
    q["read_only"] = True
    raw = json.dumps(q, ensure_ascii=False).replace(f"/api/projects/{p['id']}/", f"/api/share/{token}/")
    return json.loads(raw)
