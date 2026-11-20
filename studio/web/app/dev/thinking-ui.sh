#!/bin/bash
# Browser end to end for the working card, on the VM only: starts a throwaway server on PORT that serves this
# checkout's web/app/dist (fake builder, no photos, fresh local store, a random one-off access key that is NOT the
# studio key), runs thinking-ui.cjs, then stops only the server PID it started and deletes the key file.
#   AGENT=gemini (default): the real design agent on Vertex AI, Deep mode (about USD 1 of model calls)
#   AGENT=scripted: canned thought summaries over SCRIPTED_THINK_S seconds (default 14), no model calls
# Build the SPA first (cd web/app && npm run build).
#   setsid nohup bash web/app/dev/thinking-ui.sh 9220 > web/server/_out/thinking/ui.log 2>&1 < /dev/null &
set -uo pipefail
PORT="${1:-9220}"
AGENT="${AGENT:-gemini}"
cd "$(dirname "$0")/../../.."                                  # studio/
ROOT="$PWD"
OUT="${OUT:-$ROOT/web/server/_out/thinking/ui-$AGENT-$(date +%H%M%S)}"
mkdir -p "$OUT"
KEYF="$(mktemp)"; chmod 600 "$KEYF"
python -c 'import secrets; print(secrets.token_hex(16))' > "$KEYF"
cd "$ROOT/web/server"
ACCESS_KEY="$(cat "$KEYF")" STORE_DIR="$OUT/store" AGENT="$AGENT" BUILDER=fake PHOTOS=0 COOKIE_SECURE=0 \
  SCRIPTED_THINK_S="${SCRIPTED_THINK_S:-14}" STATIC_DIR="$ROOT/web/app/dist" FAKE_BUILD_SECONDS=2 \
  python -m uvicorn app.asgi:app --host 127.0.0.1 --port "$PORT" > "$OUT/server.log" 2>&1 < /dev/null &
SPID=$!
echo "server pid $SPID out $OUT"
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 0.5; done
cd "$ROOT"
PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers KEY_FILE="$KEYF" AGENT="$AGENT" STORE="$OUT/store" \
  node web/app/dev/thinking-ui.cjs "$PORT" "$OUT"
RC=$?
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
rm -f "$KEYF"
echo "thinking-ui rc=$RC out=$OUT"
exit $RC
