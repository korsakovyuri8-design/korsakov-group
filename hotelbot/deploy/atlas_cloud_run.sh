#!/usr/bin/env bash
# Deploy the public Korsakov Atlas demo to Google Cloud Run (docs/ATLAS_RELEASE.md).
#
#   PROJECT=<gcp-project-id> deploy/atlas_cloud_run.sh
#   PROJECT=... MIN_INSTANCES=1 deploy/atlas_cloud_run.sh     # demo day: no cold start
#
# One instance on purpose: demo sessions live in memory (see app/atlas_web/sessions.py).
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT to the Google Cloud project id}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-atlas-demo}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
REPO="${REPO:-atlas}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}:$(date -u +%Y%m%d-%H%M%S)"

cd "$(dirname "$0")/.."

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  --project "$PROJECT"
gcloud artifacts repositories describe "$REPO" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" --project "$PROJECT"

gcloud builds submit --project "$PROJECT" --config deploy/cloudbuild.atlas.yaml \
  --substitutions "_IMAGE=${IMAGE}" .

gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --image "$IMAGE" \
  --allow-unauthenticated --port 8080 \
  --min-instances "$MIN_INSTANCES" --max-instances 1 --session-affinity \
  --concurrency 40 --cpu 1 --memory 512Mi --timeout 30

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
echo "Deployed: ${URL}"
curl -fsS "${URL}/health" && echo
