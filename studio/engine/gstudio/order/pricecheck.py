"""Price receipts (MAJ-25): re-read a vendor listing, keep the raw response, sign what was read.

    check_rows(rows, out, mode="live", fetch=None) -> report
        For every purchased row whose chosen source is a dated page price ("seen"), fetch the listing (Shopify
        `<product>.js`, else the product page's JSON-LD / price meta tags), save the raw response to
        `<out>/references/listings/<module>-<vendor>.raw` plus `<...>.json` metadata (sha256, fetch time, status), and
        HMAC-sign {url, sku, pack, price, gst_basis, stock, checked_at, snapshot_sha256} into row.listing.receipt.
        A row stays "seen" only when the re-read gave a price; otherwise it drops to "estimate" with the reason.
    verify_row(row, out, key=None) -> (ok, why)
        Recomputes the HMAC from the row's own fields and the snapshot file's sha256: changing a price, URL, stock or
        the snapshot in a delivery makes it fail.

Modes: "live" fetches (cached for CACHE_TTL_S in $GSTUDIO_CACHE/listings), "cache_only" uses only cached snapshots,
"off" signs nothing (every former "seen" row becomes an estimate). The key is $GSTUDIO_PRICE_KEY, else a per-machine
key file created on first use (deploy: put the key in Secret Manager so receipts verify across builder instances).
"""
from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

CACHE_TTL_S = 3 * 24 * 3600
FETCH_TIMEOUT_S = 8.0
BUDGET_S = 45.0
MAX_BYTES = 3_000_000
UA = "GadgetStudio-pricecheck/1 (one product page per part, cached 3 days)"
REL_DIR = "references/listings"
RECEIPT_FIELDS = ("url", "sku", "pack", "price", "gst_basis", "stock", "checked_at", "snapshot_sha256")

Fetcher = Callable[[str], tuple[int, str, bytes]]   # url -> (http status, content type, body)


# ------------------------------------------------------------------ keys and signing


def cache_dir() -> Path:
    base = os.environ.get("GSTUDIO_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "gstudio")
    return Path(base)


def signing_key() -> bytes:
    env = os.environ.get("GSTUDIO_PRICE_KEY")
    if env:
        return env.encode()
    p = cache_dir() / "price-receipt.key"
    try:
        return p.read_bytes().strip()
    except OSError:
        p.parent.mkdir(parents=True, exist_ok=True)
        k = secrets.token_hex(32).encode()
        tmp = p.with_name(f".{p.name}.{os.getpid()}")
        tmp.write_bytes(k)
        os.chmod(tmp, 0o600)
        try:
            os.link(tmp, p)  # first writer wins, so parallel builds agree on one key
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
        return p.read_bytes().strip()


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:12]


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sign(payload: dict, key: Optional[bytes] = None) -> dict:
    key = key or signing_key()
    return {"alg": "HMAC-SHA256", "key_id": key_id(key), "payload": payload,
            "sig": hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()}


def receipt_payload(row: dict) -> dict:
    """The signed fields, read back from a purchased row (so an edited row no longer matches its receipt)."""
    lst = row.get("listing") or {}
    return {"url": row.get("url"), "sku": row.get("sku"), "pack": row.get("packSize"), "price": row.get("price_inr_seen"),
            "gst_basis": row.get("gst_basis"), "stock": lst.get("stock"), "checked_at": row.get("seen_on"),
            "snapshot_sha256": lst.get("snapshot_sha256")}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def read_snapshot(out: Path, rel: str) -> Optional[bytes]:
    try:
        return gzip.decompress((Path(out) / rel).read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile):
        return None


def verify_row(row: dict, out: Path, key: Optional[bytes] = None) -> tuple[bool, str]:
    lst = row.get("listing") or {}
    rc = lst.get("receipt")
    if not isinstance(rc, dict) or not rc.get("sig"):
        return False, "no receipt"
    key = key or signing_key()
    if rc.get("key_id") and rc["key_id"] != key_id(key):
        return False, f"receipt signed with key {rc['key_id']}, this machine has {key_id(key)}"
    want = receipt_payload(row)
    if rc.get("payload") != want:
        diff = sorted(k for k in RECEIPT_FIELDS if (rc.get("payload") or {}).get(k) != want.get(k))
        return False, f"row fields differ from the signed receipt: {', '.join(diff)}"
    good = hmac.new(key, canonical(want), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, str(rc["sig"])):
        return False, "signature does not verify"
    rel = lst.get("snapshot")
    raw = read_snapshot(out, rel) if rel else None
    if raw is None:
        return False, f"snapshot {rel} missing"
    if sha256_bytes(raw) != want.get("snapshot_sha256"):
        return False, f"snapshot {rel} sha256 differs from the receipt"
    return True, "receipt verifies"


# ------------------------------------------------------------------ fetching and parsing


def shopify_js_url(url: str) -> Optional[str]:
    u = urllib.parse.urlsplit(url)
    if "/products/" not in u.path:
        return None
    path = u.path.rstrip("/")
    if path.endswith(".js"):
        return url
    return urllib.parse.urlunsplit((u.scheme, u.netloc, path + ".js", "", ""))


def http_fetch(url: str) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r:  # noqa: S310 - https vendor pages only
            return r.status, r.headers.get("Content-Type", ""), r.read(MAX_BYTES)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", b""


_LD = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)
_META = re.compile(r"<meta[^>]+(?:property|itemprop|name)=[\"'](?:product:price:amount|og:price:amount|price)[\"'][^>]*>", re.I)
_CONTENT = re.compile(r"content=[\"']([0-9][0-9,]*(?:\.[0-9]+)?)[\"']", re.I)


def _offers(obj: Any) -> list[dict]:
    found = []
    if isinstance(obj, list):
        for x in obj:
            found += _offers(x)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        types = t if isinstance(t, list) else [t]
        if "Offer" in types or "AggregateOffer" in types:
            found.append(obj)
        for k in ("offers", "@graph", "mainEntity", "hasVariant"):
            if k in obj:
                found += _offers(obj[k])
    return found


def parse_listing(body: bytes, ctype: str, url: str, sku: Optional[str] = None) -> dict:
    """{price, currency, stock, sku, method} or {error}. Never guesses a price from free text."""
    text = body.decode("utf-8", "replace")
    if "json" in ctype or url.endswith(".js") or text.lstrip().startswith("{"):
        try:
            d = json.loads(text)
        except ValueError:
            d = None
        if isinstance(d, dict) and isinstance(d.get("variants"), list) and d["variants"]:
            vs = d["variants"]
            v = next((x for x in vs if sku and str(x.get("sku") or "") == str(sku)), vs[0])
            p = v.get("price", d.get("price"))
            if isinstance(p, (int, float)):
                price = p / 100.0 if isinstance(p, int) else float(p)  # Shopify .js gives paise as an integer
            else:
                try:
                    price = float(str(p).replace(",", ""))
                except ValueError:
                    return {"error": "Shopify JSON has no readable price"}
            return {"price": round(price, 2), "currency": "INR", "stock": "in_stock" if v.get("available") else "out_of_stock",
                    "sku": v.get("sku") or None, "method": "shopify .js variant" + (" (matched sku)" if sku and v.get("sku") == sku else "")}
    for blk in _LD.findall(text):
        try:
            d = json.loads(html.unescape(blk.strip()))
        except ValueError:
            continue
        for off in _offers(d):
            p = off.get("price", off.get("lowPrice"))
            cur = str(off.get("priceCurrency") or "INR").upper()
            try:
                price = float(str(p).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if cur != "INR" or price <= 0:
                continue
            av = str(off.get("availability") or "").lower()
            stock = "in_stock" if "instock" in av else ("out_of_stock" if ("outofstock" in av or "soldout" in av) else "unknown")
            return {"price": round(price, 2), "currency": cur, "stock": stock, "sku": off.get("sku"), "method": "JSON-LD Offer"}
    for m in _META.finditer(text):
        c = _CONTENT.search(m.group(0))
        if c:
            return {"price": round(float(c.group(1).replace(",", "")), 2), "currency": "INR", "stock": "unknown",
                    "sku": None, "method": "price meta tag"}
    vals = {round(float(v.replace(",", "")), 2) for v in _PRICE_EL.findall(text)}
    if len(vals) == 1:  # one price element (OpenCart-style id="price"/"price_old"/"price_new"); never several conflicting ones
        return {"price": vals.pop(), "currency": "INR", "stock": "unknown", "sku": None, "method": "price element (id=price*)"}
    if len(vals) > 1:
        return {"error": f"the page's price elements disagree ({', '.join(f'{v:g}' for v in sorted(vals))})"}
    return {"error": "no machine-readable price on the page (no Shopify JSON, JSON-LD Offer, price meta tag or price element)"}


_PRICE_EL = re.compile(r"<(?:span|div|p)[^>]+\bid=[\"'](?:price|price_old|price_new|price-new)[\"'][^>]*>\s*(?:Rs\.?|\u20b9|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*<",
                       re.I)


def _slug(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s or "x").lower()).strip("-")[:60] or "x"


def snapshot_rel(module_id: Optional[str], vendor: Optional[str], url: str) -> str:
    return f"{REL_DIR}/{_slug(module_id or hashlib.sha1(url.encode()).hexdigest()[:10])}-{_slug(vendor)}.raw"


def _cached(url: str, mode: str, fetch: Fetcher) -> dict:
    """{status, ctype, body, fetched_at, source: cache|live} or {error}."""
    cdir = cache_dir() / "listings"
    h = hashlib.sha256(url.encode()).hexdigest()[:32]
    meta_p, body_p = cdir / f"{h}.json", cdir / f"{h}.raw"
    try:
        meta = json.loads(meta_p.read_text())
        if time.time() - float(meta.get("fetched_epoch", 0)) < CACHE_TTL_S and body_p.exists():
            return dict(meta, body=body_p.read_bytes(), source="cache")
    except (OSError, ValueError):
        pass
    if mode != "live":
        return {"error": "no cached snapshot (price_check is cache_only)"}
    try:
        status, ctype, body = fetch(url)
    except Exception as exc:  # noqa: BLE001 - network failures drop the row to an estimate
        return {"error": f"fetch failed: {type(exc).__name__}: {str(exc)[:120]}"}
    now = _dt.datetime.now(_dt.timezone.utc)
    meta = {"url": url, "status": status, "ctype": ctype, "fetched_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "fetched_epoch": now.timestamp()}
    if status == 200 and body:
        cdir.mkdir(parents=True, exist_ok=True)
        tmp = body_p.with_name(f".{body_p.name}.{os.getpid()}")
        tmp.write_bytes(body)
        tmp.replace(body_p)
        meta_p.write_text(json.dumps(meta))
    return dict(meta, body=body, source="live")


def gst_incl(price: Optional[float], basis: Optional[str], rate: float = 0.18) -> Optional[float]:
    if price is None:
        return None
    return round(float(price) if basis == "incl" else float(price) * (1 + rate), 2)


def library_index(library_root: Optional[Path]) -> dict[str, str]:
    """{relative path: sha256} from library/references/index.json (foundation, MIN-23)."""
    if not library_root:
        return {}
    try:
        d = json.loads((Path(library_root) / "references" / "index.json").read_text())
    except (OSError, ValueError):
        return {}
    return {str(f.get("path")): str(f.get("sha256")) for f in d.get("files") or [] if isinstance(f, dict) and f.get("path")}


def library_snapshot(r: dict, library_root: Optional[Path], index: dict) -> Optional[tuple[bytes, str, bool]]:
    """(raw bytes, relative path, sha256 matches index.json) of the chosen source's saved listing, if foundation saved one."""
    rel = (r.get("listing") or {}).get("library_snapshot_path")
    if not rel or not library_root:
        return None
    rel = str(rel)
    p = Path(library_root) / rel
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    key = rel[len("references/"):] if rel.startswith("references/") else rel
    want = index.get(key) or index.get(rel)
    return raw, rel, bool(want) and want == sha256_bytes(raw)


def copy_library_snapshots(rows: list[dict], out: Path, library_root: Optional[Path]) -> dict:
    """MIN-23: every chosen source's saved listing ships under references/library/ with its index.json sha256 checked."""
    index = library_index(library_root)
    n, bad = 0, []
    for r in rows:
        got = library_snapshot(r, library_root, index)
        if not got:
            continue
        raw, rel, ok = got
        dst = Path(out) / "references" / "library" / rel.replace("references/", "", 1)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(raw)
        r["listing"]["library_snapshot"] = {"path": dst.relative_to(out).as_posix(), "sha256": sha256_bytes(raw), "index_sha256_ok": ok}
        n += 1
        if not ok:
            bad.append(r.get("name"))
    return {"copied": n, "sha256_mismatch": bad}


def check_rows(rows: list[dict], out: Path, mode: str = "live", fetch: Optional[Fetcher] = None,
               key: Optional[bytes] = None, budget_s: float = BUDGET_S, library_root: Optional[Path] = None) -> dict:
    """Re-read and sign every "seen" row in place. Returns a report (also for quote.json / the summary). When the live
    re-read fails, a listing foundation saved under library/references/ (sha256 checked against its index.json) is used
    instead, dated with its own check time."""
    t0 = time.monotonic()
    fetch = fetch or http_fetch
    out = Path(out)
    index = library_index(library_root)
    todo = [r for r in rows if r.get("evidence_tier") == "seen" and r.get("url")]
    report = {"mode": mode, "checked": 0, "signed": 0, "dropped": [], "seconds": 0.0, "key_id": None, "from_library_snapshot": []}
    if mode == "off" or not todo:
        for r in todo:
            _drop(r, "price re-read is switched off (price_check=off)")
            report["dropped"].append(r["name"])
        return report
    key = key or signing_key()
    report["key_id"] = key_id(key)

    def one(r: dict) -> tuple[dict, dict]:
        urls = [u for u in (shopify_js_url(r["url"]), r["url"]) if u]
        last: dict = {"error": "not fetched"}
        for u in urls:
            if time.monotonic() - t0 > budget_s:
                return r, {"error": f"price-check time budget of {budget_s:.0f} s used up"}
            got = _cached(u, mode, fetch)
            if got.get("error"):
                last = got
                continue
            if got.get("status") != 200 or not got.get("body"):
                last = {"error": f"HTTP {got.get('status')} from {u}"}
                continue
            parsed = parse_listing(got["body"], str(got.get("ctype") or ""), u, r.get("sku"))
            if parsed.get("error"):
                last = dict(parsed, fetched=got)
                continue
            return r, dict(parsed, fetched=got, read_url=u)
        lib = library_snapshot(r, library_root, index)
        if lib and lib[2]:
            raw, rel, _ok = lib
            ctype = "application/json" if rel.endswith((".js", ".json")) else "text/html"
            parsed = parse_listing(raw, ctype, rel, r.get("sku"))
            if not parsed.get("error"):
                when = (r.get("listing") or {}).get("checked_at") or r.get("seen_on")
                return r, dict(parsed, read_url=f"library/{rel}", from_library=True, live_error=last.get("error"),
                               fetched={"body": raw, "status": 200, "ctype": ctype, "fetched_at": str(when), "source": "library"})
        return r, last

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(one, todo))
    for r, res in results:
        report["checked"] += 1
        if res.get("error"):
            _drop(r, res["error"])
            report["dropped"].append(r["name"])
            continue
        got = res["fetched"]
        raw = got["body"]
        if res.get("from_library"):
            report["from_library_snapshot"].append(r["name"])
        rel = snapshot_rel(r.get("module_id"), r.get("vendor"), r["url"])
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(gzip.compress(raw, mtime=0))
        sha = sha256_bytes(raw)
        meta = {"schema": "gadget-listing-snapshot/1", "url": r["url"], "read_url": res["read_url"], "vendor": r.get("vendor"),
                "module_id": r.get("module_id"), "fetched_at": got["fetched_at"], "http_status": got["status"],
                "content_type": got.get("ctype"), "bytes": len(raw), "sha256": sha, "raw_file": Path(rel).name + " (gzip)",
                "parsed": {k: res.get(k) for k in ("price", "currency", "stock", "sku", "method")},
                "from_cache": got.get("source") == "cache"}
        (out / rel).with_suffix(".json").write_text(json.dumps(meta, indent=1))
        lib_price = r.get("price_inr_seen")
        r["price_inr_seen"] = res["price"]
        r["seen_on"] = got["fetched_at"]
        if res.get("sku") and not r.get("sku"):
            r["sku"] = res["sku"]
        r["price_inr_incl_gst"] = gst_incl(res["price"], r.get("gst_basis"))
        lst = r.setdefault("listing", {}) or {}
        r["listing"] = lst
        lst.update({"url": r["url"], "price_inr_seen": res["price"], "seen_on": got["fetched_at"], "stock": res.get("stock"),
                    "snapshot": rel, "snapshot_meta": str(Path(rel).with_suffix(".json")), "snapshot_sha256": sha,
                    "read_method": res.get("method"), "signed": True})
        if res.get("from_library"):
            r["notes"] = (r.get("notes") or "") + (f" The live re-read failed ({res.get('live_error')}); this receipt is over the "
                                                   f"listing foundation saved on {got['fetched_at'][:10]} ({res['read_url']}).")
        if lib_price is not None and abs(float(lib_price) - float(res["price"])) > 0.005:
            r["notes"] = (r.get("notes") or "") + (f" Library recorded {lib_price:g} INR; the listing read on "
                                                   f"{got['fetched_at'][:10]} says {res['price']:g} INR (used).")
        lst["receipt"] = sign(receipt_payload(r), key)
        r["notes"] = re.sub(r"Price seen at .*? on \S+: a retail snapshot, not reserved stock\.",
                            f"Price read from {r.get('vendor')}'s listing on {str(r['seen_on'])[:10]} and signed (build.json "
                            f"listing.receipt, raw listing {rel}): a retail snapshot, not reserved stock.", r.get("notes") or "", count=1)
        report["signed"] += 1
    report["seconds"] = round(time.monotonic() - t0, 2)
    return report


def _drop(r: dict, why: str) -> None:
    """A "seen" row whose listing could not be re-read becomes an estimate (its library date alone is not evidence)."""
    r["evidence_tier"] = "estimate"
    r["estimate"] = True
    was = r.get("price_inr_seen")
    r["price_inr_seen"] = None
    r["seen_on"] = None
    lst = r.get("listing") or {}
    lst["signed"] = False
    lst["receipt"] = None
    lst["recheck_error"] = why
    r["listing"] = lst
    r["notes"] = (r.get("notes") or "") + (f" The listing could not be re-read ({why}); the library's dated price "
                                           f"{was:g} INR is kept as a note only and the row is priced as an estimate."
                                           if was is not None else f" The listing could not be re-read ({why}).")
