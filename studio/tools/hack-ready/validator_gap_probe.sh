#!/bin/bash
# VM only. Does the gstudio-web image's design check catch what the builder later rejects? (readiness BRIEF "Do now"
# 3b). Recreates the image's light engine twice with the Dockerfile's own copy steps, today's and one that also copies
# engine/gstudio/cad (pure-Python footprints; CadQuery is imported lazily), installs exactly the image's Python
# packages in a fresh venv, and runs gstudio.pipeline.validate (what EngineBridge.validate calls) on one spec.
#   tools/hack-ready/validator_gap_probe.sh <spec.json | inputs/v<n>.json> <workDir>
set -euo pipefail
STUDIO="$(cd "$(dirname "$0")/../.." && pwd)"
SPEC=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
WORK=$2
mkdir -p "$WORK" && WORK=$(cd "$WORK" && pwd)
if [ ! -x "$WORK/venv/bin/python" ]; then
  python3 -m venv "$WORK/venv"
  "$WORK/venv/bin/pip" install -q -r "$STUDIO/web/server/requirements.txt" shapely==2.2.0
fi
stage() {  # $1 = target dir, $2 = "cad" to also copy the cad package (the proposed Dockerfile change)
  local T=$1
  rm -rf "$T" && mkdir -p "$T/engine/gstudio" "$T/examples"
  cp "$STUDIO"/engine/gstudio/*.py "$T/engine/gstudio/"
  for d in "$STUDIO/engine/gstudio/data" "$STUDIO/engine/gstudio/library" ${2:+"$STUDIO/engine/gstudio/cad"}; do
    [ -d "$d" ] && cp -r "$d" "$T/engine/gstudio/" || true
  done
  rm -rf "$T/engine/gstudio/cad/tests"
  cp -r "$STUDIO/library" "$T/library"
}
stage "$WORK/light"
stage "$WORK/light_cad" cad
for v in light light_cad; do
  echo "== $v"
  (cd "$WORK/$v" && "$WORK/venv/bin/python" -I - "$WORK/$v/engine" "$SPEC" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
from gstudio import pipeline
spec = json.load(open(sys.argv[2]))
spec = spec.get("spec", spec)  # a build input (inputs/v<n>.json) wraps the spec
r = pipeline.validate(spec)
warn = [str(w) for w in r.get("warnings") or []]
print(json.dumps({
    "ok": bool(r.get("ok")),
    "errors": [str(e)[:220] for e in (r.get("errors") or [])][:3],
    "footprint_fallback_warning": any("fell back to envelopes" in w for w in warn),
    "cad_footprint_importable": __import__("importlib").util.find_spec("gstudio.cad") is not None,
    "cadquery_installed": __import__("importlib").util.find_spec("cadquery") is not None,
}, indent=1))
PY
  )
done
