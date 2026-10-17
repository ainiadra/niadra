#!/usr/bin/env bash
# Empties the default cell for a fresh install: every space database, the cell and control databases,
# both Valkeys, the broker's queues, the cell bucket (all but `benchmarks/`) and the sandbox tenant's
# secret. The site's database and its contact requests stay. Runs on the k3s machine, from a laptop:
#   source scripts/lib.sh; on_ops scripts/reset-cell.sh
# It stops the continuous deploy first and leaves it stopped: push the release branches, then start it
# with `systemctl start niadra-cd.timer` (deploy runbook). The next deploy's db-init and `serve setup`
# create and migrate the databases from their baselines, the tenant job creates Niadra's tenant again
# with new credentials, and people's accounts are added again with `control_cli add-admin`.
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# shellcheck disable=SC1091
[ -f /etc/niadra/deploy.env ] && . /etc/niadra/deploy.env
export AWS_DEFAULT_REGION="${NIADRA_REGION:-us-east-2}"
NS=niadra

exec 9>/run/niadra-deploy.lock
flock 9
systemctl stop niadra-cd.timer
echo "==> continuous deploy stopped"

# The cell's own deployables: loops would create a space's database again from the snapshot it holds.
CELL=(control loops ingest read admin insights)
declare -A REPLICAS
for d in "${CELL[@]}"; do
  REPLICAS[$d]="$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.spec.replicas}')"
  kubectl -n "$NS" scale deploy "$d" --replicas=0 >/dev/null
done
for d in "${CELL[@]}"; do
  kubectl -n "$NS" wait pod -l "app.kubernetes.io/name=$d" --for=delete --timeout=180s >/dev/null 2>&1 || true
done
echo "==> cell deployables stopped"

HOST="$(kubectl -n "$NS" get configmap niadra-cell -o jsonpath='{.data.NIADRA_DB_DIRECT_HOST}')"
pod="reset-$RANDOM"
kubectl -n "$NS" run "$pod" --quiet --restart=Never --image=postgres:17-alpine \
  --labels=niadra.com/db-client=true --overrides='{"spec":{"activeDeadlineSeconds":600,"containers":[{"name":"psql",
    "image":"postgres:17-alpine","command":["sleep","600"],
    "envFrom":[{"secretRef":{"name":"niadra-db-init"}}],
    "env":[{"name":"PGHOST","value":"'"$HOST"'"},{"name":"PGDATABASE","value":"postgres"},{"name":"PGSSLMODE","value":"require"}],
    "securityContext":{"runAsUser":70,"runAsNonRoot":true,"allowPrivilegeEscalation":false}}]}}' >/dev/null
kubectl -n "$NS" wait pod "$pod" --for=condition=Ready --timeout=120s >/dev/null
kubectl -n "$NS" exec -i "$pod" -- psql -v ON_ERROR_STOP=1 -X -q -t <<'SQL'
SELECT format('DROP DATABASE %I WITH (FORCE)', datname) FROM pg_database
 WHERE datname LIKE 's\_%' OR datname IN ('niadra_cell', 'niadra_control') \gexec
SELECT 'databases left: ' || string_agg(datname, ', ' ORDER BY datname) FROM pg_database WHERE NOT datistemplate;
SQL
kubectl -n "$NS" delete pod "$pod" --wait=false >/dev/null
echo "==> databases dropped"

for cache in valkey valkey-state; do
  kubectl -n "$NS" exec "deploy/$cache" -- valkey-cli FLUSHALL >/dev/null
done
echo "==> caches flushed"

rabbit() { kubectl -n "$NS" exec niadra-rabbitmq-server-0 -c rabbitmq -- rabbitmqctl "$@" </dev/null; }
# --quiet alone still prints the "name" header. A queue can go between the listing and its purge (an
# auto-delete one whose consumer stopped): the purge fails, and only a queue still listed is an error.
queues="$(rabbit list_queues --quiet --no-table-headers name)"
for queue in $queues; do
  if ! rabbit purge_queue --quiet "$queue" >/dev/null 2>&1; then
    if rabbit list_queues --quiet --no-table-headers name | grep -qxF "$queue"; then
      echo "==> could not purge queue $queue" >&2
      exit 1
    fi
    echo "    queue $queue gone before its purge"
  fi
done
echo "==> queues purged"

BUCKET="$(kubectl -n "$NS" get configmap niadra-cell -o jsonpath='{.data.NIADRA_S3_BUCKET}')"
for prefix in $(aws s3 ls "s3://$BUCKET/" | awk '$1 == "PRE" {print $2}'); do
  [ "$prefix" = benchmarks/ ] && continue
  aws s3 rm "s3://$BUCKET/$prefix" --recursive --quiet
  echo "    s3://$BUCKET/$prefix emptied"
done

SANDBOX_SECRET="$(helm get values niadra -n "$NS" -a -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["secretStore"]["sandboxTenant"])')"
if aws secretsmanager describe-secret --secret-id "$SANDBOX_SECRET" >/dev/null 2>&1; then
  if aws secretsmanager delete-secret --secret-id "$SANDBOX_SECRET" --force-delete-without-recovery >/dev/null; then
    echo "==> secret $SANDBOX_SECRET deleted; the next deploy's tenant job writes a new one"
  else
    # The node role may delete only this secret (aws/cluster.yaml).
    echo "==> could not delete $SANDBOX_SECRET; before the deploy, from a laptop:" >&2
    echo "    aws secretsmanager delete-secret --profile niadra --region $AWS_DEFAULT_REGION --secret-id $SANDBOX_SECRET --force-delete-without-recovery" >&2
  fi
fi

# Back to the replicas they had, so the release's rollout starts from its own state; until the next
# deploy creates the databases they only restart.
for d in "${CELL[@]}"; do
  kubectl -n "$NS" scale deploy "$d" --replicas="${REPLICAS[$d]}" >/dev/null
done
echo "==> reset done; push the release branches, then: systemctl start niadra-cd.timer"
