#!/bin/bash
# Exercise the whole benchmark flow with curl against a running server.
#   web/server/scripts/curl_flow.sh [base_url] [access_key]
set -euo pipefail
B=${1:-http://127.0.0.1:8080}; KEY=${2:-gstudio-dev}
J=$(mktemp -d); C=$J/cookies
say() { printf '\n== %s\n' "$*"; }
events() { grep '^event: ' | sed 's/event: //' | sort | uniq -c | sort -rn | tr '\n' ' '; echo; }
sse() { curl -sS -N -b $C -H 'Content-Type: application/json' -X POST "$B/api/projects/$PID/chat" -d "$1" > $J/last.sse; events < $J/last.sse; }
lastdata() { grep "^event: $1\$" -A1 $J/last.sse | grep '^data: ' | tail -1 | sed 's/^data: //'; }
py() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

say health;   curl -fsS $B/api/health; echo
say "401 without cookie"; curl -s -o /dev/null -w '%{http_code}\n' $B/api/projects
say login;    curl -fsS -c $C -H 'Content-Type: application/json' -d "{\"key\":\"$KEY\"}" $B/api/login; echo
PID=$(curl -fsS -b $C -H 'Content-Type: application/json' -d '{}' $B/api/projects | py 'print(d["id"])')
say "project $PID: prompt"
sse '{"text":"I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters."}'
lastdata choices | py 'm=d["message"]; print(m["text"]); print(" defaults:", m["payload"].get("defaults")); [print(" -", q["id"], q["question"], q["options"]) for q in m["payload"]["questions"]]'
ANS=$(lastdata choices | py 'qs=d["message"]["payload"]["questions"]; print(json.dumps({"answers":[{"question_id":q["id"],"value":q["options"][min(1,len(q["options"])-1)]} for q in qs]}))')
say "answers $ANS"; sse "$ANS"
lastdata concepts | py 'print(d["message"]["text"]); [print(" -", c["tier"], c["id"], c["title"], c["state"], c["render_url"], c["photo_url"]) for c in d["concepts"]]'
CID=$(lastdata concepts | py 'print(d["concepts"][0]["id"])')
URL=$(lastdata concepts | py 'print(d["concepts"][0]["render_url"])')
curl -fsS -b $C -o $J/render.png -w 'render: %{http_code} %{content_type} %{size_download} bytes\n' "$B$URL"
say "select $CID"; sse "{\"action\":\"select_concept\",\"concept_id\":\"$CID\"}"
lastdata brief | py 'b=d["brief"]; print(d["message"]["text"]); print(" summary:", b["summary"]); print(" size:", b["size"]["value"]); print(" functions:", [(f["title"], f["source"]) for f in b["functions"]]); print(" parts:", [(p["name"][:30], p["qty"]) for p in b["parts"]][:8]); print(" details:", b["details"]); print(" limits:", [x["value"] for x in b["limits"]][:3])'
say "Build this"; sse '{"action":"approve_brief"}'
grep '^event: build.message$' -A1 $J/last.sse | grep '^data: ' | sed 's/^data: //' | py 'print(" line:", d["text"])' 2>/dev/null | head -3 || true
grep '^event: build.message$' -A1 $J/last.sse | grep '^data: ' | head -3 | sed 's/^data: /  /'
grep '^event: build.progress$' -A1 $J/last.sse | grep -o '"draft_url":"[^"]*"' | sort -u | head -2
lastdata result | py 'print(d["message"]["text"]); print(" version:", d["version"]["n"], d["version"]["state"], d["version"]["summary"])'
say files
for f in build.json spec.json cad/assembly.glb order/quote.json BUILD_GUIDE.md; do
  curl -sS -b $C -o /dev/null -w "$f: %{http_code} %{content_type} %{size_download} B cache=" "$B/api/projects/$PID/v/1/files/$f"
  curl -sS -b $C -I "$B/api/projects/$PID/v/1/files/$f" | grep -i '^cache-control' | tr -d '\r' | cut -d' ' -f2- || echo "?"
done
curl -sS -b $C -o $J/export.zip -w 'export: %{http_code} %{size_download} B\n' "$B/api/projects/$PID/v/1/export"
say "part edit"
COMP=$(curl -fsS -b $C "$B/api/projects/$PID/v/1/files/cad/components.json" | py 'print(next(k for k,v in d.items() if str(v.get("spec_path","")).startswith("features[")))')
curl -sS -N -b $C -H 'Content-Type: application/json' -X POST "$B/api/projects/$PID/parts/$COMP/edit" -d '{"instruction":"move it up a little"}' > $J/last.sse; events < $J/last.sse
lastdata result | py 'print(" version:", d["version"]["n"], d["version"]["label"], d["version"]["state"])'
say "edit by chat"; sse '{"text":"Make it pink please"}'
lastdata result | py 'print(" version:", d["version"]["n"], d["version"]["label"], d["version"]["state"])'
say tweak
curl -sS -b $C -H 'Content-Type: application/json' -d '{"patch":{"body":{"edge_round":10}},"scale":1.05}' -w ' -> %{http_code}\n' $B/api/projects/$PID/tweak | cut -c1-300
curl -fsS -b $C -H 'Content-Type: application/json' -d '{"patch":{"colors":{"body":"#9CC3E6","body_name":"sky blue"}}}' $B/api/projects/$PID/tweak | py 'print(" tweak ->", d["version"]["n"], d["version"]["label"])'
curl -sS -N -b $C "$B/api/projects/$PID/stream" | events
curl -sS -b $C -H 'Content-Type: application/json' -d '{"patch":{"features":{"nope":{"at":[0,1]}}}}' -w ' -> %{http_code}\n' $B/api/projects/$PID/tweak
say "undo/redo"
curl -fsS -b $C -X POST $B/api/projects/$PID/undo | py 'print(" undo -> current", d["current_version"])'
curl -fsS -b $C -X POST $B/api/projects/$PID/redo | py 'print(" redo -> current", d["current_version"])'
say restore; curl -fsS -b $C -X POST $B/api/projects/$PID/versions/1/restore | py 'print(" restore ->", d["n"], d["label"])'
sleep 1; curl -fsS -b $C $B/api/projects/$PID/versions | py 'print(" versions:", [(v["n"], v["source"], v["state"]) for v in d["versions"]], "current", d["current_version"])'
say "order request"
curl -fsS -b $C -H 'Content-Type: application/json' -d '{"pincode":"500032","quantity":1,"note":"test"}' $B/api/projects/$PID/order-request | py 'print(" ", d["request"]["id"], d["request"]["status"], "|", d["request"]["status_text"][:60], "| quote:", json.dumps(d["quote"])[:120])'
say share
TOK=$(curl -fsS -b $C -X POST $B/api/projects/$PID/share | py 'print(d["token"])')
curl -fsS $B/api/share/$TOK | py 'print(" shared read_only:", d["read_only"], "stage", d["stage"], "concept url", d["concepts"][0]["render_url"][:40])'
say "final project"; curl -fsS -b $C $B/api/projects/$PID | py 'print(" stage", d["stage"], "chat", len(d["chat"]), "msgs:", [m["kind"] for m in d["chat"]])'
echo; echo "pid=$PID tmp=$J"
