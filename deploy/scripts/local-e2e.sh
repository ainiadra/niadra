#!/usr/bin/env bash
# The deploy's end-to-end check (k8s/charts/niadra/files/e2e_sandbox.py) on this computer, against the
# deployables of server checkouts (../server), before a release goes to the machine:
#   scripts/local-e2e.sh <server checkout> [<checkout> ...] [--keep] [--decisions-down] [--burst=N] [--history=N]
#     [--between=<command>]
#
# Each checkout runs as production runs it, `python -m niadra.entrypoints.serve <role>` per deployable:
# setup (migrations, broker, bucket), control, loops, ingest, read, admin, insights and the worker pools
# of values.yaml, behind a path router that stands for the api ingress. Around them: PostgreSQL 17 sized
# like the db.t4g.micro (db-init.sql of the chart), PgBouncer with the chart's own pgbouncer.ini, RabbitMQ,
# the two Valkey stores (a Redis server), S3 (moto) and the models server of the checkout (hashing embedder).
# Extraction and the decisions call the AI endpoint NIADRA_LLM_BASE_URL names, as in the region.
#
# Several checkouts run in order on the same data, each upgrading the one before (the old release, then
# the new one, as the CD upgrades the cell), and the check runs after each. The cluster starts empty.
# Exit status: the failed steps of the last check (1 when a cell did not start).
#
# Needs: postgresql@17, pgbouncer, rabbitmq and redis (Homebrew), uv, helm, and the AI endpoint's settings:
# NIADRA_LLM_API_KEY, NIADRA_LLM_BASE_URL and NIADRA_LLM_MODEL (and NIADRA_DECISIONS_BASE_URL,
# NIADRA_DECISIONS_MODEL) in the environment, or the AWS CLI signed in to read them from Secrets Manager
# (NIADRA_PROFILE, default niadra; LOCAL_E2E_LLM_SECRET, default niadra/bench/llm, with the properties
# api_key, base_url, model, decisions_base_url and decisions_model). The key is never printed.
# LOCAL_E2E_DIR (default $TMPDIR/niadra-local-e2e) holds the logs; LOCAL_E2E_PORT (default 19700) is the
# first of the ports it takes. --keep leaves everything running, with the api at 127.0.0.1:LOCAL_E2E_PORT+9.
# --decisions-down points the decisions port at an address that takes connections and never answers (a
# checkout that reads NIADRA_DECISIONS_BASE_URL): the cell must work with the decision model down, as when
# it does not answer or the space's AI budget is spent; extraction still calls the extraction model.
# --burst=N sends N closed conversations of the e2e's customer 35 s before each check: the check then runs
# while that customer's sessions are extracted, reconciled, embedded and compiled under their lease, as it
# did on the machine during the re-extraction pass of 28/09 (the invoice lost its lease for two minutes).
# The burst ends with one long conversation (280 turns, over 20,000 characters), past both limits of a
# models pod request; after each check the script counts the models calls the workers went without.
# --history=N sends N orders of the e2e's customer before each check, as the long-lived sandbox holds after
# many runs: the check then reads a customer with more objects than one graph shows.
# --between=<command> runs the command from this component (deploy/) between two checkouts, with the first one's
# deployables stopped and the services up, as an operator runs a script between two releases: the
# environment names the services (LOCAL_E2E_PG, LOCAL_E2E_CACHE, LOCAL_E2E_STATE, LOCAL_E2E_RABBIT_NODE,
# LOCAL_E2E_S3, NIADRA_S3_BUCKET), as `scripts/reset-spaces.sh --local` reads them. It failing stops the run.
set -uo pipefail
INFRA="$(cd "$(dirname "$0")/.." && pwd)"
CHART="$INFRA/k8s/charts/niadra"
WORK="${LOCAL_E2E_DIR:-${TMPDIR:-/tmp}/niadra-local-e2e}"
P="${LOCAL_E2E_PORT:-19700}"
PG_BIN="${PG_BIN:-$(brew --prefix postgresql@17 2>/dev/null)/bin}"
RABBIT_BIN="${RABBIT_BIN:-$(brew --prefix rabbitmq 2>/dev/null)/sbin}"
PGDATA="$WORK/pg"
LOGS="$WORK/logs"

keep=""
decisions_down=""
burst=""
history=""
between=""
checkouts=()
for arg in "$@"; do
  case "$arg" in
    --keep) keep=1 ;;
    --decisions-down) decisions_down=1 ;;
    --burst=*) burst="${arg#--burst=}" ;;
    --history=*) history="${arg#--history=}" ;;
    --between=*) between="${arg#--between=}" ;;
    *) checkouts+=("$(cd "$arg" && pwd)") ;;
  esac
done
[ "${#checkouts[@]}" -gt 0 ] || { sed -n '2,4p' "$0"; exit 2; }

# Ports: the deployables from P, the services from P+10, the worker pools from P+20.
CONTROL=$P INGEST=$((P + 1)) READ=$((P + 2)) ADMIN=$((P + 3)) INSIGHTS=$((P + 4)) LOOPS=$((P + 5)) API=$((P + 9))
PG=$((P + 10)) PGB=$((P + 11)) RMQ=$((P + 12)) RMQ_DIST=$((P + 13)) CACHE=$((P + 14)) STATE=$((P + 15))
S3=$((P + 16)) MODELS=$((P + 17)) BLACKHOLE=$((P + 18))
POOLS=("realtime" "extraction" "measure,identity" "delivery,background,volume")

stop_cell() {
  for f in "$WORK"/run/*.pid; do
    [ -e "$f" ] || continue
    kill "$(cat "$f")" 2>/dev/null
    rm -f "$f"
  done
  for _ in $(seq 1 30); do pgrep -f "$WORK/local_cell" >/dev/null || break; sleep 1; done
  pkill -9 -f "$WORK/local_cell" 2>/dev/null
  return 0
}

stop_services() {
  [ -f "$WORK/pgb/pgbouncer.pid" ] && kill "$(cat "$WORK/pgb/pgbouncer.pid")" 2>/dev/null
  for store in cache state; do
    [ -f "$WORK/redis-$store.pid" ] && kill "$(cat "$WORK/redis-$store.pid")" 2>/dev/null
  done
  pkill -f "moto_server -H 127.0.0.1 -p $S3" 2>/dev/null
  pkill -f "local_cell.py blackhole $BLACKHOLE" 2>/dev/null
  RABBITMQ_NODENAME="e2e$P@localhost" "$RABBIT_BIN/rabbitmqctl" stop >/dev/null 2>&1
  "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null 2>&1
  return 0
}

finish() {
  if [ -z "$keep" ]; then
    stop_cell
    stop_services
  fi
}

up() {  # up <port> <what>: waits until something answers /healthz on the port
  for _ in $(seq 1 "${3:-120}"); do
    curl -s -o /dev/null "127.0.0.1:$1/healthz" && return 0
    sleep 1
  done
  echo "$2 did not start; the end of $LOGS/$2.log:"
  tail -20 "$LOGS/$2.log"
  return 1
}

services() {
  mkdir -p "$WORK/pgb" "$WORK/rmq" "$WORK/run" "$LOGS"
  LC_ALL=C "$PG_BIN/initdb" -D "$PGDATA" -U postgres -A trust --no-sync --locale=C -E UTF8 >/dev/null
  # db.t4g.micro: shared_buffers and max_connections as the instance has them.
  LC_ALL=C "$PG_BIN/pg_ctl" -D "$PGDATA" -l "$LOGS/postgres.log" -w start -o \
    "-p $PG -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c shared_buffers=112MB -c max_connections=79 \
     -c work_mem=4MB -c maintenance_work_mem=64MB -c shared_preload_libraries=pg_stat_statements \
     -c fsync=off -c synchronous_commit=off -c full_page_writes=off" >/dev/null || return 1
  "$PG_BIN/psql" -h 127.0.0.1 -p "$PG" -U postgres -d postgres -q -v ON_ERROR_STOP=1 \
    -v owner_password=owner -v app_password=app -v system_password=system -v site_password=site \
    -f "$CHART/files/db-init.sql" >/dev/null || return 1

  # The chart's pgbouncer.ini, pointed at this PostgreSQL, with trust in place of SCRAM on this computer.
  helm template niadra "$CHART" --namespace niadra --set part=base --set database.host=127.0.0.1 \
    --set database.sslmode=disable --set database.masterSecret=arn:local --set objectStorage.bucket=local \
    --set image.registry=local --set image.tags.server=local --set image.tags.models=local \
    --set image.tags.site=local --set image.tags.site-api=local --set keys.kmsKeyId=arn:local \
    --set audit.bucket=local --show-only templates/data.yaml |
    python3 -c 'import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "pgbouncer":
        print(d["data"]["pgbouncer.ini"])' |
    sed -e "s/port=5432/port=$PG/" -e "s/^listen_addr = .*/listen_addr = 127.0.0.1/" \
      -e "s/^listen_port = .*/listen_port = $PGB/" -e "s/^auth_type = .*/auth_type = trust/" \
      -e "s#^auth_file = .*#auth_file = $WORK/pgb/userlist.txt#" >"$WORK/pgb/pgbouncer.ini" || return 1
  # The files go in the [pgbouncer] section: the chart's ini ends with its [users] section.
  python3 - "$WORK/pgb/pgbouncer.ini" "$LOGS/pgbouncer.log" "$WORK/pgb/pgbouncer.pid" <<'PY' || return 1
import sys
path, log, pid = sys.argv[1:]
ini = open(path).read()
assert "[pgbouncer]\n" in ini
open(path, "w").write(ini.replace("[pgbouncer]\n", f"[pgbouncer]\nlogfile = {log}\npidfile = {pid}\n", 1))
PY
  printf '"niadra_app" ""\n"niadra_worker" ""\n"niadra_system" ""\n"niadra_owner" ""\n' >"$WORK/pgb/userlist.txt"
  pgbouncer -d "$WORK/pgb/pgbouncer.ini" || return 1

  : >"$WORK/rmq/enabled_plugins"
  RABBITMQ_NODENAME="e2e$P@localhost" RABBITMQ_NODE_PORT=$RMQ RABBITMQ_DIST_PORT=$RMQ_DIST \
    RABBITMQ_MNESIA_BASE="$WORK/rmq/mnesia" RABBITMQ_LOG_BASE="$WORK/rmq/log" \
    RABBITMQ_ENABLED_PLUGINS_FILE="$WORK/rmq/enabled_plugins" RABBITMQ_CONFIG_FILE="$WORK/rmq/rabbitmq" \
    nohup "$RABBIT_BIN/rabbitmq-server" >"$LOGS/rabbitmq.log" 2>&1 </dev/null &
  # The two stores of values.yaml: the cache evicts, the state store never does.
  redis-server --port "$CACHE" --maxmemory 64mb --maxmemory-policy allkeys-lru --save "" --appendonly no \
    --daemonize yes --logfile "$LOGS/redis-cache.log" --pidfile "$WORK/redis-cache.pid" >/dev/null
  redis-server --port "$STATE" --maxmemory 64mb --maxmemory-policy noeviction --save "" --appendonly no \
    --daemonize yes --logfile "$LOGS/redis-state.log" --pidfile "$WORK/redis-state.pid" >/dev/null
  nohup uvx --quiet --from "moto[server]" moto_server -H 127.0.0.1 -p "$S3" >"$LOGS/s3.log" 2>&1 </dev/null &
  for port in $RMQ $S3; do
    for _ in $(seq 1 120); do nc -z 127.0.0.1 "$port" 2>/dev/null && break; sleep 1; done
    nc -z 127.0.0.1 "$port" 2>/dev/null || { echo "nothing listens on $port"; return 1; }
  done
}

environment() {  # the configmap and the secrets of the chart, with this computer's addresses
  export NIADRA_REGION=us-east-2 NIADRA_CELL_ID=sandbox-1 NIADRA_LOG_LEVEL=INFO NIADRA_ALLOWED_ORIGINS=
  export NIADRA_CONTROL_PUBLIC_URL="http://127.0.0.1:$CONTROL" NIADRA_CONTROL_URL="http://127.0.0.1:$CONTROL"
  export NIADRA_DB_HOST=127.0.0.1 NIADRA_DB_PORT=$PGB NIADRA_DB_SSLMODE=disable
  export NIADRA_DB_DIRECT_HOST=127.0.0.1 NIADRA_DB_DIRECT_PORT=$PG NIADRA_DB_DIRECT_SSLMODE=disable
  export NIADRA_PGBOUNCER_ADMIN="127.0.0.1:$PGB"
  export NIADRA_DB_APP_PASSWORD=app NIADRA_DB_SYSTEM_PASSWORD=system NIADRA_DB_OWNER_PASSWORD=owner
  export NIADRA_S3_BUCKET=niadra-local NIADRA_S3_REGION=us-east-2 NIADRA_S3_ENDPOINT="http://127.0.0.1:$S3"
  export NIADRA_S3_ACCESS_KEY_ID=local NIADRA_S3_SECRET_ACCESS_KEY=local
  export NIADRA_REDIS_URL="redis://127.0.0.1:$CACHE/0" NIADRA_STATE_REDIS_URL="redis://127.0.0.1:$STATE/0"
  export NIADRA_MODELS_URL="http://127.0.0.1:$MODELS" NIADRA_RABBITMQ_URL="amqp://guest:guest@127.0.0.1:$RMQ/"
  export NIADRA_WORKER_PREFETCH=16 NIADRA_KEK=env:NIADRA_KEK_VALUE
  export NIADRA_CONTROL_SIGNING_KEY_FILE="$WORK/signing.pem"
  local name settings
  for name in NIADRA_KEK_VALUE NIADRA_KEY_PEPPER NIADRA_SUBJECT_TOKEN_SECRET NIADRA_CELL_TOKEN \
    NIADRA_CONTROL_OPERATOR_TOKEN NIADRA_CONTROL_SECRETS_KEY; do
    export "$name=$(openssl rand -hex 32)"
  done
  if [ -z "${NIADRA_LLM_API_KEY:-}" ]; then
    settings="$(aws secretsmanager get-secret-value --profile "${NIADRA_PROFILE:-niadra}" \
      --region us-east-2 --secret-id "${LOCAL_E2E_LLM_SECRET:-niadra/bench/llm}" \
      --query SecretString --output text | python3 -c '
import json, os, shlex, sys
d = json.load(sys.stdin)
for name, key in (("NIADRA_LLM_API_KEY", "api_key"), ("NIADRA_LLM_BASE_URL", "base_url"), ("NIADRA_LLM_MODEL", "model"),
                  ("NIADRA_DECISIONS_BASE_URL", "decisions_base_url"), ("NIADRA_DECISIONS_MODEL", "decisions_model")):
    if d.get(key) and not os.environ.get(name):
        print(f"export {name}={shlex.quote(d[key])}")')" ||
      { echo "the AI endpoint's settings could not be read"; return 1; }
    eval "$settings"
  fi
  export NIADRA_LLM_ZERO_RETENTION="${NIADRA_LLM_ZERO_RETENTION:-true}" NIADRA_LLM_INCLUDE_USAGE="${NIADRA_LLM_INCLUDE_USAGE:-true}"
  if [ -n "$decisions_down" ]; then
    export NIADRA_DECISIONS_BASE_URL="http://127.0.0.1:$BLACKHOLE/api"
    nohup python3 "$WORK/local_cell.py" blackhole "$BLACKHOLE" >"$LOGS/blackhole.log" 2>&1 </dev/null &
    echo "==> the decision model is down: its calls go to 127.0.0.1:$BLACKHOLE, which never answers"
  fi
}

start() {  # start <name> <port> <serve args...>
  local name=$1 port=$2
  shift 2
  (cd "$CHECKOUT" && nohup .venv/bin/python "$WORK/local_cell.py" launch "$port" "$@" >"$LOGS/$name.log" 2>&1 </dev/null &
    echo $! >"$WORK/run/$name.pid")
}

cell() {  # the deployables of $CHECKOUT, as the chart runs them
  (cd "$CHECKOUT" && uv sync --frozen --quiet) || return 1
  (cd "$CHECKOUT" && .venv/bin/python -m niadra.entrypoints.serve setup >"$LOGS/setup.log" 2>&1) ||
    { echo "setup failed; the end of $LOGS/setup.log:"; tail -20 "$LOGS/setup.log"; return 1; }
  if ! curl -s -o /dev/null "127.0.0.1:$MODELS/healthz"; then
    (cd "$CHECKOUT/models" && uv sync --frozen --quiet) || return 1
    # The venv's own uvicorn, backgrounded alone: `$!` is then the server itself, which the stop kills. Behind
    # `uv run`, or at the end of an `&&` list, it named a parent and the server outlived the run.
    (cd "$CHECKOUT/models" || exit 1
      nohup .venv/bin/uvicorn niadra_models.api:create_app --factory --host 127.0.0.1 --port "$MODELS" --workers 1 \
        --no-access-log >"$LOGS/models.log" 2>&1 </dev/null &
      echo $! >"$WORK/run/models.pid")
    up "$MODELS" models 300 || return 1
  fi
  start control "$CONTROL" control
  up "$CONTROL" control || return 1
  if [ ! -s "$WORK/bootstrap.json" ]; then
    (cd "$CHECKOUT" && .venv/bin/python -m niadra.entrypoints.control_cli bootstrap --tenant Niadra --slug niadra \
      --project "Atendimento e cobrança" --project-slug atendimento --email e2e@niadra.com --region us-east-2 \
      --cell sandbox-1 --cell-address "http://127.0.0.1:$API" --output "$WORK/bootstrap.json" \
      >"$LOGS/bootstrap.log" 2>&1) || { echo "the sandbox tenant was not created"; tail -20 "$LOGS/bootstrap.log"; return 1; }
  fi
  # The loops and the workers log in as niadra_worker, as the chart runs them (values.yaml, pgbouncer).
  NIADRA_DB_APP_LOGIN=niadra_worker start loops "$LOOPS" loops
  start ingest "$INGEST" ingest
  start read "$READ" read
  start admin "$ADMIN" admin
  start insights "$INSIGHTS" insights
  local i=0 pool
  for pool in "${POOLS[@]}"; do
    NIADRA_DB_APP_LOGIN=niadra_worker start "workers-${pool%%,*}" $((P + 20 + i)) workers --pools "$pool"
    i=$((i + 1))
  done
  printf '{"ingest": "http://127.0.0.1:%s", "read": "http://127.0.0.1:%s", "admin": "http://127.0.0.1:%s", "insights": "http://127.0.0.1:%s"}\n' \
    "$INGEST" "$READ" "$ADMIN" "$INSIGHTS" >"$WORK/upstreams.json"
  (cd "$CHECKOUT" && nohup .venv/bin/python "$WORK/local_cell.py" proxy "$API" "$WORK/upstreams.json" \
    >"$LOGS/proxy.log" 2>&1 </dev/null & echo $! >"$WORK/run/proxy.pid")
  up "$LOOPS" loops && up "$INGEST" ingest && up "$READ" read && up "$ADMIN" admin && up "$INSIGHTS" insights || return 1
  i=0
  for pool in "${POOLS[@]}"; do
    up $((P + 20 + i)) "workers-${pool%%,*}" || return 1
    i=$((i + 1))
  done
  for _ in $(seq 1 30); do nc -z 127.0.0.1 "$API" 2>/dev/null && break; sleep 1; done
  nc -z 127.0.0.1 "$API" 2>/dev/null || { echo "the api router did not start"; return 1; }
  settled
}

settled() {  # waits until every space database has this checkout's schema, as `serve setup` leaves a fleet
  local head rows
  head="$(cd "$CHECKOUT" && .venv/bin/alembic --name space heads 2>/dev/null | awk '{print $1}')"
  for _ in $(seq 1 180); do
    rows="$("$PG_BIN/psql" -h 127.0.0.1 -p "$PG" -U postgres -d niadra_cell -tAF ' ' -c \
      "SELECT environment, status, schema_version, coalesce(status_detail, '') = 'contract pending'
       FROM space_databases WHERE status NOT IN ('deleting', 'deleted')")"
    if [ -n "$rows" ] && ! echo "$rows" | awk -v head="$head" '$3 != head && $4 != "t" {bad = 1} END {exit !bad}'; then
      break
    fi
    sleep 1
  done
  echo "$rows" | awk '{printf "    space database (%s): %s at %s%s\n", $1, $2, $3, ($4 == "t" ? ", contract pending" : "")}'
  # A space database whose creation failed is named here: the check that follows would fail on it.
  "$PG_BIN/psql" -h 127.0.0.1 -p "$PG" -U postgres -d niadra_cell -tA -c \
    "SELECT '    failed (' || environment || '): ' || left(regexp_replace(status_detail, '\s+', ' ', 'g'), 200) FROM space_databases WHERE status = 'failed'"
  return 0
}

stop_cell >/dev/null 2>&1
stop_services >/dev/null 2>&1
rm -rf "$WORK"
mkdir -p "$WORK/run" "$LOGS"
trap finish EXIT
cp "$INFRA/scripts/local_cell.py" "$WORK/local_cell.py"
environment || exit 1
(cd "${checkouts[0]}" && uv sync --frozen --quiet) || exit 1
"${checkouts[0]}/.venv/bin/python" -c 'import sys
from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.generate().private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption())
open(sys.argv[1], "wb").write(key)' "$WORK/signing.pem" || exit 1
services || { echo "the services did not start; logs in $LOGS"; exit 1; }

failed=1
first=1
for CHECKOUT in "${checkouts[@]}"; do
  stop_cell
  if [ -z "$first" ] && [ -n "$between" ]; then
    echo "==> between the checkouts: $between"
    (cd "$INFRA" && LOCAL_E2E_PG=$PG LOCAL_E2E_CACHE=$CACHE LOCAL_E2E_STATE=$STATE \
      LOCAL_E2E_RABBIT_NODE="e2e$P@localhost" LOCAL_E2E_S3="http://127.0.0.1:$S3" PG_BIN="$PG_BIN" \
      RABBIT_BIN="$RABBIT_BIN" AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local AWS_DEFAULT_REGION=us-east-2 \
      bash -c "$between") || { echo "the command between the checkouts failed"; exit 1; }
  fi
  first=""
  echo "==> $(cd "$CHECKOUT" && git log --oneline -1 | cut -c1-100)"
  cell || exit 1
  if [ -n "$history" ]; then
    "$CHECKOUT/.venv/bin/python" "$WORK/local_cell.py" history "$API" "$WORK/bootstrap.json" "$history" || exit 1
  fi
  if [ -n "$burst" ]; then
    "$CHECKOUT/.venv/bin/python" "$WORK/local_cell.py" burst "$API" "$WORK/bootstrap.json" "$burst" || exit 1
    sleep 35
  fi
  "$CHECKOUT/.venv/bin/python" "$CHART/files/e2e_sandbox.py" --api "http://127.0.0.1:$API" \
    --control "http://127.0.0.1:$CONTROL" --bootstrap "$WORK/bootstrap.json" 2>&1 | tee "$LOGS/e2e-$(basename "$CHECKOUT").txt"
  failed=${PIPESTATUS[0]}
  # A models call that failed (a timeout or an outage; on releases up to 106d3f6 also a refused input) takes
  # the conservative default or retries the extraction; later releases log a refused input (413) apart.
  echo "==> models calls the workers went without: $(cat "$LOGS"/workers-*.log | grep -c 'models call failed')," \
    "refused: $(cat "$LOGS"/workers-*.log | grep -c 'models refused the input')"
  if [ -n "$decisions_down" ]; then
    # A turn the decision model did not screen is never trusted as ordinary text: the
    # extraction waits for the decision model with backoff (5, 20, 80, 320 s) and only its last attempt
    # settles, so memory arrives about 7 minutes after the conversation closes. With the decision model
    # down, these two steps wait by design; every other step must pass.
    waiting="$(grep -cE '^FAIL  (extraction turned the conversation into memory|history search):' \
      "$LOGS/e2e-$(basename "$CHECKOUT").txt")"
    [ "$waiting" -gt 0 ] && echo "==> $waiting step(s) wait for the decision model by design, not counted"
    failed=$((failed - waiting))
  fi
done
[ -n "$keep" ] && echo "==> kept: api http://127.0.0.1:$API, control http://127.0.0.1:$CONTROL, logs in $LOGS"
exit "$failed"
