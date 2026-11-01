#!/bin/bash
# gstudio-builder: build the heavy image with Cloud Build and (separately) deploy it to Cloud Run.
#   deploy/builder.sh build            create the Artifact Registry repo if missing, stage engine/ library/ builder/,
#                                      gcloud builds submit (E2_HIGHCPU_32, 300 GB disk, 2 h timeout); prints the image
#   deploy/builder.sh deploy [image]   gcloud run deploy gstudio-builder (private) from an image (default :latest)
#   deploy/builder.sh all              build, then deploy the image just built
# IAM (invoker for the web service, bucket access) is NOT granted here: see deploy/grant-access.sh.
set -euo pipefail
P=your-gcp-project
R=asia-south1
REPO=gstudio
SERVICE=gstudio-builder
IMAGE=$R-docker.pkg.dev/$P/$REPO/$SERVICE
SA=gstudio-sa@$P.iam.gserviceaccount.com
BUCKET=gstudio-projects-000000000000
STUDIO="$(cd "$(dirname "$0")/.." && pwd)"
cmd="${1:-build}"

build() {
  if ! gcloud artifacts repositories describe "$REPO" --location="$R" --project="$P" >/dev/null 2>&1; then
    gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$R" --project="$P" \
      --description="Gadget Studio images"
  fi
  local tag
  tag="${TAG:-$(date -u +%Y%m%d-%H%M%S)}"
  CTX="$(mktemp -d)"
  trap 'rm -rf "$CTX"' EXIT
  rsync -a --exclude '__pycache__' --exclude '_out' --exclude '.pytest_cache' --exclude '*.pyc' --exclude '.pio' \
    "$STUDIO/engine" "$STUDIO/library" "$STUDIO/builder" "$CTX/"
  mkdir -p "$CTX/examples"  # example specs only (firmware warm-up, worked example); generated outputs stay out
  (cd "$STUDIO/examples" && find . -maxdepth 2 -name spec.json -exec rsync -aR {} "$CTX/examples/" \;)
  echo "context: $(du -sh "$CTX" | cut -f1) -> $IMAGE:$tag"
  # regional build: the multi-GB image is pushed to the same region's Artifact Registry (BUILD_REGION=global to opt out)
  gcloud builds submit "$CTX" --project="$P" --region="${BUILD_REGION:-$R}" --config="$CTX/builder/cloudbuild.yaml" \
    --substitutions="_IMAGE=$IMAGE,_TAG=$tag"
  echo "IMAGE=$IMAGE:$tag"
  BUILT="$IMAGE:$tag"
}

deploy() {
  local img="${1:-$IMAGE:latest}"
  gcloud run deploy "$SERVICE" --image="$img" --project="$P" --region="$R" \
    --no-allow-unauthenticated --execution-environment=gen2 \
    --cpu=8 --memory=32Gi --timeout=3600 --concurrency=2 --min-instances=0 --max-instances=3 --cpu-boost \
    --service-account="$SA" --set-env-vars="BUCKET=$BUCKET,MAX_BUILDS=2" --port=8080
  gcloud run services describe "$SERVICE" --project="$P" --region="$R" --format='value(status.url)'
}

case "$cmd" in
  build) build ;;
  deploy) deploy "${2:-}" ;;
  all) build && deploy "$BUILT" ;;
  *) echo "usage: deploy/builder.sh build | deploy [image] | all"; exit 2 ;;
esac
