#!/bin/bash
# Voice wish end to end on the VM: a throwaway server on PORT (scripted design agent, fake builder, a fresh local store,
# a random one-off access key that is NOT the studio key) plus voice_e2e.py with real Gemini Live and Gemini-TTS.
# Stops only the server PID it started and deletes the key file. Run from studio/:
#   setsid nohup bash web/server/scripts/voice_e2e.sh 9201 > web/server/_out/voice-e2e.log 2>&1 < /dev/null &
set -uo pipefail
PORT="${1:-9201}"
cd "$(dirname "$0")/.."                                   # web/server
OUT="_out/voice-e2e-$(date +%H%M%S)"
mkdir -p "$OUT"
KEYF="$(mktemp)"; chmod 600 "$KEYF"
python -c 'import secrets; print(secrets.token_hex(16))' > "$KEYF"
ACCESS_KEY="$(cat "$KEYF")" STORE_DIR="$PWD/$OUT/store" AGENT=scripted BUILDER=fake COOKIE_SECURE=0 \
  STATIC_DIR=/nonexistent LIVE_MAX_SECONDS="${LIVE_MAX_SECONDS:-180}" \
  python -m uvicorn app.asgi:app --host 127.0.0.1 --port "$PORT" > "$OUT/server.log" 2>&1 < /dev/null &
SPID=$!
echo "server pid $SPID out $OUT"
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 0.5; done
BASE="http://127.0.0.1:$PORT" KEY_FILE="$KEYF" OUT="$OUT" python scripts/voice_e2e.py
RC=$?
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
rm -f "$KEYF"
echo "e2e rc=$RC out=$OUT"
exit $RC
