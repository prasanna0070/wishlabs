"""Google Cloud Storage store. Object writes are atomic by nature; JSON updates use generation preconditions
(optimistic compare-and-swap with retries)."""
from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from .base import ConflictError, JsonMixin, ObjectStat


class GCSStore(JsonMixin):
    kind = "gcs"

    def __init__(self, bucket: str, project: str | None = None, client=None):
        from google.cloud import storage  # imported lazily so tests do not need it

        self._storage = storage
        self.client = client or storage.Client(project=project)
        self.bucket_name = bucket.removeprefix("gs://").strip("/")
        self.bucket = self.client.bucket(self.bucket_name)

    def local_path(self, key: str) -> Path | None:
        return None

    def read_bytes(self, key: str) -> bytes | None:
        from google.api_core.exceptions import NotFound

        try:
            return self.bucket.blob(key).download_as_bytes()
        except NotFound:
            return None

    def _read_with_generation(self, key: str) -> tuple[bytes | None, int]:
        from google.api_core.exceptions import NotFound

        blob = self.bucket.blob(key)
        try:
            blob.reload()
        except NotFound:
            return None, 0
        try:
            data = blob.download_as_bytes(if_generation_match=blob.generation)
        except Exception:  # raced with a writer; caller retries
            return self._read_with_generation(key)
        return data, int(blob.generation)

    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        blob = self.bucket.blob(key)
        if key.endswith(("project.json", "status.json")):
            blob.cache_control = "no-store"
        blob.upload_from_string(data, content_type=content_type or "application/octet-stream")

    def stat(self, key: str) -> ObjectStat | None:
        blob = self.bucket.get_blob(key)
        if blob is None:
            return None
        return ObjectStat(size=int(blob.size or 0), etag=str(blob.generation),
                          updated=blob.updated.timestamp() if blob.updated else 0.0)

    def exists(self, key: str) -> bool:
        return self.bucket.blob(key).exists()

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self.bucket.blob(key).delete()
        except NotFound:
            pass

    def list_keys(self, prefix: str) -> list[str]:
        p = prefix.rstrip("/") + "/" if prefix else ""
        return sorted(b.name for b in self.client.list_blobs(self.bucket_name, prefix=p))

    def list_dirs(self, prefix: str) -> list[str]:
        p = prefix.rstrip("/") + "/" if prefix else ""
        it = self.client.list_blobs(self.bucket_name, prefix=p, delimiter="/")
        for _ in it:   # prefixes are only filled after iteration
            pass
        return sorted(x[len(p):].rstrip("/") for x in it.prefixes)

    def iter_chunks(self, key: str, chunk_size: int = 1 << 20) -> Iterator[bytes]:
        blob = self.bucket.blob(key)
        with blob.open("rb", chunk_size=chunk_size) as f:
            while True:
                b = f.read(chunk_size)
                if not b:
                    return
                yield b

    def copy_prefix(self, src: str, dst: str) -> int:
        s = src.rstrip("/") + "/"
        d = dst.rstrip("/") + "/"
        n = 0
        for b in self.client.list_blobs(self.bucket_name, prefix=s):
            self.bucket.copy_blob(b, self.bucket, d + b.name[len(s):])
            n += 1
        return n

    def put_tree(self, local_dir: Path, prefix: str, only: Callable[[str], bool] | None = None) -> int:
        from ..util import content_type_for

        local_dir = Path(local_dir)
        n = 0
        for p in sorted(local_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(local_dir).as_posix()
            if only and not only(rel):
                continue
            blob = self.bucket.blob(f"{prefix.rstrip('/')}/{rel}")
            blob.upload_from_filename(str(p), content_type=content_type_for(rel))
            n += 1
        return n

    def update_json(self, key: str, fn: Callable[[Any], Any], default: Any = None, retries: int = 12) -> Any:
        from google.api_core.exceptions import PreconditionFailed

        for attempt in range(retries):
            raw, gen = self._read_with_generation(key)
            cur = json.loads(raw) if raw is not None else copy.deepcopy(default)
            work = copy.deepcopy(cur)
            res = fn(work)
            new = work if res is None else res
            blob = self.bucket.blob(key)
            blob.cache_control = "no-store"
            try:
                blob.upload_from_string(json.dumps(new, ensure_ascii=False, indent=1).encode(),
                                        content_type="application/json", if_generation_match=gen)
                return new
            except PreconditionFailed:
                time.sleep(min(2.0, 0.05 * (2 ** attempt)) * (0.5 + random.random()))
        raise ConflictError(f"too many concurrent updates to {key}")
