#!/bin/bash
# Integration: verify_delivery on the three examples, CAD clean-venv regeneration (MAJ-26) and manual image regeneration
# in a clean venv (MAJ-27) on one delivery. Run on the VM from studio/:  bash tools/int/regen_checks.sh <version dir> <tag>
set -u
cd "$(dirname "$0")/../.."
V="$1"; TAG="$2"; O="_out/r1-int/regen-$TAG"
rm -rf "$O"; mkdir -p "$O"
(cd "$V" && python3 -I verify_delivery.py) > "$O/verify_delivery.txt" 2>&1; echo "verify_delivery exit $?" >> "$O/verify_delivery.txt"
(cd engine && python -m gstudio.cad.regen check "../$V" --no-build-venv) > "$O/cad-regen.txt" 2>&1; echo "cad regen exit $?" >> "$O/cad-regen.txt"
mkdir -p "$O/export" && (cd "$O/export" && unzip -q "../../../../$V/export.zip")
SLUG=$(ls "$O/export" | grep -v project-record | head -1)
if [ ! -x _out/r1-int/cleanvenv/bin/python ]; then
  python3.12 -m venv _out/r1-int/cleanvenv && _out/r1-int/cleanvenv/bin/pip install -q -r "$O/export/$SLUG/manual/render-requirements.txt"
fi
_out/r1-int/cleanvenv/bin/pip freeze > "$O/cleanvenv-freeze.txt"
(_out/r1-int/cleanvenv/bin/python "$O/export/$SLUG/manual/render_manual.py" --root "$O/export/$SLUG" --out "$O/regen" --compare --cache "$O/cache") > "$O/render-manual.txt" 2>&1
echo "render_manual exit $?" >> "$O/render-manual.txt"
tail -n 3 "$O/verify_delivery.txt" "$O/cad-regen.txt" "$O/render-manual.txt"
