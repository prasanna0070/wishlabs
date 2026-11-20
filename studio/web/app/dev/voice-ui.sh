#!/bin/bash
# Browser end to end for the voice wish, on the VM only: writes the fake-microphone WAV (Gemini-TTS), starts a
# throwaway server on PORT that serves this checkout's web/app/dist (scripted design agent, fake builder, fresh local
# store, a random one-off access key that is NOT the studio key), runs voice-ui.cjs with real Gemini Live, then stops
# only the server PID it started and deletes the key file. Build the SPA first (cd web/app && npx vite build).
#   setsid nohup bash web/app/dev/voice-ui.sh 9201 > web/server/_out/voice-ui.log 2>&1 < /dev/null &
set -uo pipefail
PORT="${1:-9201}"
cd "$(dirname "$0")/../../.."                                  # studio/
ROOT="$PWD"
OUT="$ROOT/web/server/_out/voice-ui-$(date +%H%M%S)"
mkdir -p "$OUT"
KEYF="$(mktemp)"; chmod 600 "$KEYF"
python -c 'import secrets; print(secrets.token_hex(16))' > "$KEYF"
python web/server/scripts/voice_e2e.py --mic-wav "$OUT/mic.wav" || { rm -f "$KEYF"; exit 1; }
cd "$ROOT/web/server"
ACCESS_KEY="$(cat "$KEYF")" STORE_DIR="$OUT/store" AGENT=scripted BUILDER=fake COOKIE_SECURE=0 \
  STATIC_DIR="$ROOT/web/app/dist" FAKE_BUILD_SECONDS=2 \
  python -m uvicorn app.asgi:app --host 127.0.0.1 --port "$PORT" > "$OUT/server.log" 2>&1 < /dev/null &
SPID=$!
echo "server pid $SPID out $OUT"
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 0.5; done
cd "$ROOT"
PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers KEY_FILE="$KEYF" MIC_WAV="$OUT/mic.wav" \
  node web/app/dev/voice-ui.cjs "$PORT" "$OUT"
RC=$?
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
rm -f "$KEYF"
echo "voice-ui rc=$RC out=$OUT"
exit $RC
