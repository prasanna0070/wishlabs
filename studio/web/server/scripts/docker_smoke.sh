#!/bin/bash
# Build the gstudio-web image on the VM and smoke-test the container (needs docker; run via tools/vm.sh).
#   web/server/scripts/docker_smoke.sh          real SPA from web/app
#   STUB_SPA=1 web/server/scripts/docker_smoke.sh   replace web/app with a stub (tests the server image alone)
set -euo pipefail
STUDIO="$(cd "$(dirname "$0")/../../.." && pwd)"
D=$(mktemp -d)
trap 'sudo -n docker rm -f gsw-smoke >/dev/null 2>&1 || true; rm -rf "$D"' EXIT
rsync -a --exclude node_modules --exclude _out --exclude dist --exclude .pio --exclude __pycache__ \
  --exclude engine/gstudio/cad --exclude engine/gstudio/pcb --exclude engine/gstudio/firmware \
  --exclude engine/gstudio/manual --exclude engine/gstudio/order --exclude engine/gstudio/docs_gen \
  "$STUDIO/" "$D/"
if [ "${STUB_SPA:-0}" = 1 ]; then
  rm -rf "$D/web/app" && mkdir -p "$D/web/app"
  cat > "$D/web/app/package.json" <<'JSON'
{"name": "stub", "version": "0.0.0", "scripts": {"build": "node -e \"const f=require('fs');f.mkdirSync('dist/assets',{recursive:true});f.writeFileSync('dist/index.html','<!doctype html><title>stub</title>');f.writeFileSync('dist/assets/a.js','x')\""}}
JSON
fi
cd "$D"
sudo -n docker build -q -f web/server/Dockerfile -t gstudio-web:smoke . 
sudo -n docker run -d --name gsw-smoke -p 127.0.0.1:8099:8080 -e ACCESS_KEY=k -e AGENT=scripted -e BUILDER=fake \
  -e STORE_DIR=/tmp/store gstudio-web:smoke >/dev/null
for i in $(seq 1 60); do curl -fsS 127.0.0.1:8099/api/health && break; sleep 0.5; done; echo
echo "SPA fallback: $(curl -s 127.0.0.1:8099/projects/abc | head -c 60)"
echo "asset cache: $(curl -sI 127.0.0.1:8099/assets/a.js | grep -i cache-control | tr -d '\r')"
echo "api without cookie: $(curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:8099/api/projects)"
C=$(mktemp); curl -s -c $C -H 'Content-Type: application/json' -d '{"key":"k"}' 127.0.0.1:8099/api/login >/dev/null
PID=$(curl -s -b $C -H 'Content-Type: application/json' -d '{}' 127.0.0.1:8099/api/projects | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
echo "chat events: $(curl -s -N -b $C -H 'Content-Type: application/json' -d '{"text":"a bedtime story robot"}' 127.0.0.1:8099/api/projects/$PID/chat | grep -c '^event: ')"
sudo -n docker exec gsw-smoke python -c "import sys; sys.path.insert(0,'/srv/engine'); from gstudio.pipeline import validate; print('engine validate in image:', validate({'schema':'gadget-spec/1'})['ok'] is False)"
echo "image size: $(sudo -n docker images gstudio-web:smoke --format '{{.Size}}')"
