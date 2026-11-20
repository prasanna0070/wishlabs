#!/bin/bash
# VM only. Serve this checkout's gstudio-web (scripted agent, fake builder) and its built SPA on 127.0.0.1:<port>
# with a local store, in "GCS mode": the store reports no local paths, so every file over 8 MiB streams exactly as it
# does from Cloud Storage on Cloud Run (chunked, no Content-Length). Prints the server PID; stop it with kill <pid>.
#   tools/hack-ready/share_serve.sh <port> <storeDir> <keyFile> [SHARE_URL]
# <keyFile> holds a throwaway local access key (chmod 600), never the real studio key.
set -euo pipefail
STUDIO="$(cd "$(dirname "$0")/../.." && pwd)"
PORT=$1; STORE=$(cd "$2" && pwd); SHARE=${4:-}
KEY="$(cat "$3")"
# an empty ACCESS_KEY would make the server read the real key from Secret Manager: refuse instead
[ -n "$KEY" ] || { echo "empty key file: $3" >&2; exit 1; }
cd "$STUDIO/web/server"
ACCESS_KEY="$KEY" STORE_DIR="$STORE" AGENT=scripted BUILDER=fake PHOTOS=0 COOKIE_SECURE=0 \
  STATIC_DIR="$STUDIO/web/app/dist" SHARE_URL="$SHARE" \
  setsid nohup /opt/gs/venv/bin/python -c '
import sys
import uvicorn
from app.store import local as L
L.LocalStore.local_path = lambda self, key: None  # behave like GCS: big files stream
uvicorn.run("app.asgi:app", host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
' "$PORT" > "$STORE/../server-$PORT.log" 2>&1 < /dev/null &
PID=$!
for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null && break; sleep 0.5; done
echo "$PID"
