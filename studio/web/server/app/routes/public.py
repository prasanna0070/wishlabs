"""Unauthenticated routes: login, logout, health, and read-only share links."""
from __future__ import annotations

import asyncio
import os
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..auth import COOKIE, KeyUnavailable, client_ip, is_secure
from ..config import SERVER_VERSION
from ..projects import NotFound
from .common import err, serve_concept_file, serve_export, serve_version_file, shared_view, svc_of

router = APIRouter()


@router.get("/api/health")
async def health(request: Request):
    svc = svc_of(request)
    return {"ok": True, "version": SERVER_VERSION, "builder": svc.builder.kind, "agent": svc.brain.kind,
            "store": svc.store.kind, "engine": await asyncio.to_thread(lambda: svc.bridge.available)}


@router.post("/api/login")
async def login(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    key = str((body or {}).get("key") or "")
    ip = client_ip(request)
    limiter = request.app.state.limiter
    if limiter.blocked(ip):
        raise err(429, "rate_limited", "Too many attempts. Wait a few minutes and try again.")
    signer = request.app.state.signer
    try:
        ok = await asyncio.to_thread(signer.check_key, key)
    except KeyUnavailable:
        raise err(503, "unavailable", "Sign-in is not configured on this server.") from None
    if not ok:
        limiter.fail(ip)
        raise err(401, "unauthorized", "That access key is not right.")
    token, exp = signer.issue()
    from datetime import datetime, timezone

    res = JSONResponse({"ok": True, "expires": datetime.fromtimestamp(exp, timezone.utc).isoformat()
                       .replace("+00:00", "Z")})
    res.set_cookie(COOKIE, token, max_age=signer.days * 86400, httponly=True, samesite="lax", path="/",
                   secure=is_secure(request, request.app.state.svc.settings))
    return res


@router.post("/api/logout")
async def logout(request: Request):
    res = JSONResponse({"ok": True})
    res.delete_cookie(COOKIE, path="/")
    return res


def share_url() -> str | None:
    """SHARE_URL: a read-only share link (/studio/s/<token>) the sign-in page offers as "See a real build" to visitors
    without an access key. Only an https URL or a same-site absolute path is used; anything else hides the link."""
    raw = (os.environ.get("SHARE_URL") or "").strip()
    if re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?(/[^\s\"'<>\\]*)?", raw):
        return raw
    if re.fullmatch(r"/(?!/)[^\s\"'<>\\]*", raw):
        return raw
    return None


@router.get("/api/session")
async def session(request: Request):
    """The SPA's start-up probe: always 200, so a signed-out visitor (every landing-page "Make a wish") does not get
    a 401 logged in the browser console. /api/me keeps its 401 contract."""
    from datetime import datetime, timezone

    exp = request.app.state.signer.verify(request.cookies.get(COOKIE))
    if exp is None:
        body = {"authenticated": False, "expires": None}
    else:
        body = {"authenticated": True,
                "expires": datetime.fromtimestamp(exp, timezone.utc).isoformat().replace("+00:00", "Z")}
    link = share_url()
    if link:  # only when configured: the response keeps its old shape otherwise
        body["share_url"] = link
    if not _voice_enabled(request):
        body["voice"] = False  # LIVE_ENABLED=0: the studio hides its Talk buttons (only then: same shape otherwise)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


def _voice_enabled(request: Request) -> bool:
    from ..voice_live import LiveSettings

    cfg = getattr(request.app.state, "voice_cfg", None) or LiveSettings.from_env()
    return bool(cfg.enabled)


# ------------------------------------------------------------------ share
async def _shared(request: Request, token: str) -> dict:
    svc = svc_of(request)
    if not token.startswith("s_") or "/" in token or len(token) > 64:
        raise err(404, "not_found", "This link is not valid.")
    ref = await asyncio.to_thread(svc.store.read_json, f"shares/{token}.json")
    if not ref:
        raise err(404, "not_found", "This link is not valid.")
    try:
        p = await svc.repo.aget(ref["pid"])
    except NotFound:
        raise err(404, "not_found", "This link is not valid.") from None
    if (p.get("share") or {}).get("token") != token:
        raise err(404, "not_found", "This link is not valid.")
    return await svc.builds.reconcile(p, auto_sound=False)     # a share visit never starts an AI call


@router.get("/api/share/{token}")
async def share_get(request: Request, token: str):
    return shared_view(await _shared(request, token))


@router.api_route("/api/share/{token}/v/{n}/files/{path:path}", methods=["GET", "HEAD"])
async def share_file(request: Request, token: str, n: int, path: str, download: int = 0):
    return await serve_version_file(request, await _shared(request, token), n, path, bool(download))


@router.api_route("/api/share/{token}/v/{n}/export", methods=["GET", "HEAD"])
async def share_export(request: Request, token: str, n: int):
    return await serve_export(request, await _shared(request, token), n)


@router.api_route("/api/share/{token}/concepts/{cid}/{name}", methods=["GET", "HEAD"])
async def share_concept(request: Request, token: str, cid: str, name: str):
    return await serve_concept_file(request, await _shared(request, token), cid, name)

