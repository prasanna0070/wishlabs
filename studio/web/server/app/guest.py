"""Magic-link guests: what a guest may see (the showcases read-only, its own projects) and its daily limits.

The rule is enforced in ONE place for every protected route: auth.require_session calls guard() with the route the
request matched. Deny by default: a route without a {pid} is open to guests only when listed in GUEST_FREE, and a
{pid} route answers 404 unless the guest owns the project (project.json "owner") or it is a showcase, which is
read-only (GET/HEAD; anything else is a 403 that says so). The voice WebSocket has no {pid}: routes/voice.py applies
the same rule to the project id in its start message (voice_live.VoiceSession).

Limits are per guest per day and site-wide per day for all guests together, counted in one small JSON object per day
in the store (store.update_json: a generation-match compare-and-swap on GCS), so they hold across Cloud Run instances.
"""
from __future__ import annotations

import json
import logging
import os
import re
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Request

from .auth import GUEST_PREFIX, Session

log = logging.getLogger("gstudio.guest")

# Routes a guest may call that carry no project id. Everything else without {pid} is a 404 for a guest.
GUEST_FREE = {("GET", "/api/me"), ("GET", "/api/projects"), ("POST", "/api/projects")}
READ_METHODS = ("GET", "HEAD")
SHOWCASE_FILE = Path(__file__).resolve().parents[1] / "showcase.json"
PID_RE = re.compile(r"p_[A-Za-z0-9_]{1,40}")
SHOWCASE_MESSAGE = "This is a showcase. Make your own wish to try the studio."
# never shown to a guest on a project it does not own: order contacts, the device and share capability tokens
PRIVATE_KEYS = ("order_requests", "device", "share")
IST = timezone(timedelta(hours=5, minutes=30))


# The session acting in this request (set by routes/common.load_project). Tasks the request starts inherit it, so a
# build the team starts in a guest's project is not charged to the guest; a build with no request behind it (the
# voice wish's first turn) is charged to the project's guest owner.
ACTING: ContextVar[Session | None] = ContextVar("gs_acting", default=None)


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found."})


def showcase_forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail={"code": "showcase", "message": SHOWCASE_MESSAGE})


def showcase_ids() -> list[str]:
    """SHOWCASE_PROJECTS=p_a+p_b (env, first) plus studio/web/server/showcase.json (a JSON list of ids)."""
    out: list[str] = []
    for x in re.split(r"[+,\s]+", os.environ.get("SHOWCASE_PROJECTS") or ""):
        if PID_RE.fullmatch(x) and x not in out:
            out.append(x)
    try:
        extra = json.loads(SHOWCASE_FILE.read_text()) if SHOWCASE_FILE.is_file() else []
    except (OSError, ValueError):
        log.warning("showcase.json is not readable JSON; ignored")
        extra = []
    for x in extra if isinstance(extra, list) else []:
        if isinstance(x, str) and PID_RE.fullmatch(x) and x not in out:
            out.append(x)
    return out


def is_guest_owner(owner) -> bool:
    return isinstance(owner, str) and owner.startswith(GUEST_PREFIX)


def guard(request: Request, s: Session) -> None:
    """Called by require_session for every guest request on a protected route (sync: FastAPI runs it in a thread)."""
    route = request.scope.get("route")
    template = getattr(route, "path", None) or request.url.path
    pid = request.path_params.get("pid")
    if pid is None:
        if (request.method, template) in GUEST_FREE and request.url.path == template:
            return
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such API route."})
    from .projects import NotFound

    try:
        p = request.app.state.svc.repo.get(str(pid))
    except NotFound:
        raise not_found() from None
    if p.get("owner") == s.owner:
        return
    if str(pid) in showcase_ids():
        if request.method not in READ_METHODS:
            raise showcase_forbidden()
        # the uploaded images of a showcase follow the share-link rule: hidden unless the team shares them
        if (template or "").startswith("/api/projects/{pid}/uploads") and p.get("share_uploads") is not True:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "File not found."})
        return
    raise not_found()


def owns(s: Session | None, p: dict) -> bool:
    return bool(s and s.guest and p.get("owner") == s.owner)


def view_for(s: Session | None, p: dict) -> dict:
    """The project as this session may see it: unchanged for the team and for the guest who owns it; a showcase seen
    by a guest is read-only and without the private keys."""
    if not (s and s.guest) or owns(s, p):
        return p
    q = {k: v for k, v in p.items() if k not in PRIVATE_KEYS}
    if q.get("share_uploads") is not True:          # as on a share link (routes/common.shared_view)
        from .uploads import hide_uploads

        q = hide_uploads(q)
    q["read_only"] = True
    q["showcase"] = True
    return q


def visible_rows(s: Session | None, rows: list[dict]) -> list[dict]:
    """The project list: everything for the team; the showcases (first, labelled) and its own projects for a guest."""
    if not (s and s.guest):
        return rows
    shows = showcase_ids()
    by_id = {r["id"]: r for r in rows}
    out = [{**by_id[x], "showcase": True} for x in shows if x in by_id]
    out += [r for r in rows if r.get("owner") == s.owner and r["id"] not in shows]
    return out


# ------------------------------------------------------------------ daily limits
PER_GUEST = {"projects": 2, "turns": 15, "builds": 3, "voice": 3, "sound": 3}
SITE = {"turns": 300, "builds": 50}
ENV_GUEST = {"projects": "GUEST_DAILY_PROJECTS", "turns": "GUEST_DAILY_TURNS", "builds": "GUEST_DAILY_BUILDS",
             "voice": "GUEST_DAILY_VOICE", "sound": "GUEST_DAILY_SOUND"}
ENV_SITE = {"turns": "GUESTS_DAILY_TURNS", "builds": "GUESTS_DAILY_BUILDS"}
WHAT = {"projects": "wishes", "turns": "messages to the designer", "builds": "builds", "voice": "voice wishes",
        "sound": "sound generations"}
ONE = {"projects": "wish", "turns": "message to the designer", "builds": "build", "voice": "voice wish",
       "sound": "sound generation"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name) or default))
    except ValueError:
        return default


def limits() -> tuple[dict, dict]:
    return ({k: _env_int(ENV_GUEST[k], v) for k, v in PER_GUEST.items()},
            {k: _env_int(ENV_SITE[k], v) for k, v in SITE.items()})


class LimitReached(HTTPException):
    def __init__(self, what: str, limit: int, site: bool = False):
        self.what = what
        if site:
            msg = "The studio has reached today's limit for guests. Come back tomorrow, or watch the showcases."
        else:
            noun = (ONE if limit == 1 else WHAT).get(what, what)
            msg = f"You have used today's {limit} {noun}. Come back tomorrow, or watch the showcases."
        self.message = msg
        super().__init__(status_code=429, detail={"code": "limit", "message": msg,
                                                  "details": {"what": what, "limit": limit, "site": site}})


def today(now: datetime | None = None) -> str:
    return (now or datetime.now(IST)).astimezone(IST).strftime("%Y-%m-%d")


class GuestQuota:
    """Counts guest usage per IST day in guests/usage/<day>.json: {"guests": {owner: {what: n}}, "site": {what: n}}."""

    def __init__(self, store):
        self.store = store

    def key(self, day: str | None = None) -> str:
        return f"guests/usage/{day or today()}.json"

    def charge(self, owner: str, what: str, n: int = 1) -> None:
        per, site = limits()

        def fn(cur):
            cur = cur if isinstance(cur, dict) else {}
            g = cur.setdefault("guests", {}).setdefault(owner, {})
            st = cur.setdefault("site", {})
            if int(g.get(what) or 0) + n > per.get(what, 0):
                raise LimitReached(what, per.get(what, 0))
            if what in site and int(st.get(what) or 0) + n > site[what]:
                raise LimitReached(what, site[what], site=True)
            g[what] = int(g.get(what) or 0) + n
            st[what] = int(st.get(what) or 0) + n
            return cur
        self.store.update_json(self.key(), fn, default={})

    def refund(self, owner: str, what: str, n: int = 1) -> None:
        def fn(cur):
            cur = cur if isinstance(cur, dict) else {}
            g = cur.setdefault("guests", {}).setdefault(owner, {})
            st = cur.setdefault("site", {})
            g[what] = max(0, int(g.get(what) or 0) - n)
            st[what] = max(0, int(st.get(what) or 0) - n)
            return cur
        try:
            self.store.update_json(self.key(), fn, default={})
        except Exception:  # noqa: BLE001 - a lost refund only costs the guest one unit
            log.exception("guest refund failed")

    def left(self, owner: str) -> dict:
        per, site = limits()
        cur = self.store.read_json(self.key()) or {}
        g = (cur.get("guests") or {}).get(owner) or {}
        st = cur.get("site") or {}
        out = {}
        for k, lim in per.items():
            mine = max(0, lim - int(g.get(k) or 0))
            if k in site:
                mine = min(mine, max(0, site[k] - int(st.get(k) or 0)))
            out[k] = mine
        return {"left": out, "limits": per, "day": today()}


def quota_of(svc) -> GuestQuota:
    q = getattr(svc, "_guest_quota", None)
    if q is None:
        q = svc._guest_quota = GuestQuota(svc.store)
    return q


def build_owner(project: dict) -> str | None:
    """The guest to charge for a build of this project: None when the team is acting or the project is the team's."""
    s = ACTING.get()
    if s is not None and not s.guest:
        return None
    owner = project.get("owner")
    return owner if is_guest_owner(owner) else None


def charge_owner(svc, owner, what: str) -> bool:
    """Charge one unit when `owner` is a guest. True when charged (so the caller can refund on failure)."""
    if not is_guest_owner(owner):
        return False
    quota_of(svc).charge(owner, what)
    return True
