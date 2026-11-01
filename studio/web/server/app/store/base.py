"""Store interface: a flat key/value object store with atomic writes and compare-and-swap JSON updates.

Keys are '/'-separated relative paths such as 'projects/p_x/project.json'. Both backends are synchronous;
async code calls them through asyncio.to_thread.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol


class StoreError(RuntimeError):
    pass


class ConflictError(StoreError):
    """A compare-and-swap update lost too many races."""


@dataclass
class ObjectStat:
    size: int
    etag: str
    updated: float          # unix seconds


class Store(Protocol):
    kind: str

    def read_bytes(self, key: str) -> bytes | None: ...
    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def stat(self, key: str) -> ObjectStat | None: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def list_dirs(self, prefix: str) -> list[str]: ...
    def iter_chunks(self, key: str, chunk_size: int = 1 << 20) -> Iterator[bytes]: ...
    def copy_prefix(self, src: str, dst: str) -> int: ...
    def put_tree(self, local_dir: Path, prefix: str, only: Callable[[str], bool] | None = None) -> int: ...
    def update_json(self, key: str, fn: Callable[[Any], Any], default: Any = None) -> Any: ...
    def local_path(self, key: str) -> Path | None: ...


class JsonMixin:
    def read_json(self, key: str) -> Any:
        raw = self.read_bytes(key)  # type: ignore[attr-defined]
        if raw is None:
            return None
        return json.loads(raw)

    def write_json(self, key: str, obj: Any) -> None:
        data = json.dumps(obj, ensure_ascii=False, indent=1).encode()
        self.write_bytes(key, data, "application/json")  # type: ignore[attr-defined]
