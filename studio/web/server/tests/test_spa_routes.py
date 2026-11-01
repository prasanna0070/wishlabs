"""The SPA under /studio (wish.plugbook.si/studio), /api at the root, and redirects from the old run.app routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import _studio_url, create_app
from conftest import KEY, make_settings

INDEX = '<!doctype html><html><head><title>Wish Labs</title></head><body><div id="root"></div></body></html>'


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX)
    (d / "assets" / "index-abc123.js").write_text("console.log('spa')")
    (d / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("not for the web")
    return d


@pytest.fixture
def spa(tmp_path, dist):
    app = create_app(make_settings(tmp_path, static_dir=dist))
    with TestClient(app, follow_redirects=False) as c:
        yield c


def test_studio_serves_index_for_spa_routes(spa):
    for path in ["/studio/", "/studio/login", "/studio/p/p_123456789012", "/studio/p/p_123456789012/parts",
                 "/studio/s/s_abc/build", "/studio/index.html", "/studio/unknown/deep/path?wish=x"]:
        r = spa.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith("text/html"), path
        assert '<div id="root">' in r.text, path
        assert r.headers["cache-control"] == "no-cache", path


def test_studio_serves_static_files(spa):
    r = spa.get("/studio/assets/index-abc123.js")
    assert r.status_code == 200 and "spa" in r.text
    assert "immutable" in r.headers["cache-control"]
    r = spa.get("/studio/favicon.svg")
    assert r.status_code == 200 and r.text == "<svg/>"
    assert r.headers["cache-control"] == "public, max-age=300"
    assert spa.head("/studio/assets/index-abc123.js").status_code == 200


def test_missing_bundle_is_404_not_index(spa):
    r = spa.get("/studio/assets/index-old999.js")
    assert r.status_code == 404 and '<div id="root">' not in r.text


def test_no_path_traversal_out_of_dist(spa):
    r = spa.get("/studio/%2e%2e/secret.txt")
    assert "not for the web" not in r.text


def test_studio_without_slash_is_the_spa_home(spa):
    # React Router (basename /studio) links home as /studio, so it must not need a redirect hop
    for path in ["/studio", "/studio?wish=a%20cloud%20lamp"]:
        r = spa.get(path)
        assert r.status_code == 200, path
        assert '<div id="root">' in r.text and r.headers["cache-control"] == "no-cache"
    assert spa.head("/studio").status_code == 200


@pytest.mark.parametrize("old,new", [
    ("/", "/studio/"),
    ("/?wish=a+heart", "/studio/?wish=a+heart"),
    ("/login", "/studio/login"),
    ("/p/p_123456789012", "/studio/p/p_123456789012"),
    ("/p/p_123456789012/parts", "/studio/p/p_123456789012/parts"),
    ("/s/s_abcdefabcdefabcdefabcdef", "/studio/s/s_abcdefabcdefabcdefabcdef"),
    ("/s/s_abc/code?x=1", "/studio/s/s_abc/code?x=1"),
    ("/favicon.svg", "/studio/favicon.svg"),
])
def test_old_routes_redirect_to_studio(spa, old, new):
    r = spa.get(old)
    assert r.status_code == 307, old
    assert r.headers["location"] == new
    assert spa.head(old).status_code == 307


def test_redirect_target_is_never_protocol_relative():
    assert _studio_url("/evil.example/x", "") == "/studio/evil.example/x"
    assert _studio_url("//evil.example", "a=1") == "/studio/evil.example?a=1"
    assert _studio_url("", "") == "/studio/"


def test_api_stays_at_root(spa):
    r = spa.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    for method in ("GET", "HEAD", "POST"):
        r = spa.request(method, "/api/no-such-route")
        assert r.status_code == 404, method
        if method != "HEAD":
            assert r.json()["error"]["code"] == "not_found"
    assert spa.get("/api/projects").status_code == 401
    assert spa.get("/studio/api/health").headers["content-type"].startswith("text/html")  # the SPA, not the API


def test_session_cookie_path_is_root(spa):
    r = spa.post("/api/login", json={"key": KEY})
    assert r.status_code == 200
    assert "Path=/;" in r.headers["set-cookie"] or r.headers["set-cookie"].rstrip().endswith("Path=/")
    assert spa.get("/api/me").status_code == 200


def test_share_link_points_under_studio(spa):
    spa.post("/api/login", json={"key": KEY})
    pid = spa.post("/api/projects", json={}).json()["id"]
    j = spa.post(f"/api/projects/{pid}/share").json()
    assert j["url"] == f"/studio/s/{j['token']}"
    assert spa.get(j["url"]).status_code == 200      # the SPA route answers the share link


def test_without_a_built_spa(client):
    for path in ("/studio/", "/studio"):
        r = client.get(path)
        assert r.status_code == 200 and "Wish Labs" in r.text and "Gadget Studio" not in r.text
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/studio/"


def test_branding(client):
    from app.agent.prompt import ROLE

    assert client.app.title == "Wish Labs"
    assert "Wish Labs" in ROLE and "Gadget Studio" not in ROLE
    assert client.get("/api/openapi.json").json()["info"]["title"] == "Wish Labs"
