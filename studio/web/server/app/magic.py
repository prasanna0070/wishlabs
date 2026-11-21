"""Mint a magic link (app/routes/magic.py): python -m app.magic mint --label jurors --days 21

Reads the access key from env ACCESS_KEY and prints ONLY the URL. Revoke a label with MAGIC_REVOKED=jurors (env on
the service, '+'-separated), every link with MAGIC_LINKS=off, or everything by rotating the access key.
"""
from __future__ import annotations

import argparse
import os
import sys
from types import SimpleNamespace

from .auth import AccessKey, SessionSigner

DEFAULT_BASE = "https://wish.plugbook.si"


def mint_url(key: str, label: str, days: float, base: str) -> str:
    signer = SessionSigner(AccessKey(SimpleNamespace(access_key=key, gcp_project=None)))
    return f"{base.rstrip('/')}/api/magic/{signer.mint_magic(label, days)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.magic")
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
    try:
        print(mint_url(key, a.label, a.days, a.base))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
