from __future__ import annotations

from ..config import Settings
from .base import ConflictError, ObjectStat, Store, StoreError
from .local import LocalStore


def make_store(settings: Settings):
    if settings.bucket:
        from .gcs import GCSStore

        return GCSStore(settings.bucket, project=settings.gcp_project)
    assert settings.store_dir is not None
    return LocalStore(settings.store_dir)


__all__ = ["make_store", "Store", "StoreError", "ConflictError", "ObjectStat", "LocalStore"]
