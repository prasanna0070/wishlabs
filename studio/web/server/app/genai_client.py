"""One google-genai client on Vertex AI (location global), created lazily."""
from __future__ import annotations

import threading

from .config import Settings

_client = None
_lock = threading.Lock()


def get_client(settings: Settings):
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                from google import genai
                from google.genai import types

                _client = genai.Client(vertexai=True, project=settings.gcp_project,
                                       location=settings.vertex_location,
                                       http_options=types.HttpOptions(timeout=int(settings.model_timeout_s * 1000)))
    return _client
