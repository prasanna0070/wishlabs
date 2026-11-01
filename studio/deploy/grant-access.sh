#!/bin/bash
# Gadget Studio: one-time access setup that Claude's auto mode may not run (IAM grants + secret write).
# Run it yourself:  ! bash studio/deploy/grant-access.sh
# 1) creates the site access key in Secret Manager (random; copied to your clipboard, never printed)
# 2) lets the gstudio-sa service account call Vertex AI, read/write the project bucket, read the key, write logs
# 3) if the builder service exists, lets gstudio-sa invoke it (the web service calls the builder)
set -euo pipefail
P=your-gcp-project
SA=gstudio-sa@$P.iam.gserviceaccount.com
BUCKET=gs://gstudio-projects-000000000000
if ! gcloud secrets describe gstudio-access-key --project=$P >/dev/null 2>&1; then
  KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
  printf %s "$KEY" | gcloud secrets create gstudio-access-key --data-file=- --replication-policy=automatic --project=$P
  printf %s "$KEY" | pbcopy; echo "access key created and copied to clipboard (length ${#KEY})"; unset KEY
else
  echo "secret gstudio-access-key already exists"
fi
gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=roles/aiplatform.user --condition=None --format=none
gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=roles/logging.logWriter --condition=None --format=none
gcloud storage buckets add-iam-policy-binding $BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin --format=none
gcloud secrets add-iam-policy-binding gstudio-access-key --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor --project=$P --format=none
if gcloud run services describe gstudio-builder --region=asia-south1 --project=$P >/dev/null 2>&1; then
  gcloud run services add-iam-policy-binding gstudio-builder --region=asia-south1 --project=$P --member=serviceAccount:$SA --role=roles/run.invoker --format=none
  echo "builder invoker granted"
else
  echo "builder not deployed yet: re-run this script after it is"
fi
echo "done"
