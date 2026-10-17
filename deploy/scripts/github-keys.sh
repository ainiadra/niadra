#!/usr/bin/env bash
# Creates (or rotates) the read-only deploy key the continuous deploy (cd.sh, on the machine) reads
# GitHub with, for a repository it reads over SSH (NIADRA_CD_REPOSITORY=git@github.com:<owner>/<repo>.git):
# the public half on the repository, the private half only in Secrets Manager (<cell>/github/deploy-key).
# The old keys with the same title are removed. A public repository read over HTTPS needs no key.
# Needs the GitHub CLI logged in as an admin of the repository.
#   NIADRA_GITHUB_REPOSITORY=<owner>/<repo> scripts/github-keys.sh
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"

REPOSITORY="${NIADRA_GITHUB_REPOSITORY:-ainiadra/niadra}"
TITLE="niadra-cd (read-only)"
SECRET="$STACK/github/deploy-key"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ssh-keygen -q -t ed25519 -N '' -C "$TITLE" -f "$WORK/deploy"
for id in $(gh api "repos/$REPOSITORY/keys" --jq ".[] | select(.title == \"$TITLE\") | .id"); do
  gh api -X DELETE "repos/$REPOSITORY/keys/$id" >/dev/null
done
gh api "repos/$REPOSITORY/keys" -f title="$TITLE" -f key="$(cat "$WORK/deploy.pub")" -F read_only=true >/dev/null
echo "    $REPOSITORY"

if aws secretsmanager describe-secret --secret-id "$SECRET" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id "$SECRET" --secret-string "file://$WORK/deploy" >/dev/null
else
  aws secretsmanager create-secret --name "$SECRET" --tags Key=project,Value=niadra \
    --description "Read-only GitHub deploy key of the continuous deploy (scripts/github-keys.sh)" \
    --secret-string "file://$WORK/deploy" >/dev/null
fi
echo "==> key in Secrets Manager ($SECRET); the machine picks it up on its next deploy"
