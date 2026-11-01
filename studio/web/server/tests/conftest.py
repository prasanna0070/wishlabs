from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from app.config import Settings  # noqa: E402

KEY = "test-access-key-123"


def make_settings(tmp_path: Path, **kw) -> Settings:
    base = dict(store_dir=tmp_path / "store", builder="fake", agent="scripted", access_key=KEY, photos=False,
                fake_build_seconds=0.05, fake_use_examples=False, static_dir=None, cookie_secure=False)
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authed(client):
    r = client.post("/api/login", json={"key": KEY})
    assert r.status_code == 200, r.text
    return client


def sse(client, method: str, url: str, **kw) -> list[tuple[str, dict]]:
    """Run an SSE request to completion and return [(event, data)]."""
    out = []
    with client.stream(method, url, **kw) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        ev = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                out.append((ev, json.loads(line[6:])))
    return out


def events_named(evs, name):
    return [d for e, d in evs if e == name]
