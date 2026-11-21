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


def _old(signer) -> str:
    return hmac.new(signer._mac_key(), b"magic-link-v1:jurors", hashlib.sha256).hexdigest()[:32]


def test_the_submitted_link_signs_in_as_a_guest_never_the_team(client):
    """The 32-hex link went into the Buildathon form (21 Nov) and cannot change: it must open a GUEST session."""
    signer = client.app.state.signer
    r = client.get(f"/api/magic/{_old(signer)}", follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    ck = r.headers.get("set-cookie", "")
    assert "gs_session=g1." in ck, ck
    assert not hasattr(signer, "magic_token")
    s = signer.session(ck.split("gs_session=", 1)[1].split(";", 1)[0])
    assert s is not None and s.kind == "guest"


def test_the_submitted_link_obeys_revocation_and_the_off_switch(client, monkeypatch):
    signer = client.app.state.signer
    monkeypatch.setenv("MAGIC_REVOKED", "jurors")
    assert client.get(f"/api/magic/{_old(signer)}", follow_redirects=False).status_code == 404
    monkeypatch.delenv("MAGIC_REVOKED")
    monkeypatch.setenv("MAGIC_LINKS", "off")
    assert client.get(f"/api/magic/{_old(signer)}", follow_redirects=False).status_code == 404


def test_a_pinned_legacy_token_survives_a_key_change(client, monkeypatch):
    monkeypatch.setenv("MAGIC_LEGACY_TOKENS", "0123456789abcdef0123456789abcdef")
    r = client.get("/api/magic/0123456789abcdef0123456789abcdef", follow_redirects=False)
    assert r.status_code in (302, 303) and "gs_session=g1." in r.headers.get("set-cookie", "")


def test_other_32_hex_tokens_sign_nobody_in(client):
    r = client.get("/api/magic/" + "f" * 32, follow_redirects=False)
    assert r.status_code == 404 and "gs_session=" not in r.headers.get("set-cookie", "")
