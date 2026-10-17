#!/usr/bin/env bash
# A cell dedicated to one customer: its own network, machine, database (on a
# disk encrypted with its own key), master key, buckets, secrets and alerts, from aws/cluster.yaml,
# beside the default cell in the same account and Region. The control plane, the site and the
# Console stay on the default cell; the dedicated cell pulls its configuration from there with a
# credential of its own.
#
#   scripts/cell.sh up <name> --tenant <slug> [--instance-type <type>] [--db-class <class>] [--confirm]
#   scripts/cell.sh status <name>
#   scripts/cell.sh route <name> [--confirm]
#   scripts/cell.sh down <name> [--confirm]
#
# <name> is short (acme): the cell, its stack and its secrets path are niadra-<name>. Without
# --confirm, up, route and down only say what they would do and change nothing; up prints the monthly
# estimate first. `up` is the whole bring-up: the stack, the cell in the control plane, its secrets,
# then the deploy. Then place the customer's projects on it and point their addresses at it:
#   control_cli place-project --tenant <slug> --name <name> --slug <slug> --cell niadra-<name>
#   scripts/cell.sh route <name> --confirm
# NIADRA_DOMAIN is the domain; NIADRA_ALERT_EMAIL, when set, gets the cell's alerts
# (otherwise the default cell's address does).
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"

usage() {
  sed -n '8,11p' "$0" | sed 's/^# //' >&2
  exit 2
}

[ $# -ge 2 ] || usage
COMMAND="$1"
NAME="$2"
shift 2
if ! [[ $NAME =~ ^[a-z][a-z0-9]{1,19}$ ]]; then
  echo "the name is 2 to 20 lowercase letters and digits, starting with a letter" >&2
  exit 2
fi
CELL="niadra-$NAME"
DEFAULT=niadra
DOMAIN="${NIADRA_DOMAIN:?set NIADRA_DOMAIN, the domain of the default cell}"
TENANT="" INSTANCE_TYPE="" DB_CLASS="" CONFIRM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tenant) TENANT="${2:?}" && shift 2 ;;
    --instance-type) INSTANCE_TYPE="${2:?}" && shift 2 ;;
    --db-class) DB_CLASS="${2:?}" && shift 2 ;;
    --confirm) CONFIRM=yes && shift ;;
    *) usage ;;
  esac
done
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# scripts/stack_render.py needs PyYAML: the system Python when it has it, uv's otherwise.
py() {
  if python3 -c 'import yaml' 2>/dev/null; then
    python3 "$@"
  else
    uv run --quiet --no-project --with pyyaml python3 "$@"
  fi
}

# A control plane command, in the control pod of the default cell (through Systems Manager).
control() {
  local args=""
  for arg in "$@"; do args+=" $(printf '%q' "$arg")"; done
  cat >"$WORK/control.sh" <<SCRIPT
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=\$PATH:/usr/local/bin
kubectl -n niadra exec deploy/control -- python -m niadra.entrypoints.control_cli$args
SCRIPT
  local saved="$STACK" rc=0
  STACK="$DEFAULT"
  ops_query "$WORK/control.sh" >"$WORK/control.out" || rc=$?
  STACK="$saved"
  tr '\t' '\n' <"$WORK/control.out"
  return "$rc"
}

stack_exists() {
  aws cloudformation describe-stacks --stack-name "$1" >/dev/null 2>&1
}

parameters() {
  echo "CellName=$CELL"
  [ -z "$INSTANCE_TYPE" ] || echo "InstanceType=$INSTANCE_TYPE"
  [ -z "$DB_CLASS" ] || echo "DatabaseClass=$DB_CLASS"
}

# HOSTS: the space addresses the control plane says must reach the cell (none for a cell it does not
# know). Any other failure stops the script: `down` must never take a cell it could not ask about.
load_space_hosts() {
  HOSTS=()
  if ! control routes --cell "$CELL" --domain "$DOMAIN" >"$WORK/routes"; then
    grep -q "unknown cell" "$WORK/control.out" || exit 1
  fi
  # Names the edge serves itself never move: `mtls.` is every tenant's webhook mTLS address (the control
  # plane refuses these as space slugs; this is the second lock).
  grep -E "^[a-z0-9-]+\.$AWS_REGION\.api\.${DOMAIN//./\\.}$" "$WORK/routes" |
    grep -Ev '^(mtls|api|www|control|cells|grafana)\.' >"$WORK/hosts" || true
  while IFS= read -r host; do HOSTS+=("$host"); done <"$WORK/hosts"
}

# UPSERT or DELETE of A records to the cell's address, in the default cell's zone.
change_records() {
  local action="$1" zone="$2" address="$3"
  shift 3
  python3 - "$action" "$address" "$@" >"$WORK/changes.json" <<'PY'
import json
import sys
action, address, hosts = sys.argv[1], sys.argv[2], sys.argv[3:]
changes = [
    {"Action": action, "ResourceRecordSet": {"Name": h, "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": address}]}}
    for h in hosts
]
print(json.dumps({"Comment": "space addresses of a dedicated cell (scripts/cell.sh)", "Changes": changes}))
PY
  aws route53 change-resource-record-sets --hosted-zone-id "$zone" --change-batch "file://$WORK/changes.json" \
    --query ChangeInfo.Status --output text
}

# The records in the zone that point at the cell's address, other than its own names.
records_at() {
  local zone="$1" address="$2"
  aws route53 list-resource-record-sets --hosted-zone-id "$zone" --output json |
    python3 -c '
import json, sys
address, own = sys.argv[1], sys.argv[2]
for r in json.load(sys.stdin)["ResourceRecordSets"]:
    values = [v["Value"] for v in r.get("ResourceRecords", [])]
    if r["Type"] == "A" and values == [address] and not r["Name"].rstrip(".").endswith(own):
        print(r["Name"].rstrip("."))' "$address" "$CELL.cells.$DOMAIN"
}

up() {
  if [ -z "$TENANT" ]; then
    echo "up needs --tenant <slug>: the customer the cell is dedicated to" >&2
    exit 2
  fi
  echo "==> $CELL, dedicated to tenant $TENANT, in $AWS_REGION"
  echo "    monthly estimate (on-demand list prices, running all the time):"
  # shellcheck disable=SC2046
  py "$INFRA/scripts/stack_render.py" cost $(parameters) ZoneId=Z0000ESTIMATE
  echo "    plus what it uses: data transfer out, CloudWatch Logs, S3 storage, Secrets Manager API calls"
  if [ -z "$CONFIRM" ]; then
    cat >&2 <<EOF

Nothing was created. With --confirm this creates the stack $CELL (it costs money from that moment),
registers the cell in the control plane as dedicated to $TENANT, writes its secrets under $CELL/ and
deploys it:
  scripts/cell.sh up $NAME --tenant $TENANT${INSTANCE_TYPE:+ --instance-type $INSTANCE_TYPE}${DB_CLASS:+ --db-class $DB_CLASS} --confirm
EOF
    exit 2
  fi

  local zone email
  zone="$(stack_output "$DEFAULT" ZoneId)"
  if [ -z "$zone" ]; then
    echo "the default cell's stack has no ZoneId output; run scripts/stack.sh first" >&2
    exit 1
  fi
  email="${NIADRA_ALERT_EMAIL-$(aws cloudformation describe-stacks --stack-name "$DEFAULT" \
    --query "Stacks[0].Parameters[?ParameterKey=='AlertEmail'].ParameterValue" --output text)}"
  [ "$email" != None ] || email=""

  echo "==> stack $CELL"
  # shellcheck disable=SC2046
  aws cloudformation deploy --stack-name "$CELL" --template-file "$INFRA/aws/cluster.yaml" \
    --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
    --parameter-overrides $(parameters) "ZoneId=$zone" "AlertEmail=$email" \
    --tags project=niadra "cell=$CELL" "tenant=$TENANT"

  echo "==> the cell in the control plane, and its secrets"
  if aws secretsmanager describe-secret --secret-id "$CELL/app" >/dev/null 2>&1; then
    echo "    $CELL/app exists: the cell is registered already"
  else
    # The credential goes from here to the cell's secret; the control plane only ever sees its SHA-256.
    local token digest
    token="$(openssl rand -hex 32)"
    digest="$(printf %s "$token" | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
    if ! control register-cell --cell "$CELL" --address "https://$CELL.cells.$DOMAIN" \
      --tenant "$TENANT" --token-sha256 "$digest" --region "$AWS_REGION"; then
      # Registered by an earlier run that stopped before the secret: a new credential replaces that one.
      grep -q "already exists" "$WORK/control.out" || exit 1
      control rekey-cell --cell "$CELL" --token-sha256 "$digest"
    fi
    # The key pepper is the platform's: the control plane hashes every API key with it.
    (
      umask 077
      aws secretsmanager get-secret-value --secret-id "$DEFAULT/app" --query SecretString --output text |
        jq --arg token "$token" '{key_pepper: .key_pepper, cell_token: $token}' >"$WORK/seed.json"
    )
    NIADRA_REGION="$AWS_REGION" NIADRA_SECRET_SEED="$WORK/seed.json" \
      bash "$INFRA/scripts/bootstrap-secrets.sh" "$CELL/app"
    rm -f "$WORK/seed.json"
  fi
  # The AI endpoint's secret and the deploy key, copied under the cell's name: its role reads nothing
  # under niadra/.
  for secret in platform/llm github/deploy-key; do
    if ! aws secretsmanager describe-secret --secret-id "$CELL/$secret" >/dev/null 2>&1 &&
      aws secretsmanager describe-secret --secret-id "$DEFAULT/$secret" >/dev/null 2>&1; then
      aws secretsmanager get-secret-value --secret-id "$DEFAULT/$secret" --query SecretString --output text |
        aws secretsmanager create-secret --name "$CELL/$secret" --secret-string file:///dev/stdin \
          --description "Copy of $DEFAULT/$secret for the dedicated cell $CELL (scripts/cell.sh)" >/dev/null
      echo "    $CELL/$secret"
    fi
  done

  echo "==> the machine"
  local instance state=""
  instance="$(stack_output "$CELL" InstanceId)"
  for _ in $(seq 1 90); do
    state="$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$instance" \
      --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)"
    [ "$state" = Online ] && break
    sleep 10
  done
  if [ "$state" != Online ]; then
    echo "    $instance is not reachable through Systems Manager after 15 minutes" >&2
    exit 1
  fi
  # The machine installs k3s, Docker and helmfile on its first boot.
  cat >"$WORK/ready.sh" <<'SCRIPT'
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin
for _ in $(seq 1 120); do
  if [ -x /usr/local/bin/helmfile ] && kubectl get --raw /readyz >/dev/null 2>&1; then echo "k3s ready"; exit 0; fi
  sleep 10
done
exit 1
SCRIPT
  STACK="$CELL" on_ops "$WORK/ready.sh" 1500

  echo "==> deploy"
  NIADRA_STACK="$CELL" NIADRA_DOMAIN="$DOMAIN" bash "$INFRA/scripts/deploy.sh"

  cat <<EOF

$CELL is up at https://$CELL.cells.$DOMAIN. New projects of $TENANT land on it, from the Console
too; to place one by hand (on the default cell's machine, through Systems Manager):
  kubectl -n niadra exec deploy/control -- python -m niadra.entrypoints.control_cli place-project \\
    --tenant $TENANT --name <name> --slug <slug> --cell $CELL
Then point their addresses at the cell:
  scripts/cell.sh route $NAME --confirm
EOF
}

status() {
  if ! stack_exists "$CELL"; then
    echo "$CELL: no stack"
    return 0
  fi
  aws cloudformation describe-stacks --stack-name "$CELL" \
    --query 'Stacks[0].[StackStatus,CreationTime]' --output text | sed "s/^/$CELL: /"
  aws cloudformation describe-stacks --stack-name "$CELL" \
    --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
  local instance db
  instance="$(aws cloudformation describe-stacks --stack-name "$CELL" \
    --query "Stacks[0].Parameters[?ParameterKey=='InstanceType'].ParameterValue" --output text)"
  db="$(aws cloudformation describe-stacks --stack-name "$CELL" \
    --query "Stacks[0].Parameters[?ParameterKey=='DatabaseClass'].ParameterValue" --output text)"
  echo "monthly estimate:"
  py "$INFRA/scripts/stack_render.py" cost "CellName=$CELL" ZoneId=Z0000ESTIMATE \
    "InstanceType=$instance" "DatabaseClass=$db"
  local zone address
  zone="$(stack_output "$CELL" ZoneId)"
  address="$(stack_output "$CELL" PublicAddress)"
  echo "space addresses pointing at it:"
  records_at "$zone" "$address" | sed 's/^/    /'
}

route() {
  local zone address
  zone="$(stack_output "$CELL" ZoneId)"
  address="$(stack_output "$CELL" PublicAddress)"
  load_space_hosts
  if [ ${#HOSTS[@]} -eq 0 ]; then
    echo "the control plane places no space on $CELL yet"
    return 0
  fi
  printf "    %s -> $address\n" "${HOSTS[@]}"
  if [ -z "$CONFIRM" ]; then
    echo "Nothing changed. With --confirm these records point at $CELL (a record of its own beats the wildcard)." >&2
    exit 2
  fi
  echo "    $(change_records UPSERT "$zone" "$address" "${HOSTS[@]}")"
}

down() {
  if ! stack_exists "$CELL"; then
    echo "$CELL: no stack"
    return 0
  fi
  local zone address host records
  load_space_hosts
  if [ ${#HOSTS[@]} -gt 0 ]; then
    echo "$CELL still serves ${#HOSTS[@]} space addresses (${HOSTS[*]}); delete or move those spaces first" >&2
    exit 1
  fi
  zone="$(stack_output "$CELL" ZoneId)"
  address="$(stack_output "$CELL" PublicAddress)"
  cat <<EOF
==> $CELL: what goes and what stays
    goes: the machine, its address, the database (a final snapshot stays), the network, the log group,
          the release bucket, the alerts topic, the secrets under $CELL/ (after 7 days)
    stays: the cell bucket and the audit bucket (Object Lock, five years), the master key (US\$ 1 a
          month; it opens the final snapshot, so schedule its deletion only once that snapshot goes)
EOF
  if [ -z "$CONFIRM" ]; then
    echo "Nothing changed. Run again with --confirm to take $CELL down." >&2
    exit 2
  fi
  control close-cell --cell "$CELL" || grep -q "unknown cell" "$WORK/control.out"
  records_at "$zone" "$address" >"$WORK/records"
  records=()
  while IFS= read -r host; do records+=("$host"); done <"$WORK/records"
  if [ ${#records[@]} -gt 0 ]; then
    echo "    records: $(change_records DELETE "$zone" "$address" "${records[@]}")"
  fi
  aws s3 rm "s3://$(stack_output "$CELL" ReleaseBucket)" --recursive --only-show-errors || true
  aws rds modify-db-instance --db-instance-identifier "$CELL" --no-deletion-protection --apply-immediately \
    >/dev/null
  aws rds wait db-instance-available --db-instance-identifier "$CELL"
  aws cloudformation delete-stack --stack-name "$CELL"
  aws cloudformation wait stack-delete-complete --stack-name "$CELL"
  for secret in app platform/llm github/deploy-key; do
    aws secretsmanager delete-secret --secret-id "$CELL/$secret" --recovery-window-in-days 7 >/dev/null 2>&1 || true
  done
  echo "==> $CELL is down"
}

case "$COMMAND" in
  up) up ;;
  status) status ;;
  route) route ;;
  down) down ;;
  *) usage ;;
esac
