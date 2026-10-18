#!/usr/bin/env bash
# Deploys the public site to Google Cloud Run. Run it from the repository root, with your own Google login:
#   gcloud auth login && gcloud auth application-default login
#   PROJECT=my-project ./deploy/cloudrun.sh
#
# Before the first run, create the two secrets (see the README, "Public site"):
#   agent-team-password-hash   the output of: agent-team set-password
#   agent-team-session-secret  a random string of 32 or more characters
#
# Storage: Cloud Run's disk is erased when an instance restarts. The database, the run log, and the saved
# keys live in /data, so they are lost on a restart until the storage question in the README is settled.
set -euo pipefail

: "${PROJECT:?set PROJECT to your Google Cloud project id}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-agent-team}"

gcloud config set project "$PROJECT" >/dev/null
for secret in agent-team-password-hash agent-team-session-secret; do
  gcloud secrets describe "$secret" >/dev/null 2>&1 || {
    echo "error: create the secret $secret first (see the README, Public site)" >&2
    exit 1
  }
done

# One instance: sign-in failures are counted in memory, so a second instance would count separately.
# Public access is allowed at the Cloud Run level because the app itself requires the owner's sign-in.
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --max-instances 1 \
  --set-env-vars "AGENT_TEAM_PUBLIC_HOST=pending.invalid" \
  --set-secrets "AGENT_TEAM_OWNER_PASSWORD_HASH=agent-team-password-hash:latest,AGENT_TEAM_SESSION_SECRET=agent-team-session-secret:latest"

# The site checks the Host header, so it must know its own address. Cloud Run assigns that address on the
# first deploy, which starts with a placeholder, so the real address is set on a second update.
URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
HOST="${URL#https://}"
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "AGENT_TEAM_PUBLIC_HOST=$HOST"

echo "Deployed: $URL"
