"""GET /api/magic/<token>: a signed, expiring, revocable link that signs the browser in as a guest (app/guest.py).

Each browser that opens it gets its own random guest id, kept in the httponly session cookie, so guests never see
each other's projects. A browser already signed in with the team key keeps its team session; a returning guest keeps
its id (and so its projects). Mint links with: python -m app.magic mint --label jurors --days 21
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import COOKIE, is_secure
from ..config import SPA_BASE

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
INVALID = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Wish Labs</title></head>
<body style="font-family:system-ui,sans-serif;background:#F6F5F1;color:#1B1F24;padding:48px;max-width:560px;margin:auto">
<h1 style="font-size:22px">This link no longer works</h1>
<p>It has expired or was switched off. Ask the Wish Labs team for a new one.</p>
<p><a href="/studio/" style="color:#1B1F24">Go to the studio</a></p></body></html>"""


@router.get("/api/magic/{token}")
async def magic(request: Request, token: str):
    signer = request.app.state.signer
    hit = await asyncio.to_thread(signer.check_magic, token)
    if hit is None:
        return HTMLResponse(INVALID, status_code=404, headers=NO_STORE)
    label, exp = hit
    res = RedirectResponse(f"{SPA_BASE}/", status_code=303, headers=NO_STORE)
    cur = signer.session(request.cookies.get(COOKIE))
    if cur is not None and not cur.guest:
        return res                                   # a team browser stays a team browser
    now = int(time.time())
    until = min(exp, now + signer.days * 86400)
    cookie, _gid = signer.issue_guest(label, until, cur.gid if cur is not None else None)
    res.set_cookie(COOKIE, cookie, max_age=max(60, until - now), httponly=True, samesite="lax", path="/",
                   secure=is_secure(request, request.app.state.svc.settings))
    return res
