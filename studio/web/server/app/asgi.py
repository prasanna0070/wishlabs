"""ASGI entry point: uvicorn app.asgi:app (run from web/server)."""
from .main import create_app

app = create_app()
