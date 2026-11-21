"""Login gate: one shared access key exchanged for an HMAC-signed, httponly session cookie."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import Settings

log = logging.getLogger("gstudio.auth")

COOKIE = "gs_session"
SECRET_NAME = "gstudio-access-key"


class KeyUnavailable(RuntimeError):
    pass


class AccessKey:
    """Resolves the access key once: env ACCESS_KEY, else Secret Manager (latest version)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._key: str | None = settings.access_key
        self._lock = threading.Lock()

    def get(self) -> str:
        if self._key:
            return self._key
        with self._lock:
            if not self._key:
                self._key = self._from_secret_manager()
        return self._key

    def _from_secret_manager(self) -> str:
        import base64

        import google.auth
        import requests
        from google.auth.transport.requests import Request as AuthRequest

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(AuthRequest())
        url = (f"https://secretmanager.googleapis.com/v1/projects/{self.settings.gcp_project}"
               f"/secrets/{SECRET_NAME}/versions/latest:access")
        r = requests.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=15)
        if r.status_code != 200:
            raise KeyUnavailable(f"Secret Manager returned {r.status_code}")
        key = base64.b64decode(r.json()["payload"]["data"]).decode().strip()
        if not key:
            raise KeyUnavailable("access key secret is empty")
        return key


class SessionSigner:
    def __init__(self, access_key: AccessKey, days: int = 30):
        self.access_key = access_key
        self.days = days

    def _mac_key(self) -> bytes:
        return hmac.new(b"gstudio-session-v1", self.access_key.get().encode(), hashlib.sha256).digest()

    def check_key(self, candidate: str) -> bool:
        if not candidate:
            return False
        return hmac.compare_digest(candidate.strip().encode(), self.access_key.get().encode())

    def magic_token(self, label: str) -> str:
        """Token of the magic sign-in link for `label` (32 hex chars). Derived from the access key, so rotating the key
        revokes every link."""
        return hmac.new(self._mac_key(), f"magic-link-v1:{label}".encode(), hashlib.sha256).hexdigest()[:32]

    def issue(self, now: float | None = None) -> tuple[str, int]:
        exp = int((now or time.time()) + self.days * 86400)
        body = f"v1.{exp}.{secrets.token_hex(8)}"
        sig = hmac.new(self._mac_key(), body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}", exp

    def verify(self, token: str | None, now: float | None = None) -> int | None:
        """Returns the expiry when valid, else None."""
        if not token or token.count(".") != 3:
            return None
        ver, exp_s, nonce, sig = token.split(".")
        if ver != "v1" or not exp_s.isdigit():
            return None
        body = f"{ver}.{exp_s}.{nonce}"
        try:
            want = hmac.new(self._mac_key(), body.encode(), hashlib.sha256).hexdigest()
        except KeyUnavailable:
            return None
        if not hmac.compare_digest(want, sig):
            return None
        exp = int(exp_s)
        if exp < (now or time.time()):
            return None
        return exp


class LoginLimiter:
    """At most `limit` failed logins per IP per `window` seconds."""

    def __init__(self, limit: int = 10, window: float = 600):
        self.limit, self.window = limit, window
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        with self._lock:
            q = self._fails[ip]
            cut = time.time() - self.window
            while q and q[0] < cut:
                q.popleft()
            return len(q) >= self.limit

    def fail(self, ip: str) -> None:
        with self._lock:
            self._fails[ip].append(time.time())


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def is_secure(request: Request, settings: Settings) -> bool:
    if settings.cookie_secure is not None:
        return settings.cookie_secure
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto == "https"


def require_session(request: Request) -> int:
    """FastAPI dependency for every protected /api route."""
    signer: SessionSigner = request.app.state.signer
    exp = signer.verify(request.cookies.get(COOKIE))
    if exp is None:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Please sign in."})
    return exp
