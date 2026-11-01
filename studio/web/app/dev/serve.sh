#!/bin/bash
# Start (or restart) the gstudio-web backend on the VM in dev mode (scripted agent, fake builder), serving dist/.
#   web/app/dev/serve.sh [port] [studio_root]
# Logs: web/app/_out/server-<port>.log ; store: web/app/_out/store-<port>/
set -u
APP="$(cd "$(dirname "$0")/.." && pwd)"
PORT=${1:-8090}
ROOT=${2:-}
OUT="$APP/_out"; mkdir -p "$OUT"
PIDF="$OUT/server-$PORT.pid"
if [ -f "$PIDF" ]; then kill "$(cat "$PIDF")" 2>/dev/null; sleep 1; fi
cd "$APP/../server"
env STORE_DIR="$OUT/store-$PORT" AGENT=scripted BUILDER=fake ACCESS_KEY=dev PHOTOS=0 \
    FAKE_BUILD_SECONDS="${FAKE_BUILD_SECONDS:-6}" STATIC_DIR="$APP/dist" ${ROOT:+STUDIO_ROOT=$ROOT} \
    nohup /opt/gs/venv/bin/uvicorn app.asgi:app --host 127.0.0.1 --port "$PORT" > "$OUT/server-$PORT.log" 2>&1 &
echo $! > "$PIDF"
for i in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 0.25; done
curl -s "http://127.0.0.1:$PORT/api/health"; echo
