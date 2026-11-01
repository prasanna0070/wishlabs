"""Small shared helpers: ids, time, hashing, safe paths."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

_B32 = "abcdefghijkmnpqrstuvwxyz23456789"   # no l, o, 0, 1


def new_id(prefix: str, n: int = 10) -> str:
    return prefix + "_" + "".join(secrets.choice(_B32) for _ in range(n))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def spec_hash(spec: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(spec)).hexdigest()


def safe_rel_path(path: str) -> str | None:
    """Return a normalized relative path, or None when it tries to escape."""
    if not path or "\\" in path or "\x00" in path or path.startswith("/"):
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


CONTENT_TYPES = {
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".csv": "text/csv; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}
_TEXT_EXT = {".py", ".cpp", ".c", ".h", ".hpp", ".ini", ".cfg", ".toml", ".yaml", ".yml", ".log",
             ".kicad_pro", ".kicad_sch", ".kicad_pcb", ".net", ".gbr", ".drl", ".gtl", ".gbl", ".gto",
             ".gbo", ".gts", ".gbs", ".gm1", ".ino", ".sh", ".gcode"}


def content_type_for(path: str) -> str:
    lower = path.lower()
    dot = lower.rfind(".")
    ext = lower[dot:] if dot >= 0 else ""
    if ext in CONTENT_TYPES:
        return CONTENT_TYPES[ext]
    if ext in _TEXT_EXT or lower.endswith(("makefile", "license", "readme")):
        return "text/plain; charset=utf-8"
    return "application/octet-stream"
