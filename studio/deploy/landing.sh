#!/bin/bash
# Put the Gadget Studio landing page on a public Cloud Run URL (static nginx, scales to zero).
# Claude's auto mode refuses production deploys, so Prasanna runs:  ! bash studio/deploy/landing.sh
set -euo pipefail
P=your-gcp-project
R=asia-south1
DIR="$(cd "$(dirname "$0")/../landing" && pwd)"
gcloud run deploy gstudio-landing --source="$DIR" --project=$P --region=$R \
  --allow-unauthenticated --cpu=1 --memory=256Mi --min-instances=0 --max-instances=5 \
  --concurrency=200 --port=8080 --quiet
URL=$(gcloud run services describe gstudio-landing --region=$R --project=$P --format='value(status.url)')
for i in 1 2 3 4 5 6; do curl -fsS -o /dev/null "$URL/" && break; sleep 5; done
echo "landing status: $(curl -s -o /dev/null -w '%{http_code}' "$URL/")  hero: $(curl -s -o /dev/null -w '%{http_code}' "$URL/assets/img/hero-lineup.jpg")"
echo "Landing page is live: $URL"
printf %s "$URL" | pbcopy; echo "(URL copied to clipboard)"
