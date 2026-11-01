"""Run the firmware stage on its own:

    python -m gstudio.firmware <spec.json> <electrical.json> <out_dir> [--no-compile] [--stories-audio] [--sign KEY.pem]

Writes <out_dir>/firmware/ and <out_dir>/firmware-summary.json. The spec is used as given (no normalize),
so pass a normalized spec or one whose features already match electrical.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class _Ctx:
    def __init__(self, spec, electrical, out, options):
        self.spec, self.electrical, self.out, self.options = spec, electrical, Path(out), options
        self.lib, self.artifacts, self.warnings = None, {}, []
        self.out.mkdir(parents=True, exist_ok=True)

    def progress(self, stage, msg, pct=None):
        print(f"[{stage}] {'' if pct is None else f'{pct:5.1f}% '}{msg}", file=sys.stderr, flush=True)

    def warn(self, stage, msg):
        self.warnings.append({"stage": stage, "msg": msg})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gstudio.firmware")
    ap.add_argument("spec")
    ap.add_argument("electrical")
    ap.add_argument("out")
    ap.add_argument("--no-compile", action="store_true")
    ap.add_argument("--stories-audio", action="store_true")
    ap.add_argument("--sign", help="ECDSA P-256 private key (PEM) for signed OTA")
    a = ap.parse_args(argv)
    from . import run
    from ..errors import StageError

    spec = json.loads(Path(a.spec).read_text())
    elec = json.loads(Path(a.electrical).read_text())
    opts = {"firmware_compile": not a.no_compile, "stories_audio": a.stories_audio}
    if a.sign:
        opts["ota_signing_key"] = a.sign
    ctx = _Ctx(spec, elec, a.out, opts)
    try:
        s = run(ctx)
    except StageError as e:
        print(json.dumps({"error": e.message, "details": e.details}, indent=1, default=str))
        return 2
    (Path(a.out) / "firmware-summary.json").write_text(json.dumps(s, indent=1, default=str))
    brief = {k: s[k] for k in ("template", "compiled", "sha256", "tests_passed", "tests_total", "warnings", "timings")}
    brief["size"] = {k: s["size"].get(k) for k in ("flash", "ram", "app_bin_bytes", "app_slot_used_percent")}
    print(json.dumps(brief, indent=1, default=str))
    return 0 if s["compiled"] or a.no_compile else 1


if __name__ == "__main__":
    sys.exit(main())
