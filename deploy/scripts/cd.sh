#!/usr/bin/env bash
# Continuous deploy, on the k3s machine: a systemd timer runs it a minute after each run ends
# (on-ops.sh installs both). A person moves the repository's `release` branch to a commit of main whose
# checks passed; CI never does. When it moved since the last deploy, this fetches that commit and runs
# on-ops.sh, the same deploy as scripts/deploy.sh, end-to-end check included. A commit that failed is
# not tried again until the branch moves.
#
# The machine pulls; nothing outside it can start a deploy. NIADRA_CD_REPOSITORY (deploy.env) names the
# repository; an SSH address is read with a read-only deploy key (Secrets Manager,
# <cell>/github/deploy-key; scripts/github-keys.sh; a dedicated cell reads its copy under its own name,
# which scripts/cell.sh makes).
# The output goes to the journal: `journalctl -u niadra-cd`, or scripts/cd-status.sh from a laptop.
set -euo pipefail
export HOME=/root PATH=$PATH:/snap/bin:/usr/local/bin KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# One deploy at a time: a run that finds another one going stops here (on-ops.sh also waits for
# a deploy started by scripts/deploy.sh).
exec 8>/run/niadra-cd.lock
flock -n 8 || exit 0

STATE=/var/lib/niadra
KEYS=/etc/niadra/github
# GitHub's published host key (docs.github.com, "GitHub's SSH key fingerprints").
GITHUB_HOST_KEY="github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"

# The stack's outputs and the public hosts, written by the last on-ops.sh run.
# shellcheck source=/dev/null
source /etc/niadra/deploy.env
REPOSITORY="${NIADRA_CD_REPOSITORY:-https://github.com/ainiadra/niadra.git}"

mkdir -p "$STATE" "$KEYS"
chmod 700 "$KEYS"
if [[ $REPOSITORY == git@* || $REPOSITORY == ssh://* ]]; then
  echo "$GITHUB_HOST_KEY" > "$KEYS/known_hosts"
  if [ ! -s "$KEYS/deploy" ]; then
    (
      umask 077
      aws secretsmanager get-secret-value --region "$NIADRA_REGION" \
        --secret-id "${NIADRA_SECRETS_PREFIX:-niadra}/github/deploy-key" \
        --query SecretString --output text > "$KEYS/deploy"
    )
  fi
  export GIT_SSH_COMMAND="ssh -i $KEYS/deploy -o IdentitiesOnly=yes -o UserKnownHostsFile=$KEYS/known_hosts -o StrictHostKeyChecking=yes -o BatchMode=yes"
fi

COMMIT="$(git ls-remote "$REPOSITORY" refs/heads/release | cut -f1)"
[ -n "$COMMIT" ] || exit 0
[ "$COMMIT" = "$(cat "$STATE/deployed" 2>/dev/null || true)" ] && exit 0
[ "$COMMIT" = "$(cat "$STATE/failed" 2>/dev/null || true)" ] && exit 0

VERSION="$(date -u +%Y.%m.%d-%H%M)-${COMMIT:0:7}"
RELEASE="/opt/niadra/releases/$VERSION"
echo "==> release $VERSION: ${COMMIT:0:7}"
mkdir -p "$RELEASE"
git -C "$RELEASE" init -q
git -C "$RELEASE" fetch -q --depth 1 "$REPOSITORY" "$COMMIT"
git -C "$RELEASE" -c advice.detachedHead=false checkout -q FETCH_HEAD
rm -rf "$RELEASE/.git"

export NIADRA_VERSION="$VERSION"
if bash "$RELEASE/deploy/scripts/on-ops.sh" "$RELEASE"; then
  echo "$COMMIT" > "$STATE/deployed"
  rm -f "$STATE/failed"
else
  echo "$COMMIT" > "$STATE/failed"
  echo "==> release $VERSION failed; the step that failed is above" >&2
  exit 1
fi
