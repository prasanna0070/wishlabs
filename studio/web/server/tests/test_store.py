from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.store import LocalStore, StoreError


def test_write_read_roundtrip(tmp_path):
    s = LocalStore(tmp_path)
    s.write_bytes("projects/p_a/v1/x.bin", b"hello")
    assert s.read_bytes("projects/p_a/v1/x.bin") == b"hello"
    assert s.read_bytes("projects/p_a/missing") is None
    s.write_json("projects/p_a/project.json", {"a": 1})
    assert s.read_json("projects/p_a/project.json") == {"a": 1}
    st = s.stat("projects/p_a/v1/x.bin")
    assert st.size == 5 and st.etag
    assert s.exists("projects/p_a/v1/x.bin")
    # no temp files left behind
    assert not [p for p in (tmp_path / "projects/p_a/v1").iterdir() if p.name.startswith(".tmp-")]


def test_escape_rejected(tmp_path):
    s = LocalStore(tmp_path / "root")
    with pytest.raises(StoreError):
        s.write_bytes("../evil", b"x")


def test_list_copy_put(tmp_path):
    s = LocalStore(tmp_path / "st")
    for k in ("projects/p1/project.json", "projects/p2/project.json", "projects/p1/v1/a/b.txt"):
        s.write_bytes(k, b"1")
    assert s.list_dirs("projects") == ["p1", "p2"]
    assert "projects/p1/v1/a/b.txt" in s.list_keys("projects/p1")
    assert s.copy_prefix("projects/p1/v1", "projects/p1/v2") == 1
    assert s.read_bytes("projects/p1/v2/a/b.txt") == b"1"
    src = tmp_path / "tree"
    (src / "d").mkdir(parents=True)
    (src / "d" / "f.json").write_text("{}")
    (src / ".hidden").write_text("x")
    assert s.put_tree(src, "projects/p3/v1", lambda rel: not rel.startswith(".")) == 1
    assert s.read_json("projects/p3/v1/d/f.json") == {}
    s.delete("projects/p3/v1/d/f.json")
    assert not s.exists("projects/p3/v1/d/f.json")


def test_update_json_is_atomic_under_threads(tmp_path):
    s = LocalStore(tmp_path)

    def bump(d):
        d["n"] = d.get("n", 0) + 1

    ts = [threading.Thread(target=lambda: [s.update_json("c.json", bump, default={}) for _ in range(20)])
          for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert s.read_json("c.json")["n"] == 100


def test_update_json_exception_leaves_value(tmp_path):
    s = LocalStore(tmp_path)
    s.write_json("x.json", {"v": 1})

    def boom(d):
        d["v"] = 2
        raise ValueError("no")

    with pytest.raises(ValueError):
        s.update_json("x.json", boom)
    assert s.read_json("x.json") == {"v": 1}


_BUMP = """
import sys
from pathlib import Path
from app.store import LocalStore

st = LocalStore(Path(sys.argv[1]))
for _ in range(50):
    st.update_json("n.json", lambda d: {"n": d["n"] + 1}, {"n": 0})
"""


def test_update_json_is_atomic_across_processes(tmp_path):
    server = Path(__file__).resolve().parents[1]
    procs = [
        subprocess.Popen([sys.executable, "-c", _BUMP, str(tmp_path)], cwd=server, stderr=subprocess.PIPE)
        for _ in range(4)
    ]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err.decode()
    assert LocalStore(tmp_path).read_json("n.json") == {"n": 200}
