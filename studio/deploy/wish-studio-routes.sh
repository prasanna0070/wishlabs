#!/bin/bash
# Wish Labs: serve the studio app on the main site, through the Plugbook load balancer (34.54.2.16).
#   https://wish.plugbook.si/            -> wishwright-labs-be (the landing page, unchanged; also /assets, /css, /js)
#   https://wish.plugbook.si/studio[/*]  -> gstudio-web-be     (the studio SPA, Cloud Run gstudio-web)
#   https://wish.plugbook.si/api/*       -> gstudio-web-be     (the studio API)
# Claude's auto mode refuses load-balancer changes, so Prasanna runs:  ! bash studio/deploy/wish-studio-routes.sh
#   --dry-run   create nothing; print the URL-map diff only
#   --check     change nothing; run the curl checks only (make-wish-live.sh uses this at the end)
# Additive and idempotent:
#   1. creates the serverless NEG gstudio-web-neg and the backend service gstudio-web-be only when missing;
#   2. exports plugbook-urlmap, replaces ONLY its "wish" path matcher (the one the wish.plugbook.si host rule names),
#      prints the diff and re-imports it; no import when nothing changes. plugbook.si, www and plugbook.quarktex.com
#      are never touched. A backup of the map is kept under studio/_out/lb/ (the rollback command is printed);
#   3. curl checks: /studio/ is the SPA (200 HTML), /api/health is 200 JSON, / is still the landing page.
# Needs deploy/wish-domain.sh to have run once (host rule wish.plugbook.si -> path matcher "wish", its certificate).
# projects/plugbook-mcp/lb-setup.sh declares the same rules, so a full re-import of the map keeps them.
set -euo pipefail
P=your-gcp-project; R=asia-south1; HOST=wish.plugbook.si; UM=plugbook-urlmap
SVC=gstudio-web; NEG=$SVC-neg; BE=$SVC-be; LANDING_BE=wishwright-labs-be
STUDIO="$(cd "$(dirname "$0")/.." && pwd)"
MODE=apply
for a in "$@"; do
  case "$a" in
    --dry-run) MODE=dry ;;
    --check) MODE=check ;;
    *) echo "usage: wish-studio-routes.sh [--dry-run|--check]" >&2; exit 2 ;;
  esac
done
g() { gcloud "$@" --project=$P --quiet; }
has() { "$@" --project=$P >/dev/null 2>&1; }

# ------------------------------------------------------------------ curl checks
FAILS=0
ok() { echo "  PASS  $*"; }
bad() { echo "  FAIL  $*"; FAILS=$((FAILS + 1)); }
fetch() {  # fetch <path> -> sets CODE, CTYPE, BODY (body capped at 200 KB)
  local out; out="$(mktemp)"
  read -r CODE CTYPE < <(curl -sS -m 30 -o "$out" -w '%{http_code} %{content_type}\n' "https://$HOST$1" 2>/dev/null || echo "000 -")
  BODY="$(head -c 200000 "$out")"; rm -f "$out"
}
checks() {  # checks [full]: full adds the landing's studio link and the old run.app redirect
  echo "== checks on https://$HOST (the URL map can take a few minutes to reach every Google front end)"
  local i tries=${CHECK_TRIES:-30}
  for i in $(seq 1 "$tries"); do  # wait until /studio/ is answered by the studio, not the landing page
    fetch /studio/
    if [ "$CODE" = 200 ] && grep -q 'id="root"' <<<"$BODY" && ! grep -q 'id="wishbox"' <<<"$BODY"; then break; fi
    [ "$i" = "$tries" ] || { echo "  ... /studio/ not routed yet (HTTP $CODE), retrying in 10 s"; sleep 10; }
  done
  if [ "$CODE" = 200 ] && [[ "$CTYPE" == text/html* ]] && grep -q 'id="root"' <<<"$BODY" && ! grep -q 'id="wishbox"' <<<"$BODY"; then
    ok "/studio/        200 $CTYPE, the studio SPA"
  else bad "/studio/        HTTP $CODE $CTYPE (expected 200 text/html with the SPA's <div id=\"root\">)"; fi
  fetch /api/health
  if [ "$CODE" = 200 ] && [[ "$CTYPE" == application/json* ]] && grep -q '"ok": *true' <<<"$BODY"; then
    ok "/api/health     200 $CTYPE $(head -c 160 <<<"$BODY" | tr -s "\n\r\t" "   ")"
  else bad "/api/health     HTTP $CODE $CTYPE $(head -c 160 <<<"$BODY" | tr -s "\n\r\t" "   ")"; fi
  fetch /
  if [ "$CODE" = 200 ] && [[ "$CTYPE" == text/html* ]] && grep -q 'id="wishbox"' <<<"$BODY"; then
    ok "/               200 $CTYPE, the landing page"
  else bad "/               HTTP $CODE $CTYPE (expected the landing page with its wish box)"; fi
  fetch /css/site.css
  if [ "$CODE" = 200 ] && [[ "$CTYPE" == text/css* ]]; then ok "/css/site.css   200 $CTYPE (landing)"
  else bad "/css/site.css   HTTP $CODE $CTYPE (expected the landing's stylesheet)"; fi
  if [ "${1:-}" = full ]; then
    fetch /js/config.js
    if [ "$CODE" = 200 ] && grep -q 'STUDIO_URL = "/studio/"' <<<"$BODY"; then ok "/js/config.js   STUDIO_URL = \"/studio/\" (landing redeployed)"
    else bad "/js/config.js   does not point at /studio/ yet (HTTP $CODE; run deploy/landing.sh, or wait up to 1 h for its cache)"; fi
    local run; run="$(gcloud run services describe $SVC --region=$R --project=$P --format='value(status.url)' 2>/dev/null || true)"
    if [ -n "$run" ]; then
      local loc; loc="$(curl -sS -m 30 -o /dev/null -w '%{http_code} %{redirect_url}' "$run/login" 2>/dev/null || true)"
      if [[ "$loc" == "307 $run/studio/login" ]]; then ok "old $run/login -> /studio/login"
      else bad "old $run/login answered '$loc' (expected 307 to /studio/login)"; fi
    fi
  fi
  if [ "$FAILS" = 0 ]; then echo "== all checks passed: https://$HOST/studio/"; else echo "== $FAILS check(s) failed"; fi
  return "$FAILS"
}
if [ "$MODE" = check ]; then checks full; exit $?; fi

# ------------------------------------------------------------------ preconditions
has gcloud run services describe $SVC --region=$R || { echo "Cloud Run $SVC is not deployed: run deploy/go-live.sh first" >&2; exit 1; }
has gcloud compute backend-services describe $LANDING_BE --global || { echo "$LANDING_BE is missing: run deploy/wish-domain.sh first" >&2; exit 1; }

# ------------------------------------------------------------------ 1. NEG + backend service (only when missing)
if [ "$MODE" = apply ]; then
  has gcloud compute network-endpoint-groups describe $NEG --region=$R || \
    g compute network-endpoint-groups create $NEG --region=$R --network-endpoint-type=serverless --cloud-run-service=$SVC
  has gcloud compute backend-services describe $BE --global || \
    g compute backend-services create $BE --global --load-balancing-scheme=EXTERNAL_MANAGED
  gcloud compute backend-services describe $BE --global --project=$P --format='value(backends[].group)' | grep -q "$NEG" || \
    g compute backend-services add-backend $BE --global --network-endpoint-group=$NEG --network-endpoint-group-region=$R
else
  echo "== dry run: would create $NEG / $BE if missing (now: NEG $(has gcloud compute network-endpoint-groups describe $NEG --region=$R && echo exists || echo missing), backend $(has gcloud compute backend-services describe $BE --global && echo exists || echo missing))"
fi

# ------------------------------------------------------------------ 2. the "wish" path matcher
OUT="$STUDIO/_out/lb"; mkdir -p "$OUT"
TS=$(date -u +%Y%m%d-%H%M%S)
BACKUP="$OUT/$UM-$TS.yaml"
g compute url-maps export $UM --global --destination="$BACKUP" >/dev/null
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
gcloud compute url-maps describe $UM --global --project=$P --format=json > "$WORK/live.json"
# Edit with the standard library only (JSON in, JSON out; gcloud's import reads JSON as YAML).
python3 -I - "$WORK/live.json" "$WORK/old.json" "$WORK/new.json" "$P" "$HOST" "$LANDING_BE" "$BE" <<'PY'
import json
import sys

live, old_out, new_out, project, host, landing_be, studio_be = sys.argv[1:8]
doc = json.load(open(live))
# Output-only fields the import schema rejects, and the fingerprint gcloud takes from the live map itself
# (lb-setup.sh imports without one too).
for k in ("id", "creationTimestamp", "selfLink", "kind", "region", "fingerprint"):
    doc.pop(k, None)
open(old_out, "w").write(json.dumps(doc, indent=2, sort_keys=True) + "\n")

def be(name):
    return f"https://www.googleapis.com/compute/v1/projects/{project}/global/backendServices/{name}"

rules = [r for r in doc.get("hostRules", []) if host in r.get("hosts", [])]
if len(rules) != 1:
    sys.exit(f"expected one host rule for {host}, found {len(rules)}: run deploy/wish-domain.sh first")
name = rules[0]["pathMatcher"]
if name != "wish":
    sys.exit(f"the {host} host rule uses path matcher {name!r}, not 'wish'; refusing to guess")
pms = doc.get("pathMatchers", [])
idx = [i for i, m in enumerate(pms) if m.get("name") == name]
if len(idx) != 1:
    sys.exit(f"path matcher {name!r} not found exactly once in the URL map")
pms[idx[0]] = {
    "name": name,
    "defaultService": be(landing_be),
    "pathRules": [{"paths": ["/studio", "/studio/*", "/api/*"], "service": be(studio_be)}],
}
open(new_out, "w").write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY
echo "== URL map $UM: diff of the change (only the \"wish\" path matcher may change)"
if cmp -s "$WORK/old.json" "$WORK/new.json"; then
  echo "   no change: $HOST already sends /studio, /studio/* and /api/* to $BE"
else
  diff -u --label "$UM (live)" --label "$UM (new)" "$WORK/old.json" "$WORK/new.json" || true
  if [ "$MODE" = dry ]; then echo "== dry run: nothing imported"; exit 0; fi
  # Guard: everything except the wish path matcher must be byte-identical.
  python3 -I - "$WORK/old.json" "$WORK/new.json" <<'PY'
import json
import sys

a, b = (json.load(open(p)) for p in sys.argv[1:3])
strip = lambda d: {**d, "pathMatchers": [m for m in d.get("pathMatchers", []) if m.get("name") != "wish"]}
if strip(a) != strip(b):
    sys.exit("refusing to import: something other than the 'wish' path matcher would change")
PY
  g compute url-maps import $UM --global --source="$WORK/new.json"
  echo "== imported. Backup of the previous map: $BACKUP"
  echo "   rollback: gcloud compute url-maps import $UM --global --project=$P --source=\"$BACKUP\""
fi
[ "$MODE" = dry ] && exit 0

# ------------------------------------------------------------------ 3. checks
checks
