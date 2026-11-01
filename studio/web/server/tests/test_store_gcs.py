"""GCS store against a real bucket. Skipped unless GSTUDIO_GCS_TEST_BUCKET is set. Uses a unique `_webtest/`
prefix and deletes everything it wrote.  GSTUDIO_GCS_TEST_BUCKET=<bucket> python -m pytest -m gcs tests/test_store_gcs.py"""
from __future__ import annotations

import os
import threading
import uuid

import pytest

pytestmark = [pytest.mark.gcs,
              pytest.mark.skipif(not os.environ.get("GSTUDIO_GCS_TEST_BUCKET"), reason="set GSTUDIO_GCS_TEST_BUCKET")]


@pytest.fixture
def gcs():
    from app.store.gcs import GCSStore

    s = GCSStore(os.environ["GSTUDIO_GCS_TEST_BUCKET"])
    root = f"_webtest/{uuid.uuid4().hex[:10]}"
    yield s, root
    for k in s.list_keys(root):
        s.delete(k)
    assert s.list_keys(root) == []


def test_gcs_roundtrip(gcs, tmp_path):
    s, root = gcs
    s.write_json(f"{root}/projects/p1/project.json", {"a": 1})
    s.write_bytes(f"{root}/projects/p1/v1/x.bin", b"hello")
    s.write_bytes(f"{root}/projects/p2/project.json", b"{}")
    assert s.read_json(f"{root}/projects/p1/project.json") == {"a": 1}
    assert s.read_bytes(f"{root}/nope") is None
    st = s.stat(f"{root}/projects/p1/v1/x.bin")
    assert st.size == 5 and st.etag
    assert s.list_dirs(f"{root}/projects") == ["p1", "p2"]
    assert b"".join(s.iter_chunks(f"{root}/projects/p1/v1/x.bin")) == b"hello"
    assert s.copy_prefix(f"{root}/projects/p1/v1", f"{root}/projects/p1/v2") == 1
    assert s.read_bytes(f"{root}/projects/p1/v2/x.bin") == b"hello"
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.glb").write_bytes(b"glTF")
    assert s.put_tree(tmp_path, f"{root}/tree") == 1
    assert s.bucket.get_blob(f"{root}/tree/d/f.glb").content_type == "model/gltf-binary"


def test_gcs_cas_under_contention(gcs):
    s, root = gcs
    key = f"{root}/counter.json"

    def bump(d):
        d["n"] = (d or {}).get("n", 0) + 1
        return d

    ts = [threading.Thread(target=lambda: [s.update_json(key, bump, default={}) for _ in range(5)]) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert s.read_json(key)["n"] == 20
