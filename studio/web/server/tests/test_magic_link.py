"""Magic sign-in link (21 Nov): the Buildathon form takes only a URL, so one signed link signs anyone in."""
from __future__ import annotations


def _token(client) -> str:
    return client.app.state.signer.magic_token("jurors")


def test_magic_link_signs_in_and_lands_on_the_studio(client):
    r = client.get(f"/api/magic/{_token(client)}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/studio/"
    assert "gs_session=" in r.headers.get("set-cookie", "")
    assert client.get("/api/me").status_code == 200


def test_many_people_can_use_the_same_link(client, settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    tok = _token(client)
    with TestClient(create_app(settings)) as other:
        r = other.get(f"/api/magic/{tok}", follow_redirects=False)
        assert r.status_code == 302 and "gs_session=" in r.headers.get("set-cookie", "")
    r = client.get(f"/api/magic/{tok}", follow_redirects=False)
    assert r.status_code == 302 and "gs_session=" in r.headers.get("set-cookie", "")


def test_wrong_token_signs_nobody_in(client):
    r = client.get("/api/magic/" + "0" * 32, follow_redirects=False)
    assert r.status_code == 302 and "gs_session=" not in r.headers.get("set-cookie", "")
    assert client.get("/api/me").status_code == 401


def test_magic_links_can_be_switched_off(client, monkeypatch):
    monkeypatch.setenv("MAGIC_LINKS", "off")
    r = client.get(f"/api/magic/{_token(client)}", follow_redirects=False)
    assert r.status_code == 404 and "gs_session=" not in r.headers.get("set-cookie", "")
