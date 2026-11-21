"""The magic link is the guest link (app/routes/magic.py, app/guest.py) and nothing else.

Local main once carried a team-session magic link (1159cbc: GET /api/magic/<32 hex> gave anyone the key login's
session, so every team project). It is merged into hack/magic-link only so main fast-forwards; its route is gone."""
from __future__ import annotations

import hashlib
import hmac

from fastapi.routing import APIRoute


def _routes(routes):
    out = []
    for r in routes:
        inner = getattr(r, "original_router", None)
        out += _routes(inner.routes) if inner is not None else [r]
    return out


def test_one_route_serves_magic_links_and_it_is_the_guest_route(client):
    hits = [r for r in _routes(client.app.routes) if isinstance(r, APIRoute) and r.path == "/api/magic/{token}"]
    assert len(hits) == 1 and hits[0].endpoint.__module__ == "app.routes.magic"


def test_the_old_team_session_token_signs_nobody_in(client):
    signer = client.app.state.signer
    old = hmac.new(signer._mac_key(), b"magic-link-v1:jurors", hashlib.sha256).hexdigest()[:32]
    r = client.get(f"/api/magic/{old}", follow_redirects=False)
    assert r.status_code == 404 and "gs_session=" not in r.headers.get("set-cookie", "")
    assert not hasattr(signer, "magic_token")
