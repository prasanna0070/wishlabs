"""Runtime settings, read once from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

GCP_PROJECT = "your-gcp-project"
SERVER_VERSION = "0.1.0"


def _default_studio_root() -> Path:
    # app/ -> server/ -> web/ -> studio/
    return Path(__file__).resolve().parents[3]


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    studio_root: Path = field(default_factory=_default_studio_root)
    store_dir: Path | None = None
    bucket: str | None = None
    builder: str = "fake"                      # remote | local | fake
    builder_url: str | None = None
    agent: str = "gemini"                      # gemini | scripted
    access_key: str | None = None              # None -> Secret Manager
    gcp_project: str = GCP_PROJECT
    vertex_location: str = "global"
    model_pro: str = "gemini-3.1-pro-preview"
    model_fast: str = "gemini-3.5-flash"
    image_model: str = "gemini-3-pro-image"
    photos: bool = True
    image_timeout_s: float = 120.0
    model_timeout_s: float = 300.0
    cookie_secure: bool | None = None          # None -> follow the request scheme
    session_days: int = 30
    static_dir: Path | None = None
    fake_slug: str | None = None
    fake_build_seconds: float = 4.0
    fake_use_examples: bool = True             # False: always write the placeholder tree
    preview_timeout_s: float = 180.0
    build_timeout_s: float = 3600.0
    stale_build_minutes: float = 30.0
    max_repair_rounds: int = 3
    thinking_level: str | None = None          # low | high | None (model default)
    public_url: str | None = None              # e.g. https://studio.example.com; builds then carry name_audio_url

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.environ["STUDIO_ROOT"]).resolve() if os.environ.get("STUDIO_ROOT") else _default_studio_root()
        bucket = os.environ.get("BUCKET") or None
        store_dir = os.environ.get("STORE_DIR") or None
        if not bucket and not store_dir:
            store_dir = str(root / "web" / "server" / "_out" / "store")
        builder_url = os.environ.get("BUILDER_URL") or None
        builder = os.environ.get("BUILDER") or ("remote" if builder_url else "fake")
        static = os.environ.get("STATIC_DIR")
        static_dir = Path(static) if static else (root / "web" / "app" / "dist")
        cs = os.environ.get("COOKIE_SECURE")
        return cls(
            studio_root=root,
            store_dir=Path(store_dir) if store_dir and not bucket else None,
            bucket=bucket,
            builder=builder,
            builder_url=builder_url,
            agent=os.environ.get("AGENT", "gemini"),
            access_key=os.environ.get("ACCESS_KEY") or None,
            gcp_project=os.environ.get("GOOGLE_CLOUD_PROJECT", GCP_PROJECT),
            vertex_location=os.environ.get("VERTEX_LOCATION", "global"),
            model_pro=os.environ.get("MODEL_PRO", "gemini-3.1-pro-preview"),
            model_fast=os.environ.get("MODEL_FAST", "gemini-3.5-flash"),
            image_model=os.environ.get("IMAGE_MODEL", "gemini-3-pro-image"),
            photos=_flag("PHOTOS", True),
            image_timeout_s=float(os.environ.get("IMAGE_TIMEOUT_S", "120")),
            cookie_secure=None if cs in (None, "") else _flag("COOKIE_SECURE", True),
            static_dir=static_dir,
            fake_slug=os.environ.get("FAKE_SLUG") or None,
            fake_build_seconds=float(os.environ.get("FAKE_BUILD_SECONDS", "4")),
            fake_use_examples=_flag("FAKE_EXAMPLES", True),
            thinking_level=os.environ.get("AGENT_THINKING") or None,
            public_url=(os.environ.get("PUBLIC_URL") or "").rstrip("/") or None,
        )

    @property
    def engine_dir(self) -> Path:
        return self.studio_root / "engine"

    @property
    def library_dir(self) -> Path:
        return self.studio_root / "library"

    @property
    def examples_dir(self) -> Path:
        return self.studio_root / "examples"

    @property
    def docs_dir(self) -> Path:
        return self.studio_root / "docs"

    def model_for(self, mode: str | None) -> str:
        return self.model_fast if mode == "fast" else self.model_pro
