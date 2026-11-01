"""Hash-keyed PCB stage (REVIEW-1 MIN-36): a version whose board inputs did not change reuses the previous board.

The key covers everything the PCB stage's files depend on: backplane-v1 (outline, placement, footprints, support
parts), the populated slots, the electrical nets, the project slug (printed on the bottom silk), the routing options,
the module footprint table and this package's own source. Project name, version number and date are NOT in the key
and are not printed on the board, so a colour or shape edit gives the same key and the same files.

Where a reusable board is looked for, in order:
  1. `<out>/pcb` itself (a rerun of the same version);
  2. each `options["pcb_reuse_from"]` entry: a version out dir or its pcb/ dir, local or `gs://bucket/prefix`;
  3. `<out>/.gstudio/previous/pcb` (the pipeline archives the previous run there);
  4. `options["pcb_cache_dir"]` or $GSTUDIO_PCB_CACHE: `<cache>/<key>/`, written after every fresh build.
A candidate is used only when its pcb-summary.json has the same `pcb_key`, says the board is routed, and every file
it lists still has the recorded sha256.
"""

from __future__ import annotations

import hashlib
import re
import json
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

SUMMARY = "pcb-summary.json"
HERE = Path(__file__).resolve().parent
ROUTE_OPTIONS = ("fast", "route_passes", "route_timeout")


@lru_cache(maxsize=1)
def code_hash() -> str:
    """Hash of this package's source, taken once per process: the code this process imported is the code that builds
    (a tree sync during a long run must not change the key between two builds of the same process)."""
    h = hashlib.sha256()
    for p in sorted(list(HERE.glob("*.py")) + [HERE / "parts.json"]):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _norm_nets(electrical: dict) -> dict:
    from .design import electrical_nets

    return {k: sorted(map(str, v)) for k, v in sorted(electrical_nets(electrical).items())}


def pcb_key(backplane_raw: dict, electrical: dict, slug: str, options: Optional[dict] = None,
            lib_footprints: Optional[dict] = None) -> str:
    opts = options or {}
    blob = {
        "bp": hashlib.sha256(json.dumps(backplane_raw, sort_keys=True).encode()).hexdigest(),
        "slots": sorted(str(s) for s in (electrical.get("slots_used") or [])),
        "nets": _norm_nets(electrical),
        "slug": slug,
        "route": {k: opts.get(k) for k in ROUTE_OPTIONS},
        "footprints": dict(sorted((lib_footprints or {}).items())),
        "code": code_hash(),
    }
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()[:20]


_ABS = re.compile(rb"/(?:Users|home|tmp|private|work|workspace)/[^\s\"'()<>]*?/((?:pcb|library|engine)/)")
_HOME = re.compile(rb"/(?:Users|home)/[A-Za-z0-9._-]+/")  # anything else under a home directory -> "~/"
_TEXT_SUFFIXES = (".log", ".dsn", ".net", ".ses", ".json", ".kicad_pro", ".kicad_prl", ".kicad_sch", ".kicad_pcb", ".kicad_dru",
                  ".csv", ".txt", ".md", ".svg", "fp-lib-table", "sym-lib-table")


def scrub_paths(pcb_dir: Path) -> int:
    """REVIEW-1 MIN-19 (integration): rewrite absolute build-machine paths in the PCB text outputs as paths relative to the
    version folder (".../pcb/board.dsn" -> "pcb/board.dsn"), so no delivered file names a home directory. Gerbers and
    drill files carry no paths and are never touched. Returns the number of files changed."""
    n = 0
    for p in sorted(Path(pcb_dir).rglob("*")):
        if not p.is_file() or not (p.name.endswith(_TEXT_SUFFIXES)) or p.stat().st_size > 32 * 1024 * 1024:
            continue
        b = p.read_bytes()
        nb = _HOME.sub(b"~/", _ABS.sub(rb"\1", b))
        if nb != b:
            p.write_bytes(nb)
            n += 1
    return n


def scrub_obj(obj):
    """The same rewrite for a JSON-able summary dict."""
    import json as _json

    return _json.loads(_HOME.sub(b"~/", _ABS.sub(rb"\1", _json.dumps(obj).encode())).decode())


def file_hashes(pcb_dir: Path) -> dict[str, str]:
    out = {}
    for p in sorted(pcb_dir.rglob("*")):
        if p.is_file() and p.name != SUMMARY:
            out[str(p.relative_to(pcb_dir))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _valid(pcb_dir: Path, key: str) -> Optional[dict]:
    sp = pcb_dir / SUMMARY
    if not sp.is_file():
        return None
    try:
        s = json.loads(sp.read_text())
    except ValueError:
        return None
    if s.get("pcb_key") != key or not s.get("routed") or not isinstance(s.get("file_sha256"), dict):
        return None
    for rel, digest in s["file_sha256"].items():
        f = pcb_dir / rel
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            return None
    return s


def _as_pcb_dir(p: Path) -> Path:
    return p / "pcb" if (p / "pcb" / SUMMARY).exists() else p


def _gcs_fetch(url: str, dest: Path, key: str, client: Any = None) -> Optional[Path]:
    """Download gs://bucket/prefix[/pcb]/ into dest when its pcb-summary.json has this key. None otherwise."""
    if client is None:
        from google.cloud import storage  # lazy: only the builder has it

        client = storage.Client()
    bucket_name, _, prefix = url[len("gs://"):].partition("/")
    bucket = client.bucket(bucket_name)
    prefix = prefix.rstrip("/")
    for pre in (f"{prefix}/pcb", prefix):
        blob = bucket.blob(f"{pre}/{SUMMARY}")
        if not blob.exists():
            continue
        s = json.loads(blob.download_as_bytes())
        if s.get("pcb_key") != key:
            return None
        dest.mkdir(parents=True, exist_ok=True)
        for rel in [*s.get("file_sha256", {}), SUMMARY]:
            f = dest / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            bucket.blob(f"{pre}/{rel}").download_to_filename(str(f))
        return dest
    return None


def candidates(out: Path, options: dict) -> list[tuple[str, Path | str]]:
    srcs: list[tuple[str, Path | str]] = [("this version", Path(out) / "pcb")]
    extra = options.get("pcb_reuse_from") or []
    for e in [extra] if isinstance(extra, (str, Path)) else list(extra):
        srcs.append((str(e), str(e) if str(e).startswith("gs://") else _as_pcb_dir(Path(e))))
    srcs.append(("previous run", Path(out) / ".gstudio" / "previous" / "pcb"))
    cache = options.get("pcb_cache_dir") or os.environ.get("GSTUDIO_PCB_CACHE")
    if cache:
        srcs.append(("cache", Path(cache)))
    return srcs


def find(out: Path, key: str, options: dict, gcs_client: Any = None,
         log: Callable[[str], None] = lambda m: None) -> Optional[tuple[str, Path, dict]]:
    """(label, pcb_dir, summary) of the first valid reusable board for this key, else None."""
    for label, src in candidates(out, options):
        if label == "cache":
            src = Path(src) / key
        if isinstance(src, str):  # gs://
            try:
                tmp = Path(tempfile.mkdtemp(prefix="pcb-reuse-"))
                got = _gcs_fetch(src, tmp / "pcb", key, gcs_client)
            except Exception as exc:  # noqa: BLE001 - reuse is an optimisation; never fail the stage over it
                log(f"reuse: could not read {src}: {exc}")
                continue
            if got is None:
                continue
            src = got
        s = _valid(Path(src), key)
        if s is not None:
            return label, Path(src), s
    return None


def adopt(src: Path, pcb_dir: Path) -> None:
    """Make pcb_dir a byte-identical copy of src (no-op when they are the same folder)."""
    if src.resolve() == pcb_dir.resolve():
        return
    if pcb_dir.exists():
        shutil.rmtree(pcb_dir)
    shutil.copytree(src, pcb_dir)


def store(pcb_dir: Path, key: str, options: dict) -> Optional[Path]:
    cache = options.get("pcb_cache_dir") or os.environ.get("GSTUDIO_PCB_CACHE")
    if not cache:
        return None
    dest = Path(cache) / key
    tmp = Path(cache) / f".{key}.tmp"
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(pcb_dir, tmp)
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    except OSError:
        return None
    return dest


__all__ = ["pcb_key", "code_hash", "file_hashes", "find", "adopt", "store", "candidates"]
