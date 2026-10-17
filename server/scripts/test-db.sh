#!/usr/bin/env bash
# Throwaway PostgreSQL 17 for integration tests: small memory, unix socket only, deleted on stop.
#   scripts/test-db.sh start   -> prints NIADRA_TEST_DSN
#   scripts/test-db.sh stop
set -euo pipefail
PG_BIN="${PG_BIN:-$(brew --prefix postgresql@17 2>/dev/null)/bin}"
DIR="${NIADRA_TEST_PGDATA:-${TMPDIR:-/tmp}/niadra-test-pg}"
PORT="${NIADRA_TEST_PGPORT:-54329}"

case "${1:-}" in
  start)
    rm -rf "$DIR" && mkdir -p "$DIR"
    LC_ALL=C "$PG_BIN/initdb" -D "$DIR/data" -U postgres -A trust --no-sync --locale=C -E UTF8 >/dev/null
    LC_ALL=C "$PG_BIN/pg_ctl" -D "$DIR/data" -l "$DIR/log" -w start -o \
      "-k $DIR -p $PORT -c listen_addresses='' -c shared_buffers=32MB -c max_connections=40 -c fsync=off -c synchronous_commit=off -c full_page_writes=off" >/dev/null
    "$PG_BIN/createdb" -h "$DIR" -p "$PORT" -U postgres niadra_test
    echo "postgresql://postgres@/niadra_test?host=$DIR&port=$PORT"
    ;;
  stop)
    "$PG_BIN/pg_ctl" -D "$DIR/data" -m fast -w stop >/dev/null 2>&1 || true
    rm -rf "$DIR"
    ;;
  *) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
