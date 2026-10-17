#!/usr/bin/env bash
# Empties every space of the default cell and keeps everything else: the control plane (tenants, people,
# projects, sources, keys) and the cell database (the spaces of the space directory's snapshot, the
# configuration documents, source keys, secrets and data keys). For a release whose space schema starts
# from a new baseline: that release's provisioner creates each space database again, empty, as it does
# for a new space. Runs on the k3s machine, from a laptop:
#   source scripts/lib.sh; on_ops scripts/reset-spaces.sh
# It stops the continuous deploy and leaves it stopped, with the deployed release forgotten so the next
# look deploys the release branches again even when they did not move: push new ones if wanted, then
# `systemctl start niadra-cd.timer` (deploy runbook).
#
# For each live space (`space_databases` not deleting or deleted): its database and any restored or
# retired copy of it, its row of the directory and its dead tasks, its keys in both Valkeys
# (`*:<space>:*`, the pattern the provisioner purges when it deletes a space), its objects in the cell
# bucket (server, `domain/spaces/deletion.py` `space_object_prefixes`) and its dumps
# (`backups/<database>/`). The broker's queues are purged, and the cache epoch moves on, as a restore
# does, so nothing cached for the old memory is read again.
#
# Between this script and the deploy, the running release's `loops` would create the databases again at
# its own schema, and the new release's `serve setup` would stop on a revision it does not know. So the
# script deletes the `loops` Deployment (the deploy's helm upgrade creates it again) and puts the APIs back
# to the replicas they had: their autoscalers own that field, and a deploy never sets it. The control
# plane keeps running throughout.
#
# Locally, against the services of scripts/local-e2e.sh between two checkouts (its `--between`), with the
# deployables already stopped:
#   scripts/reset-spaces.sh --local
# reads LOCAL_E2E_PG, LOCAL_E2E_CACHE, LOCAL_E2E_STATE, LOCAL_E2E_RABBIT_NODE and LOCAL_E2E_S3, and
# NIADRA_S3_BUCKET and the S3 credentials of the local cell.
set -euo pipefail
local_mode=""
[ "${1:-}" = --local ] && local_mode=1
# Must match the server's `space_object_prefixes`; a prefix missing here leaves a space's objects behind.
SPACE_PREFIXES=(raw media imports audit archive/events exports privacy/erasures turns/short turns/kept
  turns/scenario turns/held measure/raw measure/intake measure/daily measure/demand measure/bi)
NS=niadra
pod=""

if [ -n "$local_mode" ]; then
  PG_BIN="${PG_BIN:-$(brew --prefix postgresql@17)/bin}"
  RABBIT_BIN="${RABBIT_BIN:-$(brew --prefix rabbitmq)/sbin}"
  BUCKET="$NIADRA_S3_BUCKET"
  sql() {  # sql <database>, the statements on stdin
    "$PG_BIN/psql" -h 127.0.0.1 -p "$LOCAL_E2E_PG" -U postgres -d "$1" -v ON_ERROR_STOP=1 -X -q -t -A
  }
  purge_keys() {  # purge_keys <cache|state> <pattern>
    local port="$LOCAL_E2E_CACHE"
    [ "$1" = state ] && port="$LOCAL_E2E_STATE"
    redis-cli -p "$port" --scan --pattern "$2" | xargs -r -n 500 redis-cli -p "$port" unlink >/dev/null
  }
  rabbit() { "$RABBIT_BIN/rabbitmqctl" -n "$LOCAL_E2E_RABBIT_NODE" "$@" </dev/null; }
  s3_rm() { aws --endpoint-url "$LOCAL_E2E_S3" s3 rm "s3://$BUCKET/$1" --recursive --quiet; }
else
  export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
  # shellcheck disable=SC1091
  [ -f /etc/niadra/deploy.env ] && . /etc/niadra/deploy.env
  export AWS_DEFAULT_REGION="${NIADRA_REGION:-us-east-2}"
  exec 9>/run/niadra-deploy.lock
  flock 9
  systemctl stop niadra-cd.timer
  echo "==> continuous deploy stopped"

  # Nothing may write to a space while it goes: the APIs and the loops stop; the control plane stays.
  APIS=(ingest read admin insights)
  declare -A REPLICAS
  for d in "${APIS[@]}"; do
    REPLICAS[$d]="$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.spec.replicas}')"
  done
  for d in loops "${APIS[@]}"; do
    kubectl -n "$NS" scale deploy "$d" --replicas=0 >/dev/null
  done
  for d in loops "${APIS[@]}"; do
    kubectl -n "$NS" wait pod -l "app.kubernetes.io/name=$d" --for=delete --timeout=180s >/dev/null 2>&1 || true
  done
  echo "==> loops and APIs stopped"

  HOST="$(kubectl -n "$NS" get configmap niadra-cell -o jsonpath='{.data.NIADRA_DB_DIRECT_HOST}')"
  BUCKET="$(kubectl -n "$NS" get configmap niadra-cell -o jsonpath='{.data.NIADRA_S3_BUCKET}')"
  pod="reset-$RANDOM"
  trap 'kubectl -n "$NS" delete pod "$pod" --wait=false >/dev/null 2>&1 || true' EXIT
  kubectl -n "$NS" run "$pod" --quiet --restart=Never --image=postgres:17-alpine \
    --labels=niadra.com/db-client=true --overrides='{"spec":{"activeDeadlineSeconds":900,"containers":[{"name":"psql",
      "image":"postgres:17-alpine","command":["sleep","900"],
      "envFrom":[{"secretRef":{"name":"niadra-db-init"}}],
      "env":[{"name":"PGHOST","value":"'"$HOST"'"},{"name":"PGSSLMODE","value":"require"}],
      "securityContext":{"runAsUser":70,"runAsNonRoot":true,"allowPrivilegeEscalation":false}}]}}' >/dev/null
  kubectl -n "$NS" wait pod "$pod" --for=condition=Ready --timeout=120s >/dev/null
  sql() { kubectl -n "$NS" exec -i "$pod" -- psql -d "$1" -v ON_ERROR_STOP=1 -X -q -t -A; }
  purge_keys() {
    local store=valkey
    [ "$1" = state ] && store=valkey-state
    kubectl -n "$NS" exec "deploy/$store" -- sh -c \
      "valkey-cli --scan --pattern '$2' | xargs -r -n 500 valkey-cli unlink >/dev/null"
  }
  rabbit() { kubectl -n "$NS" exec niadra-rabbitmq-server-0 -c rabbitmq -- rabbitmqctl "$@" </dev/null; }
  s3_rm() { aws s3 rm "s3://$BUCKET/$1" --recursive --quiet; }
fi

purge_queues() {
  # --quiet alone still prints the "name" header. A queue can go between the listing and its purge (an
  # auto-delete one whose consumer stopped): only a queue still listed is an error.
  local queues queue
  queues="$(rabbit list_queues --quiet --no-table-headers name)"
  for queue in $queues; do
    if ! rabbit purge_queue --quiet "$queue" >/dev/null 2>&1; then
      if rabbit list_queues --quiet --no-table-headers name | grep -qxF "$queue"; then
        echo "==> could not purge queue $queue" >&2
        return 1
      fi
    fi
  done
}

# The live spaces: one per line, "<space id> <database> <instance>".
spaces="$(sql niadra_cell <<'SQL'
SELECT space_id || ' ' || db_name || ' ' || instance FROM space_databases
 WHERE status NOT IN ('deleting', 'deleted') ORDER BY db_name;
SQL
)"
if echo "$spaces" | awk 'NF && $3 != "primary" {found = 1} END {exit !found}'; then
  echo "==> a space lives on an instance of its own; this script resets the primary instance only" >&2
  exit 1
fi
echo "==> $(echo "$spaces" | grep -c . || true) live space(s)"

purge_queues
echo "==> queues purged"

sql postgres <<'SQL'
SELECT format('DROP DATABASE %I WITH (FORCE)', datname) FROM pg_database WHERE datname LIKE 's\_%' \gexec
SQL
echo "==> space databases dropped"

# One transaction: the directory forgets the live spaces, which the provisioner then creates again.
sql niadra_cell <<'SQL'
BEGIN;
DELETE FROM dead_tasks
 WHERE space_id IN (SELECT space_id FROM space_databases WHERE status NOT IN ('deleting', 'deleted'));
DELETE FROM space_databases WHERE status NOT IN ('deleting', 'deleted');
UPDATE cell_state SET value = value + 1, updated_at = now() WHERE key = 'cache_epoch';
COMMIT;
SQL
echo "==> space directory emptied, cache epoch moved on"

while read -r space db _; do
  [ -n "$space" ] || continue
  for store in cache state; do
    purge_keys "$store" "*:$space:*"
  done
  for prefix in "${SPACE_PREFIXES[@]}"; do
    s3_rm "$prefix/$space/"
  done
  s3_rm "backups/$db/"
  echo "    $db: keys and objects gone"
done <<<"$spaces"

# A task still in flight when the databases went may have been requeued.
purge_queues
echo "==> queues purged again"

if [ -z "$local_mode" ]; then
  kubectl -n "$NS" delete deploy loops >/dev/null
  echo "==> loops deleted; the deploy's helm upgrade creates it again"
  # The continuous deploy skips a release it has deployed: forget it, so the same release deploys again.
  rm -f /var/lib/niadra/deployed /var/lib/niadra/failed
  for d in "${APIS[@]}"; do
    kubectl -n "$NS" scale deploy "$d" --replicas="${REPLICAS[$d]}" >/dev/null
  done
  echo "==> reset done; push new release branches if wanted, then: systemctl start niadra-cd.timer"
else
  echo "==> reset done"
fi
