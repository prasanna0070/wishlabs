"""FastAPI app factory. The module-level ASGI app is in app/asgi.py: uvicorn app.asgi:app --port 8080."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import AccessKey, LoginLimiter, SessionSigner
from .config import SERVER_VERSION, Settings
from .routes import device as device_routes
from .routes import projects as project_routes
from .routes import public as public_routes
from .services import Services

log = logging.getLogger("gstudio.web")

NO_SPA = """<!doctype html><html><head><meta charset="utf-8"><title>Gadget Studio</title></head>
<body style="font-family:system-ui;background:#F6F5F1;color:#1B1F24;padding:40px">
<h1>Gadget Studio API</h1><p>The web app is not built into this server. API: <code>/api/health</code>.</p></body></html>"""


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or (services.settings if services else Settings.from_env())
    svc = services or Services(settings)
    app = FastAPI(title="Gadget Studio", version=SERVER_VERSION, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  redoc_url=None)
    app.state.svc = svc
    app.state.signer = SessionSigner(AccessKey(settings), settings.session_days)
    app.state.limiter = LoginLimiter()
    app.state.bg = set()

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        d = exc.detail if isinstance(exc.detail, dict) else {
            "code": {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                     409: "conflict", 422: "invalid", 429: "rate_limited"}.get(exc.status_code, "error"),
            "message": str(exc.detail)}
        return JSONResponse({"error": d}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errs = [f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg')}" for e in exc.errors()]
        return JSONResponse({"error": {"code": "invalid", "message": "Invalid request.", "details": {"errors": errs}}},
                            status_code=422)

    app.include_router(public_routes.router)
    app.include_router(device_routes.router)
    app.include_router(project_routes.router)

    static: Path | None = settings.static_dir if settings.static_dir and settings.static_dir.is_dir() else None
    index = static / "index.html" if static else None

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def api_404(rest: str):
        return JSONResponse({"error": {"code": "not_found", "message": "No such API route."}}, status_code=404)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str):
        if static is None or index is None or not index.exists():
            return HTMLResponse(NO_SPA)
        if full_path:
            cand = (static / full_path).resolve()
            if cand.is_file() and static.resolve() in cand.parents:
                cache = ("public, max-age=31536000, immutable" if full_path.startswith("assets/")
                         else "public, max-age=300")
                return FileResponse(cand, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
