#!/usr/bin/env bash
# Creates the cluster's generated secrets once, in Secrets Manager under niadra/app (a dedicated cell:
# niadra-<name>/app), where External Secrets reads them. A later run keeps every existing value and
# only adds the keys a newer release introduced (ADDED below): rotating means editing the secret there.
# Runs on the k3s machine (its role may create and update secrets under its cell's name). Nothing is
# printed or written to disk except a temporary key file, removed on exit.
#   NIADRA_SECRET_SEED=<json file>   values that replace generated ones when the secret is created:
#                                    scripts/cell.sh passes a dedicated cell's key_pepper (the one the
#                                    control plane hashes keys with) and its own cell_token.
set -euo pipefail
NAME="${1:-niadra/app}"
REGION="${NIADRA_REGION:?}"
SEED="${NIADRA_SECRET_SEED:-}"

# Hex keeps every password safe inside connection URLs.
hex() { openssl rand -hex "${1:-32}"; }

# Keys added after the first release, generated only when missing: the control plane's key for
# sealing identity-provider client secrets (NIADRA_CONTROL_SECRETS_KEY, 32 bytes as hex).
ADDED=(control_secrets_key)

if aws secretsmanager describe-secret --region "$REGION" --secret-id "$NAME" >/dev/null 2>&1; then
  current="$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$NAME" \
    --query SecretString --output text)"
  missing=()
  for key in "${ADDED[@]}"; do
    jq -e --arg k "$key" 'has($k)' <<<"$current" >/dev/null || missing+=("$key")
  done
  if [ ${#missing[@]} -eq 0 ]; then
    echo "    $NAME already exists"
    exit 0
  fi
  # Existing values are never replaced: the merge only adds what is missing.
  merged="$current"
  for key in "${missing[@]}"; do
    merged="$(jq --arg k "$key" --arg v "$(hex 32)" '. + {($k): $v}' <<<"$merged")"
  done
  aws secretsmanager put-secret-value --region "$REGION" --secret-id "$NAME" \
    --secret-string file:///dev/stdin <<<"$merged" >/dev/null
  echo "    $NAME: added ${missing[*]}"
  exit 0
fi
KEY="$(mktemp)"
trap 'rm -f "$KEY"' EXIT
# The control plane's Ed25519 key: signs person tokens and the configuration snapshot.
# macOS's /usr/bin/openssl (LibreSSL) has no Ed25519: say so instead of stopping without a word.
openssl genpkey -algorithm ed25519 -out "$KEY" 2>/dev/null || {
  echo "$(openssl version) cannot make an Ed25519 key: put OpenSSL 3 first in PATH (Homebrew: /opt/homebrew/bin)" >&2
  exit 1
}

jq -n \
  --arg db_owner_password "$(hex)" \
  --arg db_app_password "$(hex)" \
  --arg db_system_password "$(hex)" \
  --arg db_site_password "$(hex)" \
  --arg rabbitmq_password "$(hex)" \
  --arg key_pepper "$(hex)" \
  --arg subject_token_secret "$(hex)" \
  --arg cell_token "$(hex)" \
  --arg operator_token "$(hex)" \
  --arg grafana_admin_password "$(hex 12)" \
  --arg control_secrets_key "$(hex 32)" \
  --rawfile control_signing_key "$KEY" \
  '$ARGS.named' |
  if [ -n "$SEED" ]; then jq --slurpfile seed "$SEED" '. + $seed[0]'; else cat; fi |
  aws secretsmanager create-secret --region "$REGION" --name "$NAME" \
    --description "Niadra cluster: generated passwords and keys (scripts/bootstrap-secrets.sh)" \
    --secret-string file:///dev/stdin >/dev/null
echo "    $NAME created"
