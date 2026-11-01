"""gstudio-builder over HTTPS (private Cloud Run), authenticated with a Google ID token for its URL."""
from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time

import httpx

from ..config import Settings
from ..engine_bridge import EngineBridge
from .base import BuilderError

log = logging.getLogger("gstudio.builder.remote")


class IdTokens:
    def __init__(self, audience: str):
        self.audience = audience
        self._tok: str | None = None
        self._exp = 0.0
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if self._tok and time.time() < self._exp - 300:
                return self._tok
            from google.auth.transport.requests import Request
            from google.oauth2 import id_token

            self._tok = id_token.fetch_id_token(Request(), self.audience)
            self._exp = time.time() + 3300
            return self._tok


class RemoteBuilder:
    kind = "remote"
    supports_seed = False         # the builder starts from an empty temp dir; partial builds become full builds

    def __init__(self, settings: Settings, bridge: EngineBridge):
        if not settings.builder_url:
            raise BuilderError("BUILDER_URL is not set")
        self.settings = settings
        self.bridge = bridge
        self.url = settings.builder_url.rstrip("/")
        self.tokens = IdTokens(self.url)

    async def _post(self, path: str, body: dict, timeout: float) -> dict:
        tok = await asyncio.to_thread(self.tokens.get)
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=20)) as c:
            for attempt in range(3):
                try:
                    r = await c.post(self.url + path, json=body, headers={"Authorization": f"Bearer {tok}"})
                except httpx.TransportError as exc:
                    if attempt < 2 and path != "/build":
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    raise BuilderError(f"builder {path}: {exc}") from exc
                if r.status_code in (429, 503) and attempt < 2 and path != "/build":
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                if r.status_code >= 500:
                    raise BuilderError(f"builder {path}: HTTP {r.status_code} {r.text[:300]}")
                try:
                    return r.json()
                except ValueError as exc:
                    raise BuilderError(f"builder {path}: HTTP {r.status_code}, not JSON") from exc
        raise BuilderError(f"builder {path}: no answer")

    async def validate(self, spec: dict) -> dict:
        if self.bridge.available:
            return await asyncio.to_thread(self.bridge.validate, spec)
        res = await self._post("/validate", {"spec": spec}, 60)
        return {"ok": bool(res.get("ok")), "errors": res.get("errors") or [], "warnings": res.get("warnings") or [],
                "normalized": res.get("normalized"), "engine": "builder"}

    async def preview(self, spec: dict) -> dict:
        res = await self._post("/preview", {"spec": spec}, self.settings.preview_timeout_s)
        png = base64.b64decode(res["png_b64"]) if res.get("png_b64") else None
        glb = base64.b64decode(res["glb_b64"]) if res.get("glb_b64") else None
        return {"ok": bool(res.get("ok")) and png is not None, "png": png, "glb": glb,
                "warnings": res.get("warnings") or [], "errors": res.get("errors") or []}

    async def build(self, pid: str, n: int, spec: dict, brief: dict | None, options: dict) -> dict:
        """Blocks until the builder finishes (it mirrors files + status.json to GCS as it goes). A 429 means every
        builder slot is busy: wait and retry for up to ~15 minutes."""
        opts = {k: v for k, v in (options or {}).items() if k != "only"}   # no seeded files on the builder
        body = {"project_id": pid, "version": n, "spec": spec, "brief": brief, "options": opts}
        if brief:
            if brief.get("version") is not None:
                body["brief_version"] = brief["version"]
            if brief.get("concept_id"):
                body["concept_id"] = brief["concept_id"]
        for attempt in range(60):
            res = await self._post("/build", body, self.settings.build_timeout_s)
            if not isinstance(res, dict):
                return {"ok": False, "error": f"builder /build returned {type(res).__name__}, not an object"}
            err = res.get("error")
            # Two shapes come back: the builder's error envelope {"error": {"code", "message"}} and the engine's build summary,
            # whose "error" is a plain string when a stage failed ("1 stage(s) failed: order: ..."). Calling .get on that
            # string raised AttributeError and replaced the real stage message in status.json (live failure 2026-11-02).
            if isinstance(err, dict):
                if err.get("code") == "rate_limited":
                    await asyncio.sleep(15)
                    continue
                return {**res, "ok": False, "error": str(err.get("message") or err.get("code") or "builder error"),
                        "code": err.get("code")}
            if "state" in res and "ok" not in res:  # an engine build summary: ok only when every stage finished
                res = {**res, "ok": res.get("state") == "done"}
            return res
        return {"ok": False, "error": "no builder slot became free in 15 minutes"}
