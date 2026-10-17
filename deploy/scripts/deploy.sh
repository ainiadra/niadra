#!/usr/bin/env bash
# Ships a release of the whole platform to the cluster, from the folders of this repository:
#   ../server   server and models images
#   ../web      site and site-api images
#   ../deploy   chart, helmfile and the script that runs on the machine
#
#   scripts/deploy.sh                       version = date and commit
#   scripts/deploy.sh 2026.09.23-1          explicit version
#   NIADRA_DOMAIN=<domain> scripts/deploy.sh   once the domain's nameservers point at Route 53
#   NIADRA_ALERTS_WEBHOOK_URL=<url> scripts/deploy.sh   the alerts also go to that webhook
#   NIADRA_CD_REPOSITORY=<git address> scripts/deploy.sh   the repository the continuous deploy reads
#   NIADRA_STACK=niadra-<name> NIADRA_DOMAIN=<domain> scripts/deploy.sh   a dedicated cell
#                                           (scripts/cell.sh runs this): the cell only, pulling from
#                                           the default cell's control plane
#
# The sources travel as one archive through the release bucket; the machine builds the images,
# pushes them to ECR and runs helmfile. Then `helm test` runs the end-to-end check of the sandbox.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"

BACK="$ROOT/server"
FRONT="$ROOT/web"
VERSION="${1:-$(date -u +%Y.%m.%d-%H%M)-$(git -C "$ROOT" rev-parse --short HEAD)}"
BUCKET="$(output ReleaseBucket)"
BASE="$(hosts_base)"
GRAFANA_HOST="grafana.$BASE"
CONTROL_URL=""
# On the domain, the addresses of the contract: <space>.<region>.api.<domain> for the SDKs and
# control.api.<domain>, with certificates proven in the Route 53 zone. Under sslip.io, one name each.
if [ "$STACK" != niadra ]; then
  # A dedicated cell: its own address (<cell>.cells.<domain>, the one the control plane knows) and the
  # SDKs' wildcard, which reaches it for the spaces `scripts/cell.sh route` pointed here. The control
  # plane, the site and the Console stay on the default cell; the site hosts are the Console's origins.
  if [ -z "${NIADRA_DOMAIN:-}" ]; then
    echo "a dedicated cell needs NIADRA_DOMAIN: its names live in the domain's zone" >&2
    exit 1
  fi
  CELL_HOST="$(output CellHost)"
  API_HOSTS="$CELL_HOST,*.$AWS_REGION.api.$BASE"
  CONTROL_HOST="control.api.$BASE"
  CONTROL_URL="https://$CONTROL_HOST"
  GRAFANA_HOST="grafana.$CELL_HOST"
  ROUTE53_ZONE="$(output ZoneId)"
elif [ -n "${NIADRA_DOMAIN:-}" ]; then
  API_HOSTS="api.$BASE,*.$AWS_REGION.api.$BASE"
  CONTROL_HOST="control.api.$BASE"
  ROUTE53_ZONE="$(output ZoneId)"
else
  API_HOSTS="api.$BASE"
  CONTROL_HOST="control.$BASE"
  ROUTE53_ZONE=""
fi

echo "==> release $VERSION for $BASE (cell $STACK)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
EXCLUDES=(--exclude .venv --exclude __pycache__ --exclude .mypy_cache --exclude .ruff_cache
  --exclude .pytest_cache --exclude .git --exclude node_modules --exclude dist --exclude dist-ssr
  --exclude dist-server --exclude '.env*' --exclude '*.tsbuildinfo')
mkdir -p "$STAGE/server" "$STAGE/web" "$STAGE/deploy"
rsync -a "${EXCLUDES[@]}" "$BACK/" "$STAGE/server/"
rsync -a "${EXCLUDES[@]}" "$FRONT/" "$STAGE/web/"
rsync -a "${EXCLUDES[@]}" --exclude tests "$INFRA/" "$STAGE/deploy/"
# No macOS extended attributes: GNU tar on the node would warn about each one.
COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs -czf "$STAGE/release.tar.gz" -C "$STAGE" server web deploy
aws s3 cp --only-show-errors "$STAGE/release.tar.gz" "s3://$BUCKET/releases/$VERSION.tar.gz"
echo "    $(du -h "$STAGE/release.tar.gz" | cut -f1) in s3://$BUCKET/releases/$VERSION.tar.gz"

cat > "$STAGE/remote.sh" <<REMOTE
set -euo pipefail
export HOME=/root PATH=\$PATH:/snap/bin:/usr/local/bin KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# A continuous deploy already running read /etc/niadra/deploy.env before this one; left to queue behind
# it, it would then deploy with the old values and write them back. Wait until none is running.
exec 8>/run/niadra-cd.lock
flock 8
RELEASE=/opt/niadra/releases/$VERSION
mkdir -p "\$RELEASE"
aws s3 cp --only-show-errors "s3://$BUCKET/releases/$VERSION.tar.gz" - | tar -xz -C "\$RELEASE"
export NIADRA_VERSION='$VERSION'
export NIADRA_REGION='$AWS_REGION'
export NIADRA_REGISTRY='$(output Registry)'
export NIADRA_DB_HOST='$(output DatabaseEndpoint)'
export NIADRA_DB_MASTER_SECRET='$(output DatabaseMasterSecretArn)'
export NIADRA_CELL_BUCKET='$(output CellBucket)'
export NIADRA_KMS_KEY_ARN='$(output CellKeyArn)'
export NIADRA_AUDIT_BUCKET='$(output AuditBucket)'
export NIADRA_ALERTS_TOPIC_ARN='$(output AlertsTopicArn)'
export NIADRA_ALERTS_WEBHOOK_URL='${NIADRA_ALERTS_WEBHOOK_URL:-}'
export NIADRA_SITE_HOSTS='$BASE,www.$BASE'
export NIADRA_API_HOSTS='$API_HOSTS'
export NIADRA_CONTROL_HOST='$CONTROL_HOST'
export NIADRA_GRAFANA_HOST='$GRAFANA_HOST'
export NIADRA_ROUTE53_ZONE='$ROUTE53_ZONE'
export NIADRA_DOMAIN='${NIADRA_DOMAIN:-}'
# The cell's own secrets path and log group; a cell id and a control address only on a dedicated cell.
export NIADRA_CELL_ID='${CONTROL_URL:+$STACK}'
export NIADRA_SECRETS_PREFIX='$STACK'
export NIADRA_LOG_GROUP='$(output LogGroup)'
export NIADRA_CONTROL_URL='$CONTROL_URL'
export NIADRA_CD_REPOSITORY='${NIADRA_CD_REPOSITORY:-https://github.com/ainiadra/niadra.git}'
bash "\$RELEASE/deploy/scripts/on-ops.sh" "\$RELEASE"
REMOTE
on_ops "$STAGE/remote.sh" 5400

echo "==> live"
if [ -z "$CONTROL_URL" ]; then
  echo "    site     https://$BASE"
  echo "    console  https://$BASE/console"
fi
echo "    api      https://${API_HOSTS//,/ and https://}"
echo "    control  https://$CONTROL_HOST"
echo "    grafana  https://$GRAFANA_HOST (user admin; password in Secrets Manager, $STACK/app)"
if [ "$BASE" = niadra.com ] && [ -z "$CONTROL_URL" ]; then
  bash "$FRONT/deploy/indexnow.sh"
fi
