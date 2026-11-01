from __future__ import annotations

from ..config import Settings
from ..engine_bridge import EngineBridge
from .base import Builder, BuilderError


def make_builder(settings: Settings, bridge: EngineBridge, store) -> Builder:
    if settings.builder == "remote":
        from .remote import RemoteBuilder

        return RemoteBuilder(settings, bridge)
    if settings.builder == "local":
        from .local import LocalBuilder

        return LocalBuilder(settings, bridge, store)
    from .fake import FakeBuilder

    return FakeBuilder(settings, bridge, store)


__all__ = ["make_builder", "Builder", "BuilderError"]
