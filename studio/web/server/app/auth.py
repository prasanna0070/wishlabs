"""Login gate: one shared access key exchanged for an HMAC-signed, httponly session cookie."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import Settings

log = logging.getLogger("gstudio.auth")

COOKIE = "gs_session"
SECRET_NAME = "gstudio-access-key"


GUEST_PREFIX = "g_"                              # project.json "owner" of a project a magic-link guest made
LABEL_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
GID_RE = re.compile(r"[0-9a-f]{24}")


class KeyUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    """A signed-in browser: the team (shared access key, full access) or a magic-link guest (own random id)."""
    kind: str                       # "team" | "guest"
    exp: int
    gid: str = ""
    label: str = ""

    @property
    def guest(self) -> bool:
        return self.kind == "guest"

    @property
    def owner(self) -> str | None:
        return GUEST_PREFIX + self.gid if self.guest else None


def magic_enabled() -> bool:
    """MAGIC_LINKS=off switches magic links AND every guest session off (guests then see the sign-in page)."""
    return (os.environ.get("MAGIC_LINKS") or "on").strip().lower() not in ("off", "0", "false", "no")


def revoked_labels() -> set[str]:
    """MAGIC_REVOKED=jurors+demo revokes those labels: their links and the guest sessions they started."""
    return {x for x in re.split(r"[+,\s]+", (os.environ.get("MAGIC_REVOKED") or "").strip().lower()) if x}


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

    def _magic_key(self) -> bytes:
        return hmac.new(b"gstudio-magic-v1", self.access_key.get().encode(), hashlib.sha256).digest()

    def _sig(self, key: bytes, body: str) -> str:
        return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()

    def check_key(self, candidate: str) -> bool:
        if not candidate:
            return False
        return hmac.compare_digest(candidate.strip().encode(), self.access_key.get().encode())

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


    # ---------------------------------------------------------------- magic links + guest sessions
    def mint_magic(self, label: str, days: float = 21, now: float | None = None) -> str:
        """m1.<label>.<exp>.<sig>: anyone who opens /api/magic/<token> before exp becomes a guest (own id)."""
        if not LABEL_RE.fullmatch(label or ""):
            raise ValueError("label: 1-32 of a-z, 0-9 and -, starting with a letter or digit")
        exp = int((now or time.time()) + days * 86400)
        body = f"m1.{label}.{exp}"
        return f"{body}.{self._sig(self._magic_key(), body)}"

    def check_magic(self, token: str | None, now: float | None = None) -> tuple[str, int] | None:
        """(label, exp) for a valid, unexpired, unrevoked magic token while magic links are on; else None."""
        parts = (token or "").split(".")
        if len(parts) != 4 or parts[0] != "m1" or not magic_enabled():
            return None
        _, label, exp_s, sig = parts
        if not LABEL_RE.fullmatch(label) or not exp_s.isdigit() or len(exp_s) > 12:
            return None
        try:
            want = self._sig(self._magic_key(), f"m1.{label}.{exp_s}")
        except KeyUnavailable:
            return None
        if not hmac.compare_digest(want, sig):
            return None
        if int(exp_s) < (now or time.time()) or label in revoked_labels():
            return None
        return label, int(exp_s)

    def issue_guest(self, label: str, exp: int, gid: str | None = None) -> tuple[str, str]:
        """A guest session cookie g1.<exp>.<gid>.<label>.<sig> (team verify() rejects it: other shape)."""
        gid = gid if gid and GID_RE.fullmatch(gid) else secrets.token_hex(12)
        body = f"g1.{int(exp)}.{gid}.{label}"
        return f"{body}.{self._sig(self._mac_key(), body)}", gid

    def session(self, token: str | None, now: float | None = None) -> Session | None:
        exp = self.verify(token, now)
        if exp is not None:
            return Session("team", exp)
        parts = (token or "").split(".")
        if len(parts) != 5 or parts[0] != "g1":
            return None
        _, exp_s, gid, label, sig = parts
        if not exp_s.isdigit() or len(exp_s) > 12 or not GID_RE.fullmatch(gid) or not LABEL_RE.fullmatch(label):
            return None
        try:
            want = self._sig(self._mac_key(), f"g1.{exp_s}.{gid}.{label}")
        except KeyUnavailable:
            return None
        if not hmac.compare_digest(want, sig) or int(exp_s) < (now or time.time()):
            return None
        if not magic_enabled() or label in revoked_labels():
            return None
        return Session("guest", int(exp_s), gid, label)


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


def current_session(request: Request) -> Session | None:
    """The session require_session accepted for this request (None on public routes)."""
    return getattr(request.state, "gs", None)


def require_session(request: Request) -> int:
    """FastAPI dependency for every protected /api route. A guest session is also checked here, against the route
    it matched (app/guest.py guard): its own projects, the showcases read-only, nothing else (deny by default)."""
    signer: SessionSigner = request.app.state.signer
    s = signer.session(request.cookies.get(COOKIE))
    if s is None:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Please sign in."})
    request.state.gs = s
    if s.guest:
        from .guest import guard

        guard(request, s)
    return s.exp
