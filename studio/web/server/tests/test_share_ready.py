"""Hackathon share-link fixes: large exports stream (no Content-Length, so Cloud Run's 32 MiB cap does not apply),
gzip for viewer files, pre-compressed SPA assets, and the sign-in page's SHARE_URL link."""
from __future__ import annotations

import gzip
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import make_settings
from test_api_flow import _benchmark_to_ready

BIG = 40 * 1024 * 1024 + 123  # above Cloud Run's 32 MiB cap for a response with a declared length


@pytest.fixture
def built(authed):
    """A finished project (fake builder) whose store behaves like GCS: no local paths, so big files stream."""
    pid = _benchmark_to_ready(authed)
    store = authed.app.state.svc.store
    store.local_path = lambda key: None  # type: ignore[method-assign]
    return pid, store


def _big_zip(store, pid: str) -> bytes:
    data = os.urandom(BIG)
    store.write_bytes(f"projects/{pid}/v1/export.zip", data, "application/zip")
    return data


def test_large_export_streams_without_content_length(built, authed, client):
    pid, store = built
    data = _big_zip(store, pid)
    want = hashlib.sha256(data).hexdigest()
    with authed.stream("GET", f"/api/projects/{pid}/v/1/export") as r:
        assert r.status_code == 200
        assert "content-length" not in r.headers  # chunked: no 32 MiB cap on Cloud Run
        assert "attachment" in r.headers["content-disposition"]
        assert r.headers["content-type"] == "application/zip"
        assert "content-encoding" not in r.headers
        h = hashlib.sha256()
        n = 0
        for chunk in r.iter_bytes():
            h.update(chunk)
            n += len(chunk)
    assert n == BIG and h.hexdigest() == want
    # HEAD still tells the size (no body travels)
    hd = authed.head(f"/api/projects/{pid}/v/1/export")
    assert hd.status_code == 200 and hd.headers["content-length"] == str(BIG) and hd.content == b""
    # the read-only share link streams the same way
    tok = authed.post(f"/api/projects/{pid}/share").json()["token"]
    with client.stream("GET", f"/api/share/{tok}/v/1/export") as r:
        assert r.status_code == 200 and "content-length" not in r.headers
        h2 = hashlib.sha256()
        for chunk in r.iter_bytes():
            h2.update(chunk)
    assert h2.hexdigest() == want


def test_small_files_keep_content_length_and_etag(built, authed):
    pid, _ = built
    r = authed.get(f"/api/projects/{pid}/v/1/files/build.json", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200 and r.headers["content-length"] == str(len(r.content))
    assert "content-encoding" not in r.headers and r.headers["vary"] == "Accept-Encoding"
    assert authed.get(f"/api/projects/{pid}/v/1/files/build.json", headers={
        "Accept-Encoding": "identity", "If-None-Match": r.headers["etag"]}).status_code == 304


def test_viewer_files_are_gzipped_when_accepted(built, authed):
    pid, _ = built
    plain = authed.get(f"/api/projects/{pid}/v/1/files/build.json", headers={"Accept-Encoding": "identity"})
    with authed.stream("GET", f"/api/projects/{pid}/v/1/files/build.json", headers={"Accept-Encoding": "gzip"}) as r:
        raw = b"".join(r.iter_raw())
        assert r.headers["content-encoding"] == "gzip" and r.headers["vary"] == "Accept-Encoding"
        assert r.headers["content-type"] == "application/json"
        etag = r.headers["etag"]
    assert etag.endswith('-gz"') and etag != plain.headers["etag"]
    assert gzip.decompress(raw) == plain.content and len(raw) < len(plain.content)
    # a cached gzip copy revalidates against its own ETag
    assert authed.get(f"/api/projects/{pid}/v/1/files/build.json", headers={
        "Accept-Encoding": "gzip", "If-None-Match": etag}).status_code == 304
    # downloads (attachments) are never re-encoded
    d = authed.get(f"/api/projects/{pid}/v/1/files/build.json?download=1", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in d.headers and d.content == plain.content
    # the 3D model is compressible too; PNGs are not
    g = authed.get(f"/api/projects/{pid}/v/1/files/cad/assembly.glb", headers={"Accept-Encoding": "gzip, br"})
    assert g.content[:4] == b"glTF"
    p = authed.get(f"/api/projects/{pid}").json()
    png = authed.get(p["concepts"][0]["render_url"], headers={"Accept-Encoding": "gzip"})
    assert png.content[:4] == b"\x89PNG" and "content-encoding" not in png.headers


def test_gzip_quality_zero_means_not_accepted(built, authed):
    pid, _ = built
    r = authed.get(f"/api/projects/{pid}/v/1/files/build.json", headers={"Accept-Encoding": "gzip;q=0, identity"})
    assert "content-encoding" not in r.headers


@pytest.fixture
def spa(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text('<!doctype html><div id="root"></div>')
    js = b"console.log('wish labs');" * 200
    (d / "assets" / "index-abc123.js").write_bytes(js)
    (d / "assets" / "index-abc123.js.gz").write_bytes(gzip.compress(js))
    (d / "assets" / "index-abc123.js.br").write_bytes(b"not-really-brotli")
    (d / "assets" / "plain-1.css").write_text("body{color:red}")
    app = create_app(make_settings(tmp_path, static_dir=d))
    with TestClient(app, follow_redirects=False) as c:
        yield c, js


def test_precompressed_assets(spa):
    c, js = spa
    with c.stream("GET", "/studio/assets/index-abc123.js", headers={"Accept-Encoding": "gzip, deflate, br"}) as r:
        assert r.status_code == 200 and r.headers["content-encoding"] == "br"
        assert "javascript" in r.headers["content-type"] and r.headers["vary"] == "Accept-Encoding"
        assert "immutable" in r.headers["cache-control"]
        assert b"".join(r.iter_raw()) == b"not-really-brotli"
    with c.stream("GET", "/studio/assets/index-abc123.js", headers={"Accept-Encoding": "gzip"}) as r:
        assert r.headers["content-encoding"] == "gzip"
        assert gzip.decompress(b"".join(r.iter_raw())) == js
    r = c.get("/studio/assets/index-abc123.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers and r.content == js and r.headers["vary"] == "Accept-Encoding"
    r = c.get("/studio/assets/plain-1.css", headers={"Accept-Encoding": "gzip, br"})
    assert "content-encoding" not in r.headers and "vary" not in r.headers and r.text == "body{color:red}"
    assert c.head("/studio/assets/index-abc123.js", headers={"Accept-Encoding": "br"}).status_code == 200
    # index.html is never cached and not encoded
    r = c.get("/studio/s/s_abc", headers={"Accept-Encoding": "gzip, br"})
    assert '<div id="root">' in r.text and r.headers["cache-control"] == "no-cache"


def test_chat_stream_is_not_compressed(authed):
    pid = authed.post("/api/projects", json={}).json()["id"]
    with authed.stream("POST", f"/api/projects/{pid}/chat", json={"text": "A calm desk timer that glows"},
                       headers={"Accept-Encoding": "gzip, br"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in r.headers
        events = [line for line in r.iter_lines() if line.startswith("event: ")]
    assert events[0] == "event: turn.start" and events[-1] == "event: done"


@pytest.mark.parametrize("value,expected", [
    ("https://wish.plugbook.si/studio/s/s_abc123", "https://wish.plugbook.si/studio/s/s_abc123"),
    ("/studio/s/s_abc123", "/studio/s/s_abc123"),
    ("", None),
    ("javascript:alert(1)", None),
    ("http://insecure.example/studio/s/x", None),
    ("//evil.example/x", None),
    ('https://ok.example/"><script>', None),
])
def test_session_share_url(client, monkeypatch, value, expected):
    monkeypatch.setenv("SHARE_URL", value)
    j = client.get("/api/session").json()
    assert j["authenticated"] is False and j["share_url"] == expected
