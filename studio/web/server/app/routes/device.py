"""Routes the gadget's own parent page calls (MAJ-33). No sign-in: a per-project device token in the path
(POST /api/projects/{pid}/device-token makes one). CORS is open (no cookies are read), because the parent page is
served by the gadget on the home network. Rate-limited per token."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..projects import NotFound
from ..tts import TtsError
from .common import err, svc_of

router = APIRouter()

CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "600",
        "Access-Control-Expose-Headers": "X-GS-Name, X-GS-Slug, X-GS-Voice, X-GS-Sample-Rate, X-GS-Bytes"}
PER_HOUR = 60
_hits: dict[str, deque] = defaultdict(deque)


def _limited(tok: str) -> bool:
    q = _hits[tok]
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= PER_HOUR:
        return True
    q.append(now)
    return False


async def _project_for(request: Request, tok: str) -> dict:
    svc = svc_of(request)
    if not tok.startswith("d_") or "/" in tok or len(tok) > 64:
        raise err(404, "not_found", "This device link is not valid.")
    ref = await asyncio.to_thread(svc.store.read_json, f"devices/{tok}.json")
    if not ref:
        raise err(404, "not_found", "This device link is not valid.")
    try:
        p = await svc.repo.aget(ref["pid"])
    except NotFound:
        raise err(404, "not_found", "This device link is not valid.") from None
    if (p.get("device") or {}).get("token") != tok:
        raise err(404, "not_found", "This device link is not valid.")
    return p


@router.options("/api/device/{tok}/name-audio")
async def name_audio_preflight(tok: str):
    return Response(status_code=204, headers=CORS)


@router.post("/api/device/{tok}/name-audio")
async def device_name_audio(request: Request, tok: str):
    from .projects import _pack_voice, wav_response

    svc = svc_of(request)
    try:
        p = await _project_for(request, tok)
    except Exception as exc:  # noqa: BLE001 - keep CORS headers on errors too
        return _json_error(exc)
    if _limited(tok):
        return Response(content=b'{"error":{"code":"rate_limited","message":"Too many name clips this hour."}}',
                        status_code=429, media_type="application/json", headers=CORS)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    name = str((body or {}).get("name") or request.query_params.get("name") or "")
    try:
        data, info = await svc.names.clip(name, await _pack_voice(svc, p))
    except TtsError as e:
        return Response(content=('{"error":{"code":"%s","message":%s}}' % (
            "invalid" if e.status == 422 else "unavailable", _q(str(e)))).encode(),
            status_code=e.status, media_type="application/json", headers=CORS)
    res = wav_response(data, info)
    res.headers.update(CORS)
    return res


def _q(s: str) -> str:
    import json

    return json.dumps(s)


def _json_error(exc) -> Response:
    detail = getattr(exc, "detail", None) or {"code": "error", "message": str(exc)}
    status = getattr(exc, "status_code", 500)
    import json

    return Response(content=json.dumps({"error": detail}).encode(), status_code=status,
                    media_type="application/json", headers=CORS)
