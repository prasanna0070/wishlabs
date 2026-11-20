#!/bin/bash
# Start (or restart) the studio server for a REAL "start from an image" run on the VM: real Gemini agent, real concept
# photos (Nano Banana), fake builder, local store, serving web/app/dist. Vertex calls bill the VM's own GCP project.
#   web/app/dev/image-e2e/live.sh <port> <out_dir> [pylib_dir]
# A throwaway sign-in key is written to <out_dir>/key (mode 600) and never printed; the browser run reads it:
#   KEY=$(cat <out_dir>/key) PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node web/app/dev/image-e2e/live.cjs ...
# Uploads need python-multipart (and Pillow >= 12, pillow-heif for HEIC): pass a pip --target dir as pylib_dir when
# the VM's shared venv lacks them. Stop it with: kill "$(cat <out_dir>/server.pid)" (only this PID).
set -u
APP="$(cd "$(dirname "$0")/../.." && pwd)"
STUDIO="$(cd "$APP/../.." && pwd)"
PORT=${1:?port}
OUT=$(mkdir -p "$2" && cd "$2" && pwd)
PYLIB=${3:-}
PIDF="$OUT/server.pid"
if [ -f "$PIDF" ]; then kill "$(cat "$PIDF")" 2>/dev/null; sleep 1; fi
[ -s "$OUT/key" ] || (umask 077 && openssl rand -hex 16 > "$OUT/key")
cd "$APP/../server"
ACCESS_KEY="$(cat "$OUT/key")"; export ACCESS_KEY          # exported, so it never shows in a process list
env -u BUCKET -u BUILDER_URL STUDIO_ROOT="$STUDIO" STATIC_DIR="$APP/dist" STORE_DIR="$OUT/store" \
    AGENT=gemini BUILDER=fake PHOTOS=1 SOUND=0 COOKIE_SECURE=0 FAKE_BUILD_SECONDS="${FAKE_BUILD_SECONDS:-8}" \
    PYTHONUNBUFFERED=1 ${PYLIB:+PYTHONPATH=$PYLIB} \
    nohup python -m uvicorn app.asgi:app --host 127.0.0.1 --port "$PORT" > "$OUT/server.log" 2>&1 < /dev/null &
echo $! > "$PIDF"
for i in $(seq 1 80); do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 0.25; done
echo "server pid $(cat "$PIDF") on :$PORT: $(curl -s "http://127.0.0.1:$PORT/api/health")"
