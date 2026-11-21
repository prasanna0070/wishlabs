"""Magic links and guest isolation (app/guest.py, app/routes/magic.py, auth.Session).

The isolation test walks EVERY registered route (app.routes, flattened), so a route added later cannot be missed: a
guest must get 404 on every {pid} route of a team project, and every route without {pid} must be a known public route
or on the guest allow-list."""
from __future__ import annotations

import json
import re
import time

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient

from app import guest
from app.auth import COOKIE
from app.main import create_app
from conftest import KEY, make_settings, sse

PARAMS = {"n": "1", "path": "spec.json", "cid": "c_x", "name": "render.png", "uid": "u_x", "sid": "snd_x",
          "component": "body", "token": "s_nope", "tok": "d_nope", "rest": "x", "full_path": "x"}
# routes a guest may reach WITHOUT a project id: public by design (no session read) or the guest allow-list
PUBLIC = {"/api/health", "/api/login", "/api/logout", "/api/session", "/api/magic/{token}",
          "/api/share/{token}", "/api/share/{token}/v/{n}/files/{path:path}", "/api/share/{token}/v/{n}/export",
          "/api/share/{token}/concepts/{cid}/{name}", "/api/share/{token}/sound/{sid}/{name}",
          "/api/share/{token}/uploads/{uid}", "/api/share/{token}/voice/{name}",
          "/api/device/{tok}/name-audio", "/api/device/{tok}/chime.wav",
          "/api/{rest:path}", "/studio", "/studio/{full_path:path}", "/{full_path:path}",
          "/api/openapi.json", "/api/docs", "/docs/oauth2-redirect"}
GUEST_FREE_PATHS = {"/api/me", "/api/projects"}
WS = {"/api/voice/live"}      # tested on its own below (the project id travels in the start message)


def flat_routes(routes) -> list:
    out = []
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:          # FastAPI 0.16x keeps an included router as one lazy entry
            prefix = getattr(getattr(r, "include_context", None), "prefix", "") or ""
            assert not prefix, "a router with a prefix: teach flat_routes to apply it"
            out += flat_routes(inner.routes)
        else:
            out.append(r)
    return out


def fill(path: str, pid: str) -> str:
    def sub(m):
        name = m.group(1).split(":")[0]
        return pid if name == "pid" else PARAMS[name]
    return re.sub(r"\{([^}]+)\}", sub, path)


def mint(app, label="jurors", days=21, now=None) -> str:
    return app.state.signer.mint_magic(label, days, now)


def open_link(c: TestClient, token: str):
    return c.get(f"/api/magic/{token}", follow_redirects=False)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def team(app):
    with TestClient(app) as c:
        assert c.post("/api/login", json={"key": KEY}).status_code == 200
        yield c


def new_guest(app) -> TestClient:
    c = TestClient(app)
    r = open_link(c, mint(app))
    assert r.status_code == 303 and r.headers["location"] == "/studio/", r.text
    return c


@pytest.fixture
def guest_c(app, team):     # team first: its TestClient runs the app's lifespan
    c = new_guest(app)
    yield c
    c.close()


@pytest.fixture
def team_project(app, team):
    p = team.post("/api/projects", json={"name": "Client concept (CONFIDENTIAL)"}).json()

    def mut(pp):
        pp["order_requests"] = [{"email": "client@example.com"}]
        pp["device"] = {"token": "d_secret"}
        pp["concepts"] = [{"id": "c_x", "render_url": f"/api/projects/{pp['id']}/concepts/c_x/render.png"}]
    app.state.svc.repo.update(p["id"], mut)
    app.state.svc.store.write_bytes(f"projects/{p['id']}/concepts/c_x/render.png", b"\x89PNG confidential")
    return p["id"]


def project_bytes(app, pid):
    return app.state.svc.store.read_bytes(f"projects/{pid}/project.json")


# ------------------------------------------------------------------ the magic link and its token
def test_magic_link_signs_in_a_guest_with_its_own_id(app, team):
    c = TestClient(app)
    r = open_link(c, mint(app))
    assert r.status_code == 303 and r.headers["location"] == "/studio/"
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    sc = r.headers["set-cookie"]
    assert sc.startswith(COOKIE + "=g1.") and "HttpOnly" in sc and "samesite=lax" in sc.lower()
    s = c.get("/api/session").json()
    assert s["authenticated"] is True and s["kind"] == "guest"
    assert s["guest"]["left"] == {"projects": 2, "turns": 15, "builds": 3, "voice": 3, "sound": 3}
    me = c.get("/api/me").json()
    assert me["kind"] == "guest" and me["guest"]["limits"]["builds"] == 3
    gid = c.cookies.get(COOKIE).split(".")[2]
    assert open_link(c, mint(app)).status_code == 303          # opening it again keeps the same guest id
    assert c.cookies.get(COOKIE).split(".")[2] == gid
    other = new_guest(app)
    assert other.cookies.get(COOKIE).split(".")[2] != gid       # another browser: another guest
    assert team.get("/api/session").json()["kind"] == "team"
    r = open_link(team, mint(app))                              # a team browser keeps its team session
    assert r.status_code == 303 and "set-cookie" not in r.headers
    assert team.get("/api/me").json()["kind"] == "team"


@pytest.mark.parametrize("case", ["expired", "forged", "wrong_label", "other_key", "garbage", "cookie_as_link",
                                  "unicode"])
def test_bad_links_sign_nobody_in(app, team, settings, tmp_path, case):
    good = mint(app)
    if case == "expired":
        tok = mint(app, days=21, now=time.time() - 22 * 86400)
    elif case == "forged":
        tok = good[:-1] + ("0" if good[-1] != "0" else "1")
    elif case == "wrong_label":
        v, label, exp, sig = good.split(".")
        tok = ".".join([v, "team", exp, sig])
    elif case == "other_key":
        tok = create_app(make_settings(tmp_path / "o", access_key="another-key")).state.signer.mint_magic("jurors")
    elif case == "garbage":
        tok = "m1.jurors.notanumber.abc"
    elif case == "unicode":                 # non-ASCII digits and signature: a 404, never a 500
        v, label, exp, sig = good.split(".")
        tok = ".".join([v, label, exp + "\u00b2", "\u00e9" * 64])
    else:   # a team session token is not a magic link
        tok = team.cookies.get(COOKIE)
    c = TestClient(app)
    r = open_link(c, tok)
    assert r.status_code == 404 and "no longer works" in r.text and "set-cookie" not in r.headers
    assert c.get("/api/session").json()["authenticated"] is False
    assert c.get("/api/projects").status_code == 401


def test_labels_are_checked(app):
    for bad in ("", "Jurors", "a.b", "x" * 33, "-x"):
        with pytest.raises(ValueError):
            mint(app, label=bad)


def test_revoked_label_and_off_switch_end_links_and_sessions(app, team, monkeypatch):
    g = new_guest(app)
    assert g.get("/api/me").status_code == 200
    monkeypatch.setenv("MAGIC_REVOKED", "demo+jurors")
    assert open_link(TestClient(app), mint(app)).status_code == 404
    assert g.get("/api/me").status_code == 401 and g.get("/api/session").json()["authenticated"] is False
    assert open_link(TestClient(app), mint(app, label="press")).status_code == 303   # other labels still work
    monkeypatch.delenv("MAGIC_REVOKED")
    assert g.get("/api/me").status_code == 200
    monkeypatch.setenv("MAGIC_LINKS", "off")
    assert open_link(TestClient(app), mint(app)).status_code == 404
    assert g.get("/api/me").status_code == 401
    assert team.get("/api/me").status_code == 200               # the team is never affected


def test_rotating_the_key_ends_links_and_guest_sessions(app, team, tmp_path, settings):
    tok = mint(app)
    g = new_guest(app)
    rotated = create_app(make_settings(settings.store_dir.parent, access_key="rotated-key"))
    with TestClient(rotated) as c:
        assert open_link(c, tok).status_code == 404
        c.cookies.set(COOKIE, g.cookies.get(COOKIE))
        assert c.get("/api/me").status_code == 401


def test_forged_guest_cookie_is_refused(app, team):
    g = new_guest(app)
    v, exp, gid, label, sig = g.cookies.get(COOKIE).split(".")
    for bad in (f"g1.{exp}.{'0' * 24}.{label}.{sig}", f"g1.{int(exp) + 9}.{gid}.{label}.{sig}",
                f"g1.{exp}.{gid}.press.{sig}", f"v1.{exp}.{gid}.{sig}", f"g1.{exp}.{gid}.{label}.{'Z' * 64}"):
        c = TestClient(app)
        c.cookies.set(COOKIE, bad)
        assert c.get("/api/me").status_code == 401, bad


def test_mint_cli_prints_only_a_working_url(app, team, monkeypatch, capsys):
    from app import magic

    monkeypatch.delenv("ACCESS_KEY", raising=False)
    assert magic.main(["mint", "--label", "jurors"]) == 2
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("ACCESS_KEY", KEY)
    assert magic.main(["mint", "--label", "jurors", "--days", "21", "--base", "https://wish.example/"]) == 0
    out = capsys.readouterr().out.strip()
    assert re.fullmatch(r"https://wish\.example/api/magic/m1\.jurors\.\d+\.[0-9a-f]{64}", out)
    assert KEY not in out
    exp = int(out.split(".")[-2])
    assert abs(exp - (time.time() + 21 * 86400)) < 120
    assert open_link(TestClient(app), out.rsplit("/", 1)[1]).status_code == 303


def test_mint_cli_needs_only_the_standard_library(app):
    import subprocess
    import sys
    from pathlib import Path

    from app import magic

    now = time.time()
    assert magic.mint_token(KEY, "jurors", 21, now) == app.state.signer.mint_magic("jurors", 21, now)
    code = "import sys, app.magic; bad = [m for m in ('fastapi', 'starlette', 'google') if m in sys.modules]; " \
           "print(bad); sys.exit(1 if bad else 0)"
    r = subprocess.run([sys.executable, "-c", code], cwd=Path(magic.__file__).parents[1], capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ------------------------------------------------------------------ isolation: every route
def test_guest_gets_404_on_every_route_of_a_team_project(app, team, guest_c, team_project):
    before = project_bytes(app, team_project)
    seen_pid, unknown = 0, []
    for r in flat_routes(app.routes):
        if isinstance(r, APIWebSocketRoute) or type(r).__name__ == "WebSocketRoute":
            assert r.path in WS, f"new WebSocket route {r.path}: decide what a guest may do there"
            continue
        if "{pid}" not in r.path:
            if r.path not in PUBLIC | GUEST_FREE_PATHS:
                unknown.append(r.path)
            continue
        assert isinstance(r, APIRoute)
        url = fill(r.path, team_project)
        for method in sorted(r.methods):
            res = guest_c.request(method, url, json={})
            assert res.status_code == 404, (method, url, res.status_code, res.text[:200])
            if method != "HEAD":
                assert res.json()["error"]["message"] == "Project not found.", (method, url)
                assert "CONFIDENTIAL" not in res.text
            seen_pid += 1
    assert not unknown, f"routes without a project id that the guest rule does not know: {unknown}"
    assert seen_pid > 40                       # the walk really covered the project routes
    assert project_bytes(app, team_project) == before          # and nothing was written
    assert all(p["id"] != team_project for p in guest_c.get("/api/projects").json()["projects"])
    assert team.get(fill("/api/projects/{pid}/concepts/{cid}/{name}", team_project)).status_code == 200


def test_guest_not_found_on_routes_without_project_id_off_the_allow_list(app, team, guest_c):
    # a protected route without {pid} that is not GUEST_FREE answers 404 for a guest (deny by default)
    from fastapi import APIRouter, Depends

    from app.auth import require_session

    extra = APIRouter(dependencies=[Depends(require_session)])

    @extra.get("/api/admin-thing")
    async def admin_thing():
        return {"secret": True}
    app.router.routes.insert(0, extra.routes[0])
    assert guest_c.get("/api/admin-thing").status_code == 404
    assert team.get("/api/admin-thing").status_code == 200


def test_guests_never_see_each_other(app, team):
    a, b = new_guest(app), new_guest(app)
    pa = a.post("/api/projects", json={"name": "A's lamp"}).json()
    assert pa["owner"].startswith("g_")
    assert [p["id"] for p in a.get("/api/projects").json()["projects"]] == [pa["id"]]
    assert b.get("/api/projects").json()["projects"] == []
    assert b.get(f"/api/projects/{pa['id']}").status_code == 404
    assert b.post(f"/api/projects/{pa['id']}/share").status_code == 404
    assert b.delete(f"/api/projects/{pa['id']}").status_code == 404
    assert a.get(f"/api/projects/{pa['id']}").status_code == 200
    assert any(p["id"] == pa["id"] for p in team.get("/api/projects").json()["projects"])   # the team sees all
    sh = a.post(f"/api/projects/{pa['id']}/share").json()           # a guest may share its own project
    tok = sh.get("token") or sh.get("share", {}).get("token")
    assert tok and "owner" not in TestClient(app).get(f"/api/share/{tok}").json()


# ------------------------------------------------------------------ showcases: read-only
def test_showcase_is_listed_and_read_only(app, team, guest_c, team_project, monkeypatch):
    monkeypatch.setenv("SHOWCASE_PROJECTS", team_project)
    rows = guest_c.get("/api/projects").json()["projects"]
    assert rows[0]["id"] == team_project and rows[0]["showcase"] is True
    p = guest_c.get(f"/api/projects/{team_project}").json()
    assert p["read_only"] is True and p["showcase"] is True
    assert not {"order_requests", "device", "share"} & set(p)
    assert guest_c.get(fill("/api/projects/{pid}/concepts/{cid}/{name}", team_project)).status_code == 200
    app.state.svc.repo.update(team_project, lambda pp: pp.update(uploads=[{"id": "u_x", "name": "client.png"}]))
    assert "uploads" not in guest_c.get(f"/api/projects/{team_project}").json()      # not shared: hidden
    r = guest_c.get(fill("/api/projects/{pid}/uploads/{uid}", team_project))
    assert r.status_code == 404 and r.json()["error"]["message"] == "File not found."
    before = project_bytes(app, team_project)
    writes = 0
    for r in flat_routes(app.routes):
        if not isinstance(r, APIRoute) or "{pid}" not in r.path:
            continue
        url = fill(r.path, team_project)
        for method in sorted(r.methods):
            res = guest_c.request(method, url, json={"text": "make it red", "mode": "pro"})
            if method in ("GET", "HEAD"):
                assert res.status_code != 403, (method, url)
                if method == "GET" and res.status_code == 404:
                    assert res.json()["error"]["message"] != "Project not found.", url
            else:
                assert res.status_code == 403, (method, url, res.status_code)
                assert res.json()["error"] == {"code": "showcase", "message": guest.SHOWCASE_MESSAGE}
                writes += 1
    assert writes > 20
    assert project_bytes(app, team_project) == before


def test_showcase_file_fallback(app, guest_c, team_project, tmp_path, monkeypatch):
    f = tmp_path / "showcase.json"
    f.write_text(json.dumps([team_project, "not an id", 5]))
    monkeypatch.setattr(guest, "SHOWCASE_FILE", f)
    monkeypatch.delenv("SHOWCASE_PROJECTS", raising=False)
    assert guest.showcase_ids() == [team_project]
    assert guest_c.get(f"/api/projects/{team_project}").status_code == 200
    f.write_text("[]")
    assert guest_c.get(f"/api/projects/{team_project}").status_code == 404


# ------------------------------------------------------------------ daily limits
def test_guest_limits_projects_and_standard_mode(app, team, guest_c):
    p1 = guest_c.post("/api/projects", json={"mode": "pro"}).json()
    assert p1["mode"] == "fast"                                  # Standard only
    r = guest_c.patch(f"/api/projects/{p1['id']}", json={"mode": "pro"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "standard_only"
    assert guest_c.post("/api/projects", json={}).status_code == 201
    r = guest_c.post("/api/projects", json={})
    assert r.status_code == 429
    assert r.json()["error"]["message"] == "You have used today's 2 wishes. Come back tomorrow, or watch the showcases."
    assert guest_c.get("/api/me").json()["guest"]["left"]["projects"] == 0
    for _ in range(3):                                           # the team has no limits
        assert team.post("/api/projects", json={}).status_code == 201


def test_guest_turn_limit_and_site_wide_limit(app, team, monkeypatch):
    monkeypatch.setenv("GUEST_DAILY_TURNS", "1")
    a = new_guest(app)
    pid = a.post("/api/projects", json={}).json()["id"]
    evs = sse(a, "POST", f"/api/projects/{pid}/chat", json={"text": "a desk lamp that glows warm", "mode": "pro"})
    assert evs
    r = a.post(f"/api/projects/{pid}/chat", json={"text": "make it taller"})
    assert r.status_code == 429 and r.json()["error"]["details"]["what"] == "turns"
    assert "today's 1 message to the designer." in r.json()["error"]["message"]
    monkeypatch.setenv("GUEST_DAILY_TURNS", "15")
    monkeypatch.setenv("GUESTS_DAILY_TURNS", "1")                # site-wide: all guests together
    b = new_guest(app)
    pb = b.post("/api/projects", json={}).json()["id"]
    r = b.post(f"/api/projects/{pb}/chat", json={"text": "a robot"})
    assert r.status_code == 429 and r.json()["error"]["details"]["site"] is True
    assert "reached today's limit for guests" in r.json()["error"]["message"]
    tp = team.post("/api/projects", json={}).json()["id"]
    assert sse(team, "POST", f"/api/projects/{tp}/chat", json={"text": "a desk lamp"})   # team unaffected


def test_guest_build_limit_is_charged_at_the_build_chokepoint(app, team, monkeypatch):
    import asyncio

    from app.auth import Session
    from app.guest import ACTING, LimitReached, build_owner, quota_of

    svc = app.state.svc
    p = svc.repo.create(None, "fast", "g_" + "a" * 24)
    monkeypatch.setenv("GUEST_DAILY_BUILDS", "0")
    with pytest.raises(LimitReached) as e:          # every build path (agent, tweak, restore, voice) calls start()
        asyncio.run(svc.builds.start(p["id"], {}, source="tweak", label="x"))
    assert e.value.message == "You have used today's 0 builds. Come back tomorrow, or watch the showcases."
    assert build_owner(p) == p["owner"]                         # no request behind it: the guest owner pays
    tok = ACTING.set(Session("team", 0))                        # the team acting in a guest project: no charge
    try:
        assert build_owner(p) is None
    finally:
        ACTING.reset(tok)
    assert build_owner(svc.repo.create(None, "fast")) is None   # a team project: no charge
    monkeypatch.delenv("GUEST_DAILY_BUILDS")
    assert quota_of(svc).left(p["owner"])["left"]["builds"] == 3


def test_counters_are_shared_across_instances(app, settings):
    from app.guest import GuestQuota, LimitReached

    a, b = GuestQuota(app.state.svc.store), GuestQuota(app.state.svc.store)
    owner = "g_" + "b" * 24
    for i in range(3):
        (a if i % 2 else b).charge(owner, "builds")
    with pytest.raises(LimitReached):
        a.charge(owner, "builds")
    assert b.left(owner)["left"]["builds"] == 0
    raw = json.loads(app.state.svc.store.read_bytes(a.key()))
    assert raw["guests"][owner]["builds"] == 3 and raw["site"]["builds"] == 3


def test_guest_sound_limit(app, team, guest_c, monkeypatch):
    monkeypatch.setenv("GUEST_DAILY_SOUND", "0")
    pid = guest_c.post("/api/projects", json={}).json()["id"]
    r = guest_c.post(f"/api/projects/{pid}/sound", json={})
    assert r.status_code == 429 and r.json()["error"]["details"]["what"] == "sound"


# ------------------------------------------------------------------ voice: the project id in the start message
def test_guest_voice_never_targets_a_team_project(settings, team_project, app, monkeypatch):
    from test_voice_live import connector, converse

    from app.voice_live import LiveSettings

    app.state.voice_cfg = LiveSettings(max_seconds=30, fallback_model=None)
    app.state.voice_connect = connector()
    blank = app.state.svc.repo.create(None, "fast")              # a blank team project, a valid voice target
    before = project_bytes(app, blank["id"])
    g = new_guest(app)
    with g.websocket_connect("/api/voice/live") as ws:
        events, _ = converse(ws, {"client_turn": True, "project_id": blank["id"], "mode": "pro"})
    sub = next(e for e in events if e["type"] == "submitted")
    assert sub["project_id"] != blank["id"]
    made = app.state.svc.repo.get(sub["project_id"])
    assert made["owner"] == "g_" + g.cookies.get(COOKIE).split(".")[2]
    assert made["mode"] == "fast"
    assert project_bytes(app, blank["id"]) == before
    assert [p["id"] for p in g.get("/api/projects").json()["projects"]] == [sub["project_id"]]
    monkeypatch.setenv("GUEST_DAILY_VOICE", "1")
    with g.websocket_connect("/api/voice/live") as ws:
        ev = json.loads(ws.receive_text())
    assert ev["type"] == "error" and ev["code"] == "limit" and "1 voice wish." in ev["message"]
