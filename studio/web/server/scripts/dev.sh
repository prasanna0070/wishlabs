#!/bin/bash
# Start the dev server on this machine (run it on the VM via tools/vm.sh, never on the laptop).
#   web/server/scripts/dev.sh [port]      defaults: BUILDER=fake AGENT=scripted PHOTOS=0 ACCESS_KEY=gstudio-dev
# Override any env var on the command line, e.g. AGENT=gemini PHOTOS=1 web/server/scripts/dev.sh 8081
set -euo pipefail
PORT=${1:-8080}
cd "$(dirname "$0")/.."
export BUILDER=${BUILDER:-fake} AGENT=${AGENT:-scripted} PHOTOS=${PHOTOS:-0} ACCESS_KEY=${ACCESS_KEY:-gstudio-dev}
export STORE_DIR=${STORE_DIR:-$PWD/_out/store-$PORT} FAKE_BUILD_SECONDS=${FAKE_BUILD_SECONDS:-6}
mkdir -p _out
pkill -f "uvicorn app.asgi:app --host 0.0.0.0 --port $PORT" 2>/dev/null || true
for i in $(seq 1 40); do pgrep -f "uvicorn app.asgi:app --host 0.0.0.0 --port $PORT" >/dev/null || break; sleep 0.25; done
for i in $(seq 1 40); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 || break; sleep 0.25; done
nohup uvicorn app.asgi:app --host 0.0.0.0 --port "$PORT" > "_out/dev-$PORT.log" 2>&1 &
NEW=$!
for i in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 0.25; done
kill -0 $NEW 2>/dev/null || { echo "dev server failed to start; see _out/dev-$PORT.log"; tail -20 "_out/dev-$PORT.log"; exit 1; }
curl -fsS "http://127.0.0.1:$PORT/api/health"; echo
echo "dev server on :$PORT  BUILDER=$BUILDER AGENT=$AGENT PHOTOS=$PHOTOS store=$STORE_DIR log=$PWD/_out/dev-$PORT.log"
