#!/bin/bash
# Build the gstudio-web image with Cloud Build and deploy it to Cloud Run. Never builds on the laptop.
#   bash studio/deploy/web.sh            build + deploy
#   bash studio/deploy/web.sh --build    build only (prints the image)
# Prerequisites (run once by the orchestrator, NOT by this script): deploy/grant-access.sh (service account
# gstudio-sa, bucket access, Vertex AI user, secret gstudio-access-key, builder invoker) and a deployed
# gstudio-builder. This script creates no IAM bindings and no secrets.
set -euo pipefail
P=your-gcp-project
REGION=asia-south1
SVC=gstudio-web
SA=gstudio-sa@$P.iam.gserviceaccount.com
BUCKET=gstudio-projects-000000000000
STUDIO="$(cd "$(dirname "$0")/.." && pwd)"
TAG=$(date -u +%Y%m%d-%H%M%S)
IMAGE=$REGION-docker.pkg.dev/$P/gstudio/$SVC:$TAG

gcloud builds submit "$STUDIO" --project=$P --region=$REGION \
  --config="$STUDIO/deploy/web.cloudbuild.yaml" --ignore-file="$STUDIO/deploy/web.gcloudignore" \
  --substitutions=_IMAGE=$IMAGE,_LATEST=$REGION-docker.pkg.dev/$P/gstudio/$SVC:latest
echo "image: $IMAGE"
[ "${1:-}" = "--build" ] && exit 0

BUILDER_URL=${BUILDER_URL:-$(gcloud run services describe gstudio-builder --region=$REGION --project=$P \
  --format='value(status.url)' 2>/dev/null || true)}
if [ -z "$BUILDER_URL" ]; then
  echo "gstudio-builder is not deployed; set BUILDER_URL or deploy the builder first" >&2; exit 1
fi

# Values kept from the live service when this shell does not set them, because --set-env-vars below replaces every
# env var: SHARE_URL (the sign-in page's "See a real build" link, hidden when empty; pass
# SHARE_URL=https://.../studio/s/<token> to set it) and LIVE_ALLOWED_ORIGINS (set by hand in an emergency with
# gcloud run services update --update-env-vars, see docs/hackathon/DEPLOY-TONIGHT.md).
LIVE_ENV_JSON=$(gcloud run services describe $SVC --region=$REGION --project=$P --format=json 2>/dev/null || true)
live_env() {
  printf '%s' "$LIVE_ENV_JSON" | python3 -c 'import json, sys
try:
    env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env") or []
    print(next((e.get("value", "") for e in env if e.get("name") == sys.argv[1]), ""))
except Exception:
    pass' "$1" || true
}
SHARE_URL=${SHARE_URL:-$(live_env SHARE_URL)}
LIVE_ALLOWED_ORIGINS=${LIVE_ALLOWED_ORIGINS:-$(live_env LIVE_ALLOWED_ORIGINS)}

# MAJ-33: story_player builds carry name_audio_url = $PUBLIC_URL/api/device/<token>/name-audio (empty on a first deploy)
PUBLIC_URL=${PUBLIC_URL:-$(gcloud run services describe $SVC --region=$REGION --project=$P \
  --format='value(status.url)' 2>/dev/null || true)}

# Optional feature knobs (voice wish, gadget sound), passed only when set in this shell. --set-env-vars below replaces
# every env var, so a knob set by hand on the service is lost on the next deploy: set it here instead. No commas.
#   LIVE_ENABLED=0 bash studio/deploy/web.sh          voice wish off
#   LIVE_NO_BARGE_IN=1 bash studio/deploy/web.sh      noisy room: Live finishes its line even when it hears sound
#   SOUND=0 bash studio/deploy/web.sh                 gadget sound off
#   LYRIA_MODEL=lyria-002 bash studio/deploy/web.sh   Lyria 2 only
KNOBS=""
for k in LIVE_ENABLED LIVE_MODEL LIVE_LOCATION LIVE_FALLBACK_MODEL LIVE_ALLOWED_ORIGINS LIVE_MAX_SECONDS \
         LIVE_NO_BARGE_IN LIVE_START_SENSITIVITY SOUND LYRIA_MODEL LYRIA_LOCATION LYRIA_FALLBACK SOUND_DAILY_CAP \
         SOUND_SESSION_HOURLY; do
  v="${!k:-}"
  [ -n "$v" ] || continue
  case "$v" in *,*) echo "$k must not contain a comma" >&2; exit 1 ;; esac
  KNOBS="$KNOBS,$k=$v"
done
[ -z "$KNOBS" ] || echo "feature knobs: ${KNOBS#,}"

gcloud run deploy $SVC --project=$P --region=$REGION --image=$IMAGE \
  --service-account=$SA --allow-unauthenticated \
  --cpu=1 --memory=1Gi --no-cpu-throttling --min-instances=0 --max-instances=3 --concurrency=40 \
  --timeout=3600 --execution-environment=gen2 \
  --set-env-vars="BUILDER=remote,BUILDER_URL=$BUILDER_URL,BUCKET=$BUCKET,AGENT=gemini,GOOGLE_CLOUD_PROJECT=$P${PUBLIC_URL:+,PUBLIC_URL=$PUBLIC_URL}${SHARE_URL:+,SHARE_URL=$SHARE_URL}$KNOBS" \
  --set-secrets="ACCESS_KEY=gstudio-access-key:latest"
URL=$(gcloud run services describe $SVC --region=$REGION --project=$P --format='value(status.url)')
echo "deployed: $URL"
curl -fsS "$URL/api/health" && echo
