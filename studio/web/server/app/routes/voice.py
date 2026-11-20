"""Voice wish routes: the Gemini Live WebSocket (studio login required) and the stored recording (owner and
read-only share). Session logic and limits: app/voice_live.py."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from fastapi import APIRouter, Depends, Request, Response, WebSocket

from ..auth import COOKIE, require_session
from ..voice_live import AUDIO_KEY, LiveSettings, VoiceHub, VoiceSession, login_id, origin_ok, vertex_live
from .common import err, load_project, svc_of

log = logging.getLogger("gstudio.voice")

router = APIRouter()
START_WAIT_S = 10.0


def _hub(app) -> VoiceHub:
    hub = getattr(app.state, "voice_hub", None)
    if hub is None:
        hub = app.state.voice_hub = VoiceHub(getattr(app.state, "voice_cfg", None) or LiveSettings.from_env())
    return hub


@router.websocket("/api/voice/live")
async def voice_live(ws: WebSocket):
    app = ws.app
    cfg: LiveSettings = getattr(app.state, "voice_cfg", None) or LiveSettings.from_env()
    app.state.voice_cfg = cfg
    svc = app.state.svc
    cookie = ws.cookies.get(COOKIE)
    if app.state.signer.verify(cookie) is None:
        await ws.close(code=4401)                  # handshake refused: not signed in
        return
    if not origin_ok(ws.headers.get("origin"), ws.headers.get("x-forwarded-host") or ws.headers.get("host"), cfg,
                     svc.settings.public_url):
        await ws.close(code=4403)                  # handshake refused: another site
        return
    await ws.accept()

    async def fail(code: str, message: str) -> None:
        await ws.send_text(json.dumps({"type": "error", "code": code, "message": message}))
        await ws.close(code=4000 + {"disabled": 3, "busy": 9, "rate_limited": 29, "capacity": 29}.get(code, 0))

    if not cfg.enabled:
        await fail("disabled", "Voice wishes are switched off on this server. Type your wish instead.")
        return
    hub = _hub(app)
    login = login_id(cookie or "")
    refused = hub.admit(login)
    if refused:
        await fail(*refused)
        return
    try:
        try:
            first = await asyncio.wait_for(ws.receive(), timeout=START_WAIT_S)
        except asyncio.TimeoutError:
            await fail("timeout", "The voice wish did not start.")
            return
        if first.get("type") == "websocket.disconnect":
            return
        try:
            opts = json.loads(first.get("text") or "")
        except ValueError:
            opts = None
        if not isinstance(opts, dict) or opts.get("type") != "start":
            await fail("invalid", "Send a start message first.")
            return
        connect = getattr(app.state, "voice_connect", None) or vertex_live
        reason = await VoiceSession(ws, app, svc, cfg, connect, opts).run()
        log.info("voice session ended: %s", reason)
        try:
            await ws.close(code=1000)
        except Exception:  # noqa: BLE001 - already closed by the browser
            pass
    finally:
        hub.release(login)


# ------------------------------------------------------------------ the stored recording
@router.api_route("/api/projects/{pid}/voice/{name}", methods=["GET", "HEAD"], dependencies=[Depends(require_session)])
async def voice_file(request: Request, pid: str, name: str):
    p = await load_project(request, pid, reconcile=False)
    return await _serve(request, p, name)


@router.api_route("/api/share/{token}/voice/{name}", methods=["GET", "HEAD"])
async def share_voice_file(request: Request, token: str, name: str):
    from .public import _shared

    return await _serve(request, await _shared(request, token), name)


async def _serve(request: Request, p: dict, name: str):
    """The recording, with byte ranges: Safari's <audio> asks for bytes=0-1 first and will not play a file served
    without 206 Partial Content. The file is small (at most a few MB), so it is read whole."""
    voice = p.get("voice") or {}
    if f"voice/{name}" != AUDIO_KEY or not voice.get("audio_url"):
        raise err(404, "not_found", "File not found.")
    data = await asyncio.to_thread(svc_of(request).store.read_bytes, f"projects/{p['id']}/{AUDIO_KEY}")
    if not data:
        raise err(404, "not_found", "File not found.")
    etag = '"' + hashlib.sha256(data).hexdigest()[:32] + '"'
    headers = {"Cache-Control": "private, max-age=300", "ETag": etag, "Accept-Ranges": "bytes"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    size = len(data)
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", request.headers.get("range") or "")
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:                                       # bytes=-N: the last N bytes
            start, end = max(0, size - int(m.group(2))), size - 1
        if start >= size or start > end:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        return Response(content=data[start:end + 1], status_code=206, media_type="audio/wav",
                        headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"})
    return Response(content=data, media_type="audio/wav", headers=headers)

