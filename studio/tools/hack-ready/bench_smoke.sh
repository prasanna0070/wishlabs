#!/bin/bash
# VM only. Free plumbing check of bench.py against THIS checkout's real web server, with the scripted agent and the
# fake builder (no model calls, no engine): --base + --key-file, the chat SSE flow, share links, then --resume with
# all three example cases and --jobs 2 (the finished case must be skipped).
#   tools/hack-ready/bench_smoke.sh <port> [outDir]        (default outDir: studio/_out/bench/smoke)
# Uses a throwaway access key in a chmod 600 file (never the studio key), deleted at the end; stops only the server
# PID it started.
set -u
PORT=${1:?usage: bench_smoke.sh <port> [outDir]}
cd "$(dirname "$0")/../.."          # studio/
D=${2:-$PWD/_out/bench/smoke}
D=$(mkdir -p "$D" && cd "$D" && pwd)
rm -rf "$D/run" "$D/store"
if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then echo "port $PORT is in use" >&2; exit 2; fi
(umask 077; python -c 'import secrets; print(secrets.token_hex(16))' > "$D/.key")
cd web/server
ACCESS_KEY="$(cat "$D/.key")" STORE_DIR="$D/store" AGENT=scripted BUILDER=fake PHOTOS=0 COOKIE_SECURE=0 \
  FAKE_BUILD_SECONDS=6 setsid nohup python -m uvicorn app.asgi:app --host 127.0.0.1 --port "$PORT" \
  > "$D/server.log" 2>&1 < /dev/null &
SP=$!
cd ../..
trap '[ -n "$SP" ] && kill "$SP" 2>/dev/null; rm -f "$D/.key"' EXIT
echo "server pid $SP on :$PORT"
for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null && break; sleep 0.5; done
echo "health: $(curl -s "http://127.0.0.1:$PORT/api/health")"
python tools/hack-ready/bench.py --base "http://127.0.0.1:$PORT" --key-file "$D/.key" \
  --cases tools/hack-ready/bench_cases.example.json --only focus-lamp --out "$D/run" > "$D/run1.log" 2>&1
echo "run 1 (focus-lamp only) rc=$?: $(grep '^score:' "$D/run1.log")"
python tools/hack-ready/bench.py --base "http://127.0.0.1:$PORT" --key-file "$D/.key" \
  --cases tools/hack-ready/bench_cases.example.json --jobs 2 --out "$D/run" --resume > "$D/run2.log" 2>&1
echo "run 2 (--resume, all, --jobs 2) rc=$?: $(grep '^score:' "$D/run2.log")"
grep -E 'skipped|signed in' "$D/run2.log" | sed 's/^/  /'
kill "$SP"
for _ in $(seq 1 40); do kill -0 "$SP" 2>/dev/null || break; sleep 0.25; done
kill -0 "$SP" 2>/dev/null && echo "server $SP still alive" || { echo "server $SP stopped"; SP=; }
echo "summary: $D/run/summary.md"
