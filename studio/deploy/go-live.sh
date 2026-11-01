#!/bin/bash
# Wish Labs (code name gstudio): put both Cloud Run services live. Claude's auto mode refuses production deploys,
# IAM grants and secret writes, so Prasanna runs this once:   ! bash studio/deploy/go-live.sh
#
# 1. deploys gstudio-builder (private, heavy pipeline image, pinned by digest)
# 2. creates the site access key (copied to clipboard, never printed) + service-account grants
# 3. deploys gstudio-web (public URL, app-level login) from the newest built web image
# 4. lets the web service call the builder, makes the web URL reachable, health-checks both
# Re-running is safe: every step is idempotent. Images are never built here (Cloud Build already did).
set -euo pipefail
P=your-gcp-project
R=asia-south1
SA=gstudio-sa@$P.iam.gserviceaccount.com
BUCKET=gstudio-projects-000000000000
STUDIO="$(cd "$(dirname "$0")/.." && pwd)"
REPO=$R-docker.pkg.dev/$P/gstudio

latest() {  # the digest the :latest tag points at (Cloud Build tags every release build :latest)
  gcloud artifacts docker images describe "$REPO/$1:latest" --project=$P \
    --format='value(image_summary.fully_qualified_digest)'
}
BUILDER_IMG=${BUILDER_IMG:-$(latest gstudio-builder)}
WEB_IMG=${WEB_IMG:-$(latest gstudio-web)}
echo "builder image: $BUILDER_IMG"
echo "web image:     $WEB_IMG"

echo "== 1/4 deploy gstudio-builder"
gcloud run deploy gstudio-builder --image="$BUILDER_IMG" --project=$P --region=$R \
  --no-allow-unauthenticated --execution-environment=gen2 --cpu=8 --memory=32Gi --timeout=3600 \
  --concurrency=2 --min-instances=0 --max-instances=3 --cpu-boost --service-account=$SA \
  --set-env-vars=BUCKET=$BUCKET,MAX_BUILDS=2 --port=8080 --quiet
BUILDER_URL=$(gcloud run services describe gstudio-builder --region=$R --project=$P --format='value(status.url)')

echo "== 2/4 access key + grants"
bash "$STUDIO/deploy/grant-access.sh"

echo "== 3/4 deploy gstudio-web"
gcloud run deploy gstudio-web --image="$WEB_IMG" --project=$P --region=$R \
  --service-account=$SA --no-allow-unauthenticated \
  --cpu=1 --memory=1Gi --no-cpu-throttling --min-instances=0 --max-instances=3 --concurrency=40 \
  --timeout=3600 --execution-environment=gen2 \
  --set-env-vars="BUILDER=remote,BUILDER_URL=$BUILDER_URL,BUCKET=$BUCKET,AGENT=gemini,GOOGLE_CLOUD_PROJECT=$P" \
  --set-secrets="ACCESS_KEY=gstudio-access-key:latest" --quiet

echo "== 4/4 invoker + public web binding, health checks"
bash "$STUDIO/deploy/grant-access.sh" >/dev/null
URL=$(gcloud run services describe gstudio-web --region=$R --project=$P --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)
for i in 1 2 3 4 5 6; do curl -fsS "$URL/api/health" >/dev/null 2>&1 && break; sleep 5; done
echo "web health:     $(curl -fsS "$URL/api/health" || echo FAILED)"
echo "builder health: $(curl -fsS -m 300 -H "Authorization: Bearer $TOKEN" "$BUILDER_URL/health" | head -c 300 || echo FAILED)"
echo
echo "Wish Labs studio is live: $URL (redirects to $URL/studio/)"
echo "On the main site (after deploy/wish-studio-routes.sh): https://wish.plugbook.si/studio/"
echo "Log in with the access key (it was copied to your clipboard the first time this ran;"
echo "to copy it again: gcloud secrets versions access latest --secret=gstudio-access-key --project=$P | pbcopy)"
