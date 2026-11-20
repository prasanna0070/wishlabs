"""Gadget sound routes (Lyria on Vertex AI; the work is in app/sound.py).

Signed in (studio login):
  GET  /api/projects/{pid}/sound               the sound state, the switch and this browser's limit
  POST /api/projects/{pid}/sound               compose now, {"version": n} optional -> 202. One job per project, a
                                               per-session hourly limit and a daily cap for the service; fails closed
  GET  /api/projects/{pid}/sound/{sid}/{name}  theme, chime or device chime audio (HTTP Range, for <audio>)
Read-only share link (no login; shared_view rewrites /api/projects/<pid>/ URLs to these):
  GET  /api/share/{token}/sound/{sid}/{name}   the same files, nothing else (no compose, no state changes)
The gadget's parent page (device token, CORS open, like name-audio):
  GET  /api/device/{tok}/chime.wav             the current device chime: 16 kHz mono 16-bit WAV under 100 KB
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..auth import COOKIE, require_session
from ..projects import NotFound
from ..sound import MIME, SoundError, file_key, public_state, sound_composer
from .common import err, load_project, svc_of

router = APIRouter()

AUDIO_CACHE = "private, max-age=31536000, immutable"   # a composition's files never change (new id per composition)
DEVICE_CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
               "Access-Control-Allow-Headers": "Range, Content-Type", "Access-Control-Max-Age": "600",
               "Access-Control-Expose-Headers": "Content-Length, Content-Range, X-GS-Sound-Id, X-GS-Sample-Rate"}


def session_key(request: Request) -> str:
    """The per-session limit's key: a hash of this browser's signed session cookie (never the cookie itself)."""
    return "s:" + hashlib.sha256((request.cookies.get(COOKIE) or "").encode()).hexdigest()[:24]


def byte_range(header: str | None, size: int):
    """One `bytes=` range -> (start, end) inclusive; None to send the whole file; "bad" when unsatisfiable."""
    if not header or not header.strip().lower().startswith("bytes=") or "," in header:
        return None
    a, _, b = header.strip()[6:].strip().partition("-")
    try:
        if a == "":
            k = int(b)
            return (max(0, size - k), size - 1) if k > 0 and size else "bad"
        start, end = int(a), (int(b) if b else size - 1)
    except ValueError:
        return None
    if start >= size or end < start:
        return "bad"
    return start, min(end, size - 1)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "gadget").lower()).strip("-")[:40] or "gadget"


async def serve_sound(request: Request, p: dict, sid: str, name: str, download: bool = False) -> Response:
    key = file_key(p, sid, name)
    if key is None:
        raise err(404, "not_found", "Sound not found.")
    data = await asyncio.to_thread(sound_composer(svc_of(request)).read, key)
    if data is None:
        raise err(404, "not_found", "Sound not found.")
    etag = f'"{sid}-{name}"'
    headers = {"Cache-Control": AUDIO_CACHE, "ETag": etag, "Accept-Ranges": "bytes"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{_slug(p.get("name"))}-{name}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    rng = byte_range(request.headers.get("range"), len(data))
    if rng == "bad":
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(data)}"})
    if rng:
        a, b = rng
        headers["Content-Range"] = f"bytes {a}-{b}/{len(data)}"
        return Response(content=data[a:b + 1], status_code=206, media_type=MIME[name], headers=headers)
    return Response(content=data, media_type=MIME[name], headers=headers)


# ------------------------------------------------------------------ owner (signed in)
@router.get("/api/projects/{pid}/sound", dependencies=[Depends(require_session)])
async def sound_state(request: Request, pid: str):
    p = await load_project(request, pid, reconcile=False)
    return JSONResponse(public_state(p, sound_composer(svc_of(request)), session_key(request)),
                        headers={"Cache-Control": "no-store"})


@router.post("/api/projects/{pid}/sound", dependencies=[Depends(require_session)])
async def sound_compose(request: Request, pid: str):
    raw = await request.body()
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise err(422, "invalid", "Body must be JSON.") from None
    if not isinstance(body, dict):
        raise err(422, "invalid", "Body must be a JSON object.")
    n = body.get("version")
    if n is not None:
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise err(422, "invalid", "version must be a version number") from None
    svc = svc_of(request)
    c = sound_composer(svc)
    await load_project(request, pid, reconcile=False)
    try:
        await c.start(pid, n, trigger="user", session=session_key(request))
    except SoundError as e:
        raise err(e.status, e.code, str(e)) from None
    except NotFound:
        raise err(404, "not_found", "Project not found.") from None
    p = await svc.repo.aget(pid)
    return JSONResponse(public_state(p, c, session_key(request)), status_code=202,
                        headers={"Cache-Control": "no-store"})


@router.api_route("/api/projects/{pid}/sound/{sid}/{name}", methods=["GET", "HEAD"],
                  dependencies=[Depends(require_session)])
async def sound_file(request: Request, pid: str, sid: str, name: str, download: int = 0):
    return await serve_sound(request, await load_project(request, pid, reconcile=False), sid, name, bool(download))


# ------------------------------------------------------------------ share link (read-only)
@router.api_route("/api/share/{token}/sound/{sid}/{name}", methods=["GET", "HEAD"])
async def share_sound_file(request: Request, token: str, sid: str, name: str, download: int = 0):
    from .public import _shared

    return await serve_sound(request, await _shared(request, token), sid, name, bool(download))


# ------------------------------------------------------------------ the gadget's parent page (device token)
def _device_error(status: int, code: str, message: str) -> Response:
    return Response(content=json.dumps({"error": {"code": code, "message": message}}).encode(), status_code=status,
                    media_type="application/json", headers=DEVICE_CORS)


@router.options("/api/device/{tok}/chime.wav")
async def device_chime_preflight(tok: str):
    return Response(status_code=204, headers=DEVICE_CORS)


@router.api_route("/api/device/{tok}/chime.wav", methods=["GET", "HEAD"])
async def device_chime(request: Request, tok: str):
    """The design's signature chime, sized for the gadget: 16 kHz mono 16-bit WAV, under 100 KB."""
    from .device import _limited, _project_for

    try:
        p = await _project_for(request, tok)
    except Exception as exc:  # noqa: BLE001 - keep the CORS headers on errors too
        d = getattr(exc, "detail", None) or {}
        return _device_error(getattr(exc, "status_code", 500), d.get("code", "error"), d.get("message", "Error."))
    if _limited(tok):
        return _device_error(429, "rate_limited", "Too many requests for this gadget this hour.")
    cur = (p.get("sound") or {}).get("current") or {}
    if not cur.get("id") or not cur.get("device_chime"):
        return _device_error(404, "not_found", "This gadget has no chime yet.")
    try:
        res = await serve_sound(request, p, cur["id"], "device-chime.wav")
    except Exception as exc:  # noqa: BLE001
        d = getattr(exc, "detail", None) or {}
        return _device_error(getattr(exc, "status_code", 500), d.get("code", "error"), d.get("message", "Error."))
    res.headers.update(DEVICE_CORS)
    res.headers["X-GS-Sound-Id"] = cur["id"]
    res.headers["X-GS-Sample-Rate"] = str((cur.get("device_chime") or {}).get("sample_rate") or "")
    return res
