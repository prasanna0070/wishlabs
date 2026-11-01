"""Builder interface. A builder validates specs, renders shell previews and runs full builds whose outputs land
in the store under projects/<pid>/v<n>/ (with status.json updated as stages finish)."""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from ..engine_bridge import EngineBridge


class BuilderError(RuntimeError):
    pass


class Builder(Protocol):
    kind: str

    async def validate(self, spec: dict) -> dict: ...
    async def preview(self, spec: dict) -> dict: ...
    async def build(self, pid: str, n: int, spec: dict, brief: dict | None, options: dict) -> dict: ...


class BridgeValidateMixin:
    """Validate in-process with the bundled engine modules; subclasses may override with a remote call."""

    bridge: EngineBridge

    async def validate(self, spec: dict) -> dict:
        return await asyncio.to_thread(self.bridge.validate, spec)


def status_doc(state: str, stages: list[dict] | None = None, percent: float | None = None,
               error: str | None = None, **extra: Any) -> dict:
    d = {"state": state, "percent": percent, "stages": stages or [], "error": error}
    d.update(extra)
    return d
