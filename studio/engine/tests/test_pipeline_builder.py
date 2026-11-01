"""Builder service (studio/builder/app.py): routes, the 2-build limit, and the GCS mirror order with a fake bucket."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

APP = Path(__file__).resolve().parents[2] / "builder" / "app.py"


@pytest.fixture(scope="module")
def app_mod(tmp_path_factory):
    os.environ.pop("BUCKET", None)
    os.environ["WORK_DIR"] = str(tmp_path_factory.mktemp("work"))
    spec = importlib.util.spec_from_file_location("gs_builder_app", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def client(app_mod):
    return TestClient(app_mod.app)


def test_health(client):
    r = client.get("/health").json()
    assert r["ok"] and r["max_builds"] == 2 and r["builds_running"] == 0 and "prusa-slicer" in r["tools"]


def test_validate_and_build_reject_bad_bodies(client):
    assert client.post("/validate", content="nope").status_code == 422
    r = client.post("/build", json={"project_id": "../etc", "version": 1, "spec": {}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid"
    assert client.post("/build", json={"project_id": "p1", "version": 0, "spec": {}}).status_code == 422
    assert client.post("/build", json={"project_id": "p1", "version": 1, "spec": "x"}).status_code == 422


def test_third_build_gets_429(app_mod, client):
    app_mod._running = app_mod.MAX_BUILDS
    try:
        r = client.post("/build", json={"project_id": "p1", "version": 1, "spec": {}})
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    finally:
        app_mod._running = 0


def test_local_build_runs_the_engine_and_reports_an_invalid_spec(client):
    r = client.post("/build", json={"project_id": "p_test", "version": 3, "spec": {"name": "x"}, "brief": {"title": "t"}})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["state"] == "error" and s["project_id"] == "p_test" and s["version"] == 3 and s["gcs"] is None
    out = Path(s["local_out"])
    st = json.loads((out / "status.json").read_text())
    assert st["state"] == "error" and st["stages"][0]["id"] == "normalize" and st["stages"][0]["state"] == "failed"
    assert (out / "logs" / "normalize.log").exists()


class FakeBlob:
    def __init__(self, log, name):
        self.log, self.name, self.cache_control = log, name, None

    def upload_from_filename(self, path, content_type=None, timeout=None):
        self.log.append((self.name, content_type, Path(path).read_bytes()))


class FakeBucket:
    def __init__(self):
        self.log = []

    def blob(self, name):
        return FakeBlob(self.log, name)


def test_mirror_order_draft_before_status_and_terminal_status_last(app_mod, tmp_path):
    out = tmp_path / "out"
    (out / "cad").mkdir(parents=True)
    (out / "firmware" / ".pio" / "build").mkdir(parents=True)
    (out / "firmware" / ".pio" / "build" / "x.o").write_text("obj")
    m = app_mod.Mirror(out, "projects/p1/v2")
    m._bucket = FakeBucket()
    names = lambda: [n for n, _c, _b in m._bucket.log]  # noqa: E731

    async def scenario():
        (out / "cad" / "draft.glb").write_bytes(b"draft1")
        (out / "status.json").write_text(json.dumps({"state": "running", "stages": [{"id": "cad", "state": "running"}],
                                                     "draft": "cad/draft.glb", "draft_rev": 1}))
        last, ls = await app_mod._tick(out, m, None, {})
        assert names() == ["projects/p1/v2/cad/draft.glb", "projects/p1/v2/status.json"]
        # same status: nothing uploaded
        last, ls = await app_mod._tick(out, m, last, ls)
        assert len(names()) == 2
        # cad done: its files go up, then the status
        (out / "cad" / "assembly.glb").write_bytes(b"final")
        (out / "status.json").write_text(json.dumps({"state": "running", "stages": [{"id": "cad", "state": "done"}],
                                                     "draft": "cad/draft.glb", "draft_rev": 1}))
        last, ls = await app_mod._tick(out, m, last, ls)
        assert "projects/p1/v2/cad/assembly.glb" in names() and names()[-1] == "projects/p1/v2/status.json"
        assert not any(".pio" in n for n in names())
        assert names().count("projects/p1/v2/cad/draft.glb") == 1  # unchanged draft is not re-sent
        # a terminal status is held back until the final sync
        n0 = len(names())
        (out / "status.json").write_text(json.dumps({"state": "done", "stages": [{"id": "cad", "state": "done"}]}))
        await app_mod._tick(out, m, last, ls)
        assert "projects/p1/v2/status.json" not in names()[n0:]
        await m.sync()
        await m.put_status()
        assert names()[-1] == "projects/p1/v2/status.json"

    asyncio.run(scenario())
    ctypes = {n.rsplit("/", 1)[-1]: c for n, c, _ in m._bucket.log}
    assert ctypes["assembly.glb"] == "model/gltf-binary" and ctypes["status.json"] == "application/json"


def test_content_types(app_mod):
    ct = lambda n: app_mod.content_type(Path(n))  # noqa: E731
    assert ct("pcb/gerbers/backplane-F_Cu.gbr").startswith("text/plain")
    assert ct("pcb/backplane.kicad_pcb").startswith("text/plain") and ct("cad/components/PRINT_Shell.brep").startswith("text/plain")
    assert ct("cad/assembly.glb") == "model/gltf-binary" and ct("BUILD_GUIDE.md") == "text/markdown" and ct("firmware/firmware.bin") == "application/octet-stream"
