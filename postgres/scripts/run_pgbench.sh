#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/postgres_env.sh"

# Override these settings through environment variables.
CLIENTS="${CLIENTS:-16}"
THREADS="${THREADS:-4}"
DURATION="${DURATION:-60}"
SCALE="${SCALE:-100}"

# Optional first argument: rebuild benchmark tables before running.
CLEAN=false
if [[ "${1:-}" == "--clean" ]]; then
  CLEAN=true
  shift
fi

for value in "$CLIENTS" "$THREADS" "$DURATION" "$SCALE"; do
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "CLIENTS, THREADS, DURATION, and SCALE must be positive integers." >&2
    exit 1
  fi
done

"$PG_BIN/pg_isready" "${CONNECTION[@]}"

exists=$("$PG_BIN/psql" "${CONNECTION[@]}" -X -At \
  -v ON_ERROR_STOP=1 -d postgres \
  -c "SELECT 1 FROM pg_database WHERE datname = 'pgbench_test'")

if [[ "$exists" != "1" ]]; then
  "$PG_BIN/createdb" "${CONNECTION[@]}" pgbench_test
  CLEAN=true
fi

if [[ "$CLEAN" == true ]]; then
  echo "Rebuilding pgbench tables with scale $SCALE..."
  "$PG_BIN/pgbench" "${CONNECTION[@]}" \
    -i -s "$SCALE" pgbench_test
fi

# Extra arguments are forwarded to pgbench, e.g. -S for read-only.
"$PG_BIN/pgbench" "${CONNECTION[@]}" \
  -c "$CLIENTS" -j "$THREADS" -T "$DURATION" -P 5 \
  "$@" pgbench_test
