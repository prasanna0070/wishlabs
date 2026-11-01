#!/bin/bash
# Put the Wishwright Labs site on a public Cloud Run URL (static nginx, scales to zero).
# Claude's auto mode refuses production deploys, so Prasanna runs:  ! bash studio/deploy/landing.sh
# Deploys studio/site (the animated site) when present, else studio/landing (the simple page).
set -euo pipefail
P=your-gcp-project
R=asia-south1
SVC=${SVC:-wishwright-labs}
STUDIO="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$STUDIO/site"; [ -f "$DIR/index.html" ] && [ -f "$DIR/Dockerfile" ] || DIR="$STUDIO/landing"
echo "deploying $DIR as $SVC"
gcloud run deploy "$SVC" --source="$DIR" --project=$P --region=$R \
  --allow-unauthenticated --cpu=1 --memory=256Mi --min-instances=0 --max-instances=5 \
  --concurrency=200 --port=8080 --quiet
URL=$(gcloud run services describe "$SVC" --region=$R --project=$P --format='value(status.url)')
for i in 1 2 3 4 5 6; do curl -fsS -o /dev/null "$URL/" && break; sleep 5; done
echo "page: $(curl -s -o /dev/null -w '%{http_code}' "$URL/")"
echo "Site is live: $URL"
printf %s "$URL" | pbcopy; echo "(URL copied to clipboard)"
