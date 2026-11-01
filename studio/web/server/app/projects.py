"""Project repository: project.json CRUD plus the small pure helpers that edit a project dict."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .util import new_id, now_iso

SCHEMA = "gstudio-web-project/1"
STAGES = ("clarify", "concepts", "brief", "building", "ready", "error")


class NotFound(Exception):
    pass


def pkey(pid: str, rel: str = "project.json") -> str:
    return f"projects/{pid}/{rel}"


def vkey(pid: str, n: int, rel: str) -> str:
    return f"projects/{pid}/v{n}/{rel}"


def blank_project(name: str | None = None, mode: str | None = None) -> dict:
    t = now_iso()
    return {
        "schema": SCHEMA,
        "id": new_id("p", 12),
        "name": (name or "Untitled gadget").strip()[:120] or "Untitled gadget",
        "name_source": "user" if (name or "").strip() else "auto",
        "created": t,
        "updated": t,
        "deleted": False,
        "stage": "clarify",
        "mode": mode if mode in ("pro", "fast") else "pro",
        "turn": None,
        "questions_asked": 0,
        "chat": [],
        "concepts": [],
        "selected_concept": None,
        "brief": None,
        "brief_history": [],
        "versions": [],
        "current_version": None,
        "checklist": {"assembly": {}, "wiring": {}},
        "order_requests": [],
        "share": None,
        "usage": None,
    }


def make_message(role: str, kind: str, text: str, payload: Any = None) -> dict:
    return {"id": new_id("m"), "role": role, "kind": kind, "text": text, "payload": payload, "ts": now_iso()}


def find_message(p: dict, mid: str) -> dict | None:
    return next((m for m in p["chat"] if m["id"] == mid), None)


def last_message(p: dict, kind: str, role: str = "assistant") -> dict | None:
    for m in reversed(p["chat"]):
        if m["kind"] == kind and m["role"] == role:
            return m
    return None


def find_concept(p: dict, cid: str | None) -> dict | None:
    return next((c for c in p.get("concepts", []) if c["id"] == cid), None) if cid else None


def find_version(p: dict, n: int | None) -> dict | None:
    return next((v for v in p.get("versions", []) if v["n"] == n), None) if n is not None else None


def summary_of(p: dict) -> dict:
    thumb = None
    cv = find_version(p, p.get("current_version"))
    if cv and cv.get("state") == "done" and cv.get("thumb"):
        thumb = f"/api/projects/{p['id']}/v/{cv['n']}/files/{cv['thumb']}"
    if not thumb:
        c = find_concept(p, p.get("selected_concept"))
        if c:
            thumb = c.get("photo_url") or c.get("render_url")
    return {"id": p["id"], "name": p["name"], "created": p["created"], "updated": p["updated"],
            "stage": p["stage"], "current_version": p.get("current_version"), "thumb_url": thumb,
            **measured_waits(p)}


def measured_waits(p: dict) -> dict:
    """MIN-45: the last finished build's and concept turn's wall time in seconds, measured from this project."""
    from .util import parse_iso

    def secs(a, b):
        ta, tb = parse_iso(a), parse_iso(b)
        return round((tb - ta).total_seconds()) if ta and tb and tb >= ta else None

    build = None
    for v in sorted(p.get("versions") or [], key=lambda x: x.get("n", 0)):
        if v.get("state") == "done" and v.get("source") in ("build", "update", "tweak", "story_audio"):
            build = secs(v.get("started"), v.get("ended")) or build
    concepts = None
    chat = p.get("chat") or []
    for i, m in enumerate(chat):
        if m.get("kind") == "concepts" and m.get("role") == "assistant":
            prev = next((x for x in reversed(chat[:i]) if x.get("role") == "user"), None)
            if prev:
                concepts = secs(prev.get("ts"), m.get("ts")) or concepts
    return {"last_build_seconds": build, "last_concepts_seconds": concepts}


class ProjectRepo:
    def __init__(self, store):
        self.store = store
        self._pool = ThreadPoolExecutor(max_workers=16)

    # sync API -------------------------------------------------------------
    def get(self, pid: str) -> dict:
        if not pid or "/" in pid or ".." in pid:
            raise NotFound(pid)
        p = self.store.read_json(pkey(pid))
        if not p or p.get("deleted"):
            raise NotFound(pid)
        return p

    def create(self, name: str | None = None, mode: str | None = None) -> dict:
        p = blank_project(name, mode)
        self.store.write_json(pkey(p["id"]), p)
        return p

    def update(self, pid: str, fn: Callable[[dict], Any]) -> dict:
        """CAS update. fn mutates the project (or returns a replacement)."""
        missing = object()

        def wrap(cur):
            if not cur or cur is missing or cur.get("deleted"):
                raise NotFound(pid)
            res = fn(cur)
            out = cur if res is None else res
            out["updated"] = now_iso()
            return out

        return self.store.update_json(pkey(pid), wrap, default=None)

    def list(self) -> list[dict]:
        pids = self.store.list_dirs("projects")
        out = list(self._pool.map(self._safe_summary, pids))
        rows = [r for r in out if r]
        rows.sort(key=lambda r: r["updated"], reverse=True)
        return rows

    def _safe_summary(self, pid: str) -> dict | None:
        try:
            p = self.store.read_json(pkey(pid))
        except Exception:  # noqa: BLE001 - one broken project must not break the list
            return None
        if not p or p.get("deleted") or p.get("schema") != SCHEMA:
            return None
        return summary_of(p)

    # async wrappers ----------------------------------------------------------
    async def aget(self, pid: str) -> dict:
        return await asyncio.to_thread(self.get, pid)

    async def aupdate(self, pid: str, fn: Callable[[dict], Any]) -> dict:
        return await asyncio.to_thread(self.update, pid, fn)
