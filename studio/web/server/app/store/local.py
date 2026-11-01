"""Local filesystem store for dev. Atomic writes (temp file + fsync + rename), flock-guarded JSON updates."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

from .base import JsonMixin, ObjectStat, StoreError


class LocalStore(JsonMixin):
    kind = "local"

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- paths
    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if p != self.root and self.root not in p.parents:
            raise StoreError(f"key escapes store: {key}")
        return p

    def local_path(self, key: str) -> Path | None:
        return self._path(key)

    # -- bytes
    def read_bytes(self, key: str) -> bytes | None:
        p = self._path(key)
        try:
            return p.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return None

    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=p.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def stat(self, key: str) -> ObjectStat | None:
        p = self._path(key)
        try:
            st = p.stat()
        except FileNotFoundError:
            return None
        if not p.is_file():
            return None
        etag = hashlib.sha1(f"{st.st_size}:{st.st_mtime_ns}:{st.st_ino}".encode()).hexdigest()[:20]
        return ObjectStat(size=st.st_size, etag=etag, updated=st.st_mtime)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def list_keys(self, prefix: str) -> list[str]:
        base = self._path(prefix.rstrip("/")) if prefix.strip("/") else self.root
        if not base.exists():
            return []
        out = []
        for p in base.rglob("*"):
            if p.is_file() and not p.name.startswith(".tmp-") and not p.name.endswith(".lock"):
                out.append(p.relative_to(self.root).as_posix())
        return sorted(out)

    def list_dirs(self, prefix: str) -> list[str]:
        base = self._path(prefix.rstrip("/")) if prefix.strip("/") else self.root
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())

    def iter_chunks(self, key: str, chunk_size: int = 1 << 20) -> Iterator[bytes]:
        with open(self._path(key), "rb") as f:
            while True:
                b = f.read(chunk_size)
                if not b:
                    return
                yield b

    def copy_prefix(self, src: str, dst: str) -> int:
        s = self._path(src.rstrip("/"))
        d = self._path(dst.rstrip("/"))
        if not s.is_dir():
            return 0
        n = 0
        for p in s.rglob("*"):
            if p.is_file() and not p.name.startswith(".tmp-") and not p.name.endswith(".lock"):
                t = d / p.relative_to(s)
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, t)
                n += 1
        return n

    def put_tree(self, local_dir: Path, prefix: str, only: Callable[[str], bool] | None = None) -> int:
        local_dir = Path(local_dir)
        n = 0
        for p in local_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(local_dir).as_posix()
            if only and not only(rel):
                continue
            t = self._path(f"{prefix.rstrip('/')}/{rel}")
            if t == p.resolve():
                continue
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, t)
            n += 1
        return n

    # -- CAS json
    def _thread_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def update_json(self, key: str, fn: Callable[[Any], Any], default: Any = None) -> Any:
        """Read-modify-write under a thread lock and an flock. fn gets a deep copy and returns the new value
        (or mutates and returns None). Returns what was written."""
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        lock_path = p.with_name(p.name + ".lock")
        with self._thread_lock(key):
            with open(lock_path, "a+") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    raw = self.read_bytes(key)
                    cur = json.loads(raw) if raw is not None else copy.deepcopy(default)
                    work = copy.deepcopy(cur)
                    res = fn(work)
                    new = work if res is None else res
                    self.write_json(key, new)
                    return new
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
