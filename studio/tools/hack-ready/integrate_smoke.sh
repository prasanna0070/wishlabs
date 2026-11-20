#!/bin/bash
# VM only. Integrated smoke test of this checkout on ONE server (127.0.0.1:<port>) in GCS mode (share_serve.sh:
# scripted design agent, fake builder, a local store whose big files stream as they do from Cloud Storage), with real
# Gemini Live, Gemini-TTS and Lyria called from the VM's service account:
#   1. login with a throwaway key (never the studio key) and /api/session;
#   2. a Live voice session over /api/voice/live with synthesized speech (web/server/scripts/voice_e2e.py) that must
#      create a project with its first chat turn, the voice record, the stored recording (owner and share link) and
#      the "live" AI receipt;
#   3. RENDERING CHECK ONLY: that voice record, recording and Live receipt are copied onto the demo copy, so the share
#      page can be checked with every section at once (the real demo design was typed, not spoken);
#   4. one Lyria composition on the demo copy (POST /api/projects/<pid>/sound), polled until ready;
#   5. the share page at 1440x900 and 390x844 (share_shots.cjs) and the sound card, owner and share
#      (web/server/scripts/sound_ui_check.cjs);
#   6. the demo's export (47.9 MB) through the share link: chunked, byte count and sha256 against the store.
# Costs one Live session, about 6 TTS lines and one Lyria call (about USD 0.1 in all). Stops only the PID it started
# and deletes its key files.
#   tools/hack-ready/integrate_smoke.sh <port> <srcStore> <outDir>
# <srcStore>: a local store (projects/, shares/) holding the demo project WITH a share token (a read-only copy).
set -uo pipefail
STUDIO="$(cd "$(dirname "$0")/../.." && pwd)"
PORT=$1; SRC=$2; OUT=$3
DEMO=${DEMO_PID:-p_jfxwcznywysp}
ERRP=${ERROR_PID:-p_ncqbuf5zkfv6}
mkdir -p "$OUT" && OUT=$(cd "$OUT" && pwd)
export PATH=/opt/gs/venv/bin:$PATH
STORE="$OUT/store"
[ -d "$STORE/projects/$DEMO" ] || cp -a "$SRC" "$STORE"
KEYF="$OUT/.key"; LOGINF="$OUT/.login.json"
(umask 077; python -c 'import secrets; print(secrets.token_hex(16))' > "$KEYF"; printf '{"key":"%s"}' "$(cat "$KEYF")" > "$LOGINF")
B=http://127.0.0.1:$PORT
J="$OUT/.cookies"
SPID=
start() {
  SPID=$(bash "$STUDIO/tools/hack-ready/share_serve.sh" "$PORT" "$STORE" "$KEYF" "/studio/s/$TOK" | tail -1)
  echo "server pid $SPID"
  curl -s -c "$J" -o /dev/null -w 'login %{http_code}\n' -H 'Content-Type: application/json' -d @"$LOGINF" "$B/api/login"
}
stop() {
  if [ -n "$SPID" ]; then
    kill "$SPID" 2>/dev/null
    for _ in $(seq 1 40); do kill -0 "$SPID" 2>/dev/null || break; sleep 0.25; done
    SPID=
  fi
}
trap 'stop; rm -f "$KEYF" "$LOGINF" "$J"' EXIT
tok_of() { python3 -c 'import json, sys
try:
    print((json.load(open(sys.argv[1])).get("share") or {}).get("token") or "")
except Exception:
    print("")' "$STORE/projects/$1/project.json"; }
TOK=$(tok_of "$DEMO"); ERR_TOK=$(tok_of "$ERRP")
[ -n "$TOK" ] || { echo "the demo copy has no share token" >&2; exit 2; }
echo "demo $DEMO share $TOK; error project share ${ERR_TOK:-none}"

echo "== 1. login"
start
echo "session: $(curl -s -b "$J" "$B/api/session")"

echo "== 2. voice wish over Gemini Live"
E2E_LINES='["Hi! I want a small desk gadget that glows a calm colour and shows a countdown on a little screen when I press a button, for my focus sessions.", "It is for me, on my work desk. Twenty-five minute sessions, with a soft glow and nothing loud.", "Yes, that is right. Please go ahead and make it.", "Yes, go ahead and make it now.", "That is everything. Please make it."]' \
  BASE="$B" KEY_FILE="$KEYF" OUT="$OUT/voice" python "$STUDIO/web/server/scripts/voice_e2e.py" > "$OUT/voice.log" 2>&1
VRC=$?
VPID=$(python3 -c 'import json, sys
try:
    print(json.load(open(sys.argv[1])).get("project_id") or "")
except Exception:
    print("")' "$OUT/voice/result.json")
echo "voice rc=$VRC project=${VPID:-none}"

echo "== 3. copy the spoken wish onto the demo copy (rendering check only)"
stop
if [ -n "$VPID" ]; then
  python3 - "$STORE" "$VPID" "$DEMO" "$STUDIO/web/server" <<'PY'
import json, shutil, sys
from pathlib import Path
store, vpid, demo = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path.insert(0, sys.argv[4])
from app.usage import apply_turn
vp = json.loads((store / "projects" / vpid / "project.json").read_text())
path = store / "projects" / demo / "project.json"
dp = json.loads(path.read_text())
voice = json.loads(json.dumps(vp["voice"]).replace(f"/api/projects/{vpid}/", f"/api/projects/{demo}/"))
dp["voice"] = voice
src = store / "projects" / vpid / "voice" / "wish.wav"
if src.exists():
    (store / "projects" / demo / "voice").mkdir(exist_ok=True)
    shutil.copy2(src, store / "projects" / demo / "voice" / "wish.wav")
live = [t for t in (vp.get("usage") or {}).get("turns") or [] if t.get("kind") == "live"]
for t in live:
    apply_turn(dp, t)
path.write_text(json.dumps(dp, indent=1, ensure_ascii=False))
print(json.dumps({"voice_turns": len(voice.get("turns") or []), "recording": src.exists(), "live_receipts": len(live)}))
PY
fi

echo "== 4. Lyria composition on the demo copy"
start
curl -s -b "$J" -X POST -H 'Content-Type: application/json' -d '{}' -o "$OUT/sound-post.json" \
  -w 'compose %{http_code}\n' "$B/api/projects/$DEMO/sound"
T0=$(date +%s); ST=
for _ in $(seq 1 90); do
  curl -s -b "$J" -o "$OUT/sound-state.json" "$B/api/projects/$DEMO/sound"
  ST=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("status"))' "$OUT/sound-state.json" 2>/dev/null)
  [ "$ST" = ready ] || [ "$ST" = error ] && break
  sleep 2
done
echo "sound status=$ST after $(( $(date +%s) - T0 )) s"
python3 - "$OUT/sound-state.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
c = ((s.get("sound") or {}).get("current")) or {}
print(json.dumps({"status": s.get("status"), "model": c.get("model"), "model_seconds": c.get("model_seconds"),
                  "theme": {k: (c.get("theme") or {}).get(k) for k in ("seconds", "bytes")},
                  "chime": {k: (c.get("chime") or {}).get(k) for k in ("seconds", "bytes", "onset_s")},
                  "limits": s.get("limits"), "error": s.get("error")}, default=str))
PY
echo "share JSON: voice=$(curl -s "$B/api/share/$TOK" | python3 -c 'import json, sys; p = json.load(sys.stdin); print(bool(p.get("voice")), "sound=", ((p.get("sound") or {}).get("current") or {}).get("id"), "audio_url=", (p.get("voice") or {}).get("audio_url"))')"

echo "== 5a. share page screenshots"
PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers node "$STUDIO/tools/hack-ready/share_shots.cjs" "$B" "$TOK" "$OUT/shots" $ERR_TOK \
  > "$OUT/shots.log" 2>&1
echo "share_shots rc=$?"
echo "== 5b. sound card, owner and share"
printf '{"pid":"%s","share":"%s"}' "$DEMO" "$TOK" > "$OUT/sound-e2e.json"
NODE_PATH=/opt/gs/pw/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers BASE="$B" KEY_FILE="$KEYF" \
  E2E="$OUT/sound-e2e.json" OUTDIR="$OUT/sound-ui" node "$STUDIO/web/server/scripts/sound_ui_check.cjs" \
  > "$OUT/sound-ui.log" 2>&1
echo "sound_ui_check rc=$?"

echo "== 6. export through the share link"
curl -s -I "$B/api/share/$TOK/v/1/export" | tr -d '\r' | grep -iE '^(HTTP|content-length|content-type|content-disposition)' | sed 's/^/  HEAD /'
curl -s -D "$OUT/export-headers.txt" -o "$OUT/export.zip" -w '  GET %{http_code} %{size_download} bytes in %{time_total} s\n' \
  "$B/api/share/$TOK/v/1/export"
grep -iE '^(transfer-encoding|content-length|content-encoding)' "$OUT/export-headers.txt" | tr -d '\r' | sed 's/^/  /'
A=$(sha256sum < "$OUT/export.zip" | cut -c1-64); S=$(sha256sum < "$STORE/projects/$DEMO/v1/export.zip" | cut -c1-64)
[ "$A" = "$S" ] && echo "  sha256 matches the store ($A)" || echo "  sha256 MISMATCH $A vs $S"
rm -f "$OUT/export.zip"
stop
echo "== done"
