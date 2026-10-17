#!/usr/bin/env bash
# Creates or updates the cluster stack (aws/cluster.yaml) and prints what the next steps need.
#   scripts/stack.sh
#   NIADRA_ALERT_EMAIL=<address> scripts/stack.sh    that address gets the alerts, once it confirms
#   NIADRA_DOMAIN=<domain> scripts/stack.sh          the domain whose zone the stack creates
#   NIADRA_DOCS_TARGET, NIADRA_DOCS_HOSTNAME_PROOF, NIADRA_DOCS_CERTIFICATE_PROOF   the docs host's records
# Without a variable the stack keeps the value it has; setting it empty removes it.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"

overrides=()
[ -n "${NIADRA_ALERT_EMAIL+set}" ] && overrides+=("AlertEmail=$NIADRA_ALERT_EMAIL")
[ -n "${NIADRA_DOMAIN:-}" ] && overrides+=("DomainName=$NIADRA_DOMAIN")
[ -n "${NIADRA_DOCS_TARGET+set}" ] && overrides+=("DocsTarget=$NIADRA_DOCS_TARGET")
[ -n "${NIADRA_DOCS_HOSTNAME_PROOF+set}" ] && overrides+=("DocsHostnameProof=$NIADRA_DOCS_HOSTNAME_PROOF")
[ -n "${NIADRA_DOCS_CERTIFICATE_PROOF+set}" ] && overrides+=("DocsCertificateProof=$NIADRA_DOCS_CERTIFICATE_PROOF")
aws cloudformation deploy --stack-name "$STACK" --template-file "$INFRA/aws/cluster.yaml" \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  ${overrides[@]+--parameter-overrides "${overrides[@]}"} \
  --tags project=niadra
aws cloudformation describe-stacks --stack-name "$STACK" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
# A parameter group change or the move to a new group waits for a reboot (README, "What is not
# continuous"); say so instead of leaving it to be found.
aws rds describe-db-instances --db-instance-identifier "$STACK" \
  --query 'DBInstances[0].DBParameterGroups[].[DBParameterGroupName,ParameterApplyStatus]' --output text |
  while read -r group status; do
    echo "database parameter group $group: $status"
    [ "$status" = pending-reboot ] && echo "  reboot the database in a quiet moment to apply it (README)"
  done
