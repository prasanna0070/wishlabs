#!/bin/bash
# VM only. Build the gstudio-web image from exactly the files deploy/web.sh uploads (gcloud's own upload list for
# deploy/web.gcloudignore) with the same Dockerfile, then run it on 127.0.0.1:<port> with a throwaway key, a scratch
# store, the scripted agent and the fake builder, and check: health, the SPA with brotli, the voice and sound modules
# and their packages (numpy, lameenc, miniaudio, websockets) inside the image, and the light engine's design check
# with gstudio.cad. Stops and removes the container and the image at the end. Pushes nothing anywhere.
#   tools/hack-ready/web_image_check.sh <port> <workDir>
set -uo pipefail
STUDIO="$(cd "$(dirname "$0")/../.." && pwd)"
PORT=$1
WORK=$2
mkdir -p "$WORK" && WORK=$(cd "$WORK" && pwd)
TAG=wish-web-integ:check-$PORT           # per port: two checks on the shared VM never remove each other's image
NAME=wish-web-integ-$PORT
D="sudo -n docker"
fail() { echo "FAIL $*"; FAILS=$((FAILS + 1)); }
FAILS=0

# 1. the upload list Cloud Build would get
rm -rf "$WORK/stage" "$WORK/ctx" && mkdir -p "$WORK/stage" "$WORK/ctx"
rsync -a --exclude _out --exclude node_modules --exclude dist --exclude .venv --exclude __pycache__ --exclude .pio \
  "$STUDIO/" "$WORK/stage/"
cp "$STUDIO/deploy/web.gcloudignore" "$WORK/stage/.gcloudignore"
(cd "$WORK/stage" && gcloud meta list-files-for-upload . 2>/dev/null | grep -v '^\.gcloudignore$' | sort) > "$WORK/files.txt"
(cd "$WORK/stage" && rsync -a --files-from="$WORK/files.txt" . "$WORK/ctx/")
echo "upload: $(wc -l < "$WORK/files.txt") files, $(du -sh "$WORK/ctx" | cut -f1)"
grep -q '^engine/gstudio/cad/kits.py$\|^engine/gstudio/cad/' "$WORK/files.txt" || fail "engine/gstudio/cad not in the upload"
grep -q '^site/' "$WORK/files.txt" && fail "site/ is in the upload"

# 2. docker build (same Dockerfile and context as web.cloudbuild.yaml)
T0=$(date +%s)
if $D build -q -f "$WORK/ctx/web/server/Dockerfile" -t "$TAG" "$WORK/ctx" > "$WORK/docker-build.log" 2>&1; then
  echo "docker build ok in $(( $(date +%s) - T0 )) s"
else
  fail "docker build (see $WORK/docker-build.log)"; tail -20 "$WORK/docker-build.log"; exit 1
fi

# 3. imports inside the image
$D run --rm "$TAG" python -c '
import numpy, lameenc, miniaudio, websockets
import app.sound, app.voice_live, app.routes.sound, app.routes.voice
import PIL, multipart, pillow_heif, app.uploads, app.routes.uploads, app.agent.image_rules
from app.main import create_app
import sys; sys.path.insert(0, "/srv/engine")
import gstudio.cad, importlib.util
print("imports ok; numpy", numpy.__version__, "cadquery installed:", importlib.util.find_spec("cadquery") is not None,
      "| Pillow", PIL.__version__, "pillow-heif", pillow_heif.__version__, "python-multipart", multipart.__version__)
' > "$WORK/imports.log" 2>&1 && cat "$WORK/imports.log" || { fail "imports"; cat "$WORK/imports.log"; }

# 4. run it and probe (throwaway key; never the studio key; scripted agent; fake builder; scratch store)
KEY=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
$D rm -f "$NAME" > /dev/null 2>&1
$D run -d --name "$NAME" -p 127.0.0.1:$PORT:8080 -e ACCESS_KEY="$KEY" -e STORE_DIR=/tmp/store -e AGENT=scripted \
  -e BUILDER=fake -e COOKIE_SECURE=0 "$TAG" > /dev/null || fail "docker run"
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1 && break; sleep 0.5; done
B=http://127.0.0.1:$PORT
echo "health: $(curl -s "$B/api/health")"
code=$(curl -s -o /dev/null -w '%{http_code}' "$B/studio/"); [ "$code" = 200 ] || fail "/studio/ $code"
js=$(curl -s "$B/studio/" | grep -o '/studio/assets/index-[^"]*\.js' | head -1)
enc=$(curl -s -D - -o /dev/null -H 'Accept-Encoding: br, gzip' "$B$js" | tr -d '\r' | awk -F': ' 'tolower($1)=="content-encoding"{print $2}')
echo "main js $js content-encoding=$enc"; [ "$enc" = br ] || fail "main js not brotli"
# login, then the sound and voice routes answer (no model call: an unknown project, a WebSocket without login)
J=$(mktemp); curl -s -c "$J" -o /dev/null -w 'login %{http_code}\n' -H 'Content-Type: application/json' \
  -d "{\"key\":\"$KEY\"}" "$B/api/login"
echo "sound route (unknown project): $(curl -s -b "$J" -o /dev/null -w '%{http_code}' "$B/api/projects/p_nope/sound")"
echo "share sound (bad token): $(curl -s -o /dev/null -w '%{http_code}' "$B/api/share/s_nope/sound/x/theme.mp3")"
# start from an image: a project takes a PNG upload (normalised to JPEG) and serves it back to a signed-in owner only
PID=$(curl -s -b "$J" -H 'Content-Type: application/json' -d '{"mode":"fast"}' "$B/api/projects" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
python3 - "$WORK/probe.png" <<'PY'
import struct, sys, zlib
w = h = 16                                     # a plain orange 16 x 16 PNG, written without Pillow
raw = b"".join(b"\x00" + bytes([200, 80, 40]) * w for _ in range(h))
def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
open(sys.argv[1], "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                              + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
PY
up=$(curl -s -b "$J" -F "file=@$WORK/probe.png;type=image/png" "$B/api/projects/$PID/uploads")
echo "upload: $up"
UID_=$(printf '%s' "$up" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["id"] if d.get("mime")=="image/jpeg" else "")' 2>/dev/null)
[ -n "$UID_" ] || fail "image upload inside the image"
code=$(curl -s -b "$J" -o /dev/null -w '%{http_code}' "$B/api/projects/$PID/uploads/$UID_?thumb=1"); [ "$code" = 200 ] || fail "upload thumb $code"
code=$(curl -s -o /dev/null -w '%{http_code}' "$B/api/projects/$PID/uploads/$UID_"); [ "$code" = 401 ] || fail "upload without login $code"
ws=$(python3 - "$PORT" <<'PY'
import asyncio, sys
async def main():
    import websockets
    try:
        async with websockets.connect(f"ws://127.0.0.1:{sys.argv[1]}/api/voice/live") as ws:
            await ws.recv()
    except websockets.ConnectionClosed as e:
        print("closed", e.rcvd.code if e.rcvd else None)
    except Exception as e:  # noqa: BLE001
        print("error", type(e).__name__, str(e)[:120])
asyncio.run(main())
PY
)
# refused before accept: uvicorn answers the handshake with HTTP 403 (Starlette's test client reports it as 4401)
echo "voice socket without login: $ws"; [[ "$ws" == "closed 4401" || "$ws" == *403* ]] || fail "voice socket without login: $ws"
rm -f "$J"
$D logs "$NAME" > "$WORK/container.log" 2>&1
$D rm -f "$NAME" > /dev/null
$D rmi "$TAG" > /dev/null 2>&1
echo "FAILS=$FAILS"
exit $FAILS
