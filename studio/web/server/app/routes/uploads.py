"""User image routes ("start from an image"; the work is in app/uploads.py).

Signed in (studio login):
  POST   /api/projects/{pid}/uploads             multipart/form-data, field "file": JPEG, PNG, WebP (HEIC when
                                                 pillow-heif is installed), at most 15 MB -> 201 upload record
  GET    /api/projects/{pid}/uploads/{uid}       the normalised JPEG (?thumb=1: the 384 px thumbnail)
  DELETE /api/projects/{pid}/uploads/{uid}       remove an image no chat message carries (a composer that dropped it)
Read-only share link (no login), only while the owner has turned on project.share_uploads:
  GET    /api/share/{token}/uploads/{uid}        the same JPEG (?thumb=1), else 404
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..agent.turn import TurnError
from ..auth import require_session
from ..projects import NotFound
from ..uploads import (ID_RE, MAX_BYTES, MSG_BIG, MSG_PROJECT, PER_PROJECT, UploadError, attached_ids, discard,
                       find_upload, ingest, public, upload_key)
from .common import err, load_project, serve_store_file, svc_of

router = APIRouter()
BODY_SLACK = 256 * 1024             # multipart headers and boundaries around the 15 MB file


async def _capped(request: Request, limit: int):
    n = 0
    async for chunk in request.stream():
        n += len(chunk)
        if n > limit:
            raise UploadError(413, "too_large", MSG_BIG)
        yield chunk


async def _file_from_form(request: Request) -> tuple[bytes, str | None]:
    from starlette.datastructures import UploadFile
    from starlette.formparsers import MultiPartException, MultiPartParser

    ctype = request.headers.get("content-type") or ""
    if not ctype.lower().startswith("multipart/form-data"):
        raise UploadError(422, "invalid", 'Send the image as multipart/form-data in the field "file".')
    try:
        if int(request.headers.get("content-length") or 0) > MAX_BYTES + BODY_SLACK:
            raise UploadError(413, "too_large", MSG_BIG)
    except ValueError:
        raise UploadError(422, "invalid", "Bad Content-Length.") from None
    try:
        parser = MultiPartParser(request.headers, _capped(request, MAX_BYTES + BODY_SLACK), max_files=1,
                                 max_fields=8)
    except AssertionError:                      # python-multipart is not installed on this server
        raise UploadError(503, "unavailable", "Image uploads are not available on this server yet.") from None
    try:
        form = await parser.parse()
    except MultiPartException as e:
        raise UploadError(422, "invalid", f"The upload could not be read: {e}") from None
    try:
        f = form.get("file")
        if not isinstance(f, UploadFile):
            raise UploadError(422, "invalid", 'Attach the image in the field "file".')
        data = await f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise UploadError(413, "too_large", MSG_BIG)
        return data, f.filename
    finally:
        await form.close()


@router.post("/api/projects/{pid}/uploads", status_code=201, dependencies=[Depends(require_session)])
async def upload(request: Request, pid: str):
    svc = svc_of(request)
    p = await load_project(request, pid, reconcile=False)
    try:
        if len(p.get("uploads") or []) >= PER_PROJECT:
            raise UploadError(409, "limit", MSG_PROJECT)
        data, filename = await _file_from_form(request)
        rec = await asyncio.to_thread(ingest, svc.store, pid, data, filename)
    except UploadError as e:
        raise err(e.status, e.code, e.message) from None

    def mut(pp):
        ups = pp.setdefault("uploads", [])
        if len(ups) >= PER_PROJECT:              # another upload won the race for the last slot
            raise TurnError(409, "limit", MSG_PROJECT)
        ups.append(rec)
    try:
        await svc.repo.aupdate(pid, mut)
    except (TurnError, NotFound) as e:
        await asyncio.to_thread(discard, svc.store, pid, rec["id"])
        if isinstance(e, NotFound):
            raise err(404, "not_found", "Project not found.") from None
        raise err(e.status, e.code, e.message) from None
    return JSONResponse(public(rec), status_code=201)


async def _serve(request: Request, p: dict, uid: str, thumb: int, *, owner: bool):
    if not ID_RE.fullmatch(uid) or find_upload(p, uid) is None:
        raise err(404, "not_found", "File not found.")
    # never cached for long, even for the owner: a shared venue laptop must not show them from cache after logout
    return await serve_store_file(request, upload_key(p["id"], uid, bool(thumb)), immutable=False)


@router.api_route("/api/projects/{pid}/uploads/{uid}", methods=["GET", "HEAD"],
                  dependencies=[Depends(require_session)])
async def upload_file(request: Request, pid: str, uid: str, thumb: int = 0):
    return await _serve(request, await load_project(request, pid, reconcile=False), uid, thumb, owner=True)


@router.delete("/api/projects/{pid}/uploads/{uid}", dependencies=[Depends(require_session)])
async def upload_delete(request: Request, pid: str, uid: str):
    svc = svc_of(request)
    p = await load_project(request, pid, reconcile=False)
    if find_upload(p, uid) is None:
        raise err(404, "not_found", "File not found.")
    if uid in attached_ids(p):
        raise err(409, "conflict", "That image is part of the conversation, so it stays with the project.")

    def mut(pp):
        if uid in attached_ids(pp):
            raise TurnError(409, "conflict", "That image is part of the conversation, so it stays with the project.")
        pp["uploads"] = [u for u in pp.get("uploads") or [] if u.get("id") != uid]
    try:
        await svc.repo.aupdate(pid, mut)
    except TurnError as e:
        raise err(e.status, e.code, e.message) from None
    except NotFound:
        raise err(404, "not_found", "Project not found.") from None
    await asyncio.to_thread(discard, svc.store, pid, uid)
    return {"ok": True}


@router.api_route("/api/share/{token}/uploads/{uid}", methods=["GET", "HEAD"])
async def share_upload_file(request: Request, token: str, uid: str, thumb: int = 0):
    from .public import _shared

    p = await _shared(request, token)
    # only when the owner shared the images, and only images a message carries: one removed from the composer or
    # never sent stays private (same answer as a bad id)
    if p.get("share_uploads") is not True or uid not in attached_ids(p):
        raise err(404, "not_found", "File not found.")
    return await _serve(request, p, uid, thumb, owner=False)
