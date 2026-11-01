#!/bin/bash
# Wish Labs: put the whole platform live on the main site, https://wish.plugbook.si, in one command.
#   1/5  web.sh --build          build the new gstudio-web image with Cloud Build (the SPA now lives under /studio);
#                                skip with SKIP_BUILD=1 when :latest already has this code
#   2/5  go-live.sh              deploy both Cloud Run services (gstudio-builder, gstudio-web from that image)
#   3/5  wish-studio-routes.sh   load balancer: /studio, /studio/* and /api/* on wish.plugbook.si -> gstudio-web
#                                (prints the URL-map diff before importing it)
#   4/5  landing.sh              redeploy the landing page: its "Make a wish" opens /studio/?wish=<the wish>
#   5/5  curl checks             /studio/ is the SPA, /api/health is JSON, / is the landing, old run.app links redirect
# Claude's auto mode refuses production deploys and load-balancer changes, so Prasanna runs:
#   ! bash studio/deploy/make-wish-live.sh
# Every step is idempotent: re-running after a failure is safe. To preview the URL-map change only:
#   ! bash studio/deploy/wish-studio-routes.sh --dry-run
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
if [ "${SKIP_BUILD:-0}" = 1 ]; then
  echo "== 1/5 build skipped (SKIP_BUILD=1): go-live.sh deploys whatever gstudio-web:latest is"
else
  echo "== 1/5 build the gstudio-web image (Cloud Build, about 5-10 min)"
  bash "$D/web.sh" --build
fi
echo "== 2/5 deploy the Cloud Run services (go-live.sh)"
bash "$D/go-live.sh"
echo "== 3/5 route /studio and /api on wish.plugbook.si to gstudio-web (wish-studio-routes.sh)"
bash "$D/wish-studio-routes.sh"
echo "== 4/5 redeploy the landing page (landing.sh)"
bash "$D/landing.sh"
echo "== 5/5 checks"
bash "$D/wish-studio-routes.sh" --check
echo
echo "Wish Labs is live: https://wish.plugbook.si/ (landing) and https://wish.plugbook.si/studio/ (the studio)."
echo "Sign-in uses the same access key as before (to copy it: gcloud secrets versions access latest"
echo "  --secret=gstudio-access-key --project=your-gcp-project | pbcopy)."
