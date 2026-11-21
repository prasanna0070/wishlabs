"""Mint a magic link (app/routes/magic.py): python3 -m app.magic mint --label jurors --days 21

Run from studio/web/server with the access key in env ACCESS_KEY; prints ONLY the URL. Standard library only, so it
runs on a laptop without the server's dependencies, e.g. (the key never reaches the screen):
  ACCESS_KEY="$(gcloud secrets versions access latest --secret=gstudio-access-key --project=<project>)" \
    python3 -m app.magic mint --label jurors --days 21
Revoke a label with MAGIC_REVOKED=jurors on the service ('+'-separated), every link and guest session with
MAGIC_LINKS=off, or everything by rotating the access key. The token format is checked by
auth.SessionSigner.check_magic (tests/test_guest.py opens a minted link in the app).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import re
import sys
import time

DEFAULT_BASE = "https://wish.plugbook.si"
LABEL_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")      # = auth.LABEL_RE


def mint_token(key: str, label: str, days: float = 21, now: float | None = None) -> str:
    """m1.<label>.<exp>.<sig>, the same bytes auth.SessionSigner.mint_magic makes."""
    if not LABEL_RE.fullmatch(label or ""):
        raise ValueError("label: 1-32 of a-z, 0-9 and -, starting with a letter or digit")
    exp = int((now or time.time()) + days * 86400)
    body = f"m1.{label}.{exp}"
    mac_key = hmac.new(b"gstudio-magic-v1", key.encode(), hashlib.sha256).digest()
    return f"{body}.{hmac.new(mac_key, body.encode(), hashlib.sha256).hexdigest()}"


def mint_url(key: str, label: str, days: float, base: str) -> str:
    return f"{base.rstrip('/')}/api/magic/{mint_token(key, label, days)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m app.magic")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint", help="print a magic link")
    m.add_argument("--label", required=True, help="who it is for: a-z, 0-9, - (revoke with MAGIC_REVOKED=<label>)")
    m.add_argument("--days", type=float, default=21)
    m.add_argument("--base", default=os.environ.get("MAGIC_BASE_URL") or DEFAULT_BASE)
    a = ap.parse_args(argv)
    key = (os.environ.get("ACCESS_KEY") or "").strip()
    if not key:
        print("ACCESS_KEY is not set in the environment", file=sys.stderr)
        return 2
    if a.days <= 0:
        print("--days must be more than 0", file=sys.stderr)
        return 2
    try:
        print(mint_url(key, a.label, a.days, a.base))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
