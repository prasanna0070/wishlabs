from __future__ import annotations

from app.auth import COOKIE, AccessKey, SessionSigner
from app.config import Settings

from conftest import KEY


def test_health_needs_no_auth(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] and j["builder"] == "fake" and j["agent"] == "scripted" and j["store"] == "local"


def test_protected_routes_401_without_cookie(client):
    for method, url in [("GET", "/api/projects"), ("POST", "/api/projects"), ("GET", "/api/me"),
                        ("GET", "/api/projects/p_x"), ("POST", "/api/projects/p_x/chat"),
                        ("GET", "/api/projects/p_x/v/1/files/build.json"), ("POST", "/api/projects/p_x/tweak")]:
        r = client.request(method, url)
        assert r.status_code == 401, (method, url, r.status_code)
        assert r.json()["error"]["code"] == "unauthorized"


def test_login_bad_key(client):
    r = client.post("/api/login", json={"key": "nope"})
    assert r.status_code == 401
    assert COOKIE not in r.cookies


def test_login_good_key_sets_httponly_cookie(client):
    r = client.post("/api/login", json={"key": KEY})
    assert r.status_code == 200
    sc = r.headers["set-cookie"]
    assert "HttpOnly" in sc and "SameSite=lax" in sc.replace("Lax", "lax")
    assert client.get("/api/me").status_code == 200
    assert client.get("/api/projects").status_code == 200


def test_tampered_cookie_rejected(client):
    client.post("/api/login", json={"key": KEY})
    tok = client.cookies.get(COOKIE)
    bad = tok[:-1] + ("0" if tok[-1] != "0" else "1")
    client.cookies.set(COOKIE, bad)
    assert client.get("/api/me").status_code == 401


def test_session_probe_is_always_200(client):
    # signed out: 200 with authenticated false (no 401 in the browser console for landing-page visitors)
    r = client.get("/api/session")
    assert r.status_code == 200 and r.json() == {"authenticated": False, "expires": None}
    assert "no-store" in r.headers.get("cache-control", "")
    client.cookies.set(COOKIE, "v1.9999999999.abcd.deadbeef")
    assert client.get("/api/session").json()["authenticated"] is False
    client.cookies.clear()
    client.post("/api/login", json={"key": KEY})
    j = client.get("/api/session").json()
    assert j["authenticated"] is True and j["expires"].endswith("Z")
    client.post("/api/logout")
    client.cookies.clear()
    assert client.get("/api/session").json()["authenticated"] is False


def test_logout(client):
    client.post("/api/login", json={"key": KEY})
    assert client.post("/api/logout").status_code == 200
    client.cookies.clear()
    assert client.get("/api/me").status_code == 401


def test_signer_expiry_and_rotation():
    s = SessionSigner(AccessKey(Settings(access_key="k1")), days=1)
    tok, exp = s.issue(now=1000.0)
    assert s.verify(tok, now=1001.0) == exp
    assert s.verify(tok, now=exp + 1) is None
    other = SessionSigner(AccessKey(Settings(access_key="k2")), days=1)
    assert other.verify(tok, now=1001.0) is None
    assert s.verify("garbage") is None and s.verify(None) is None


def test_login_rate_limit(client):
    for _ in range(10):
        assert client.post("/api/login", json={"key": "wrong"}).status_code == 401
    r = client.post("/api/login", json={"key": KEY})
    assert r.status_code == 429
