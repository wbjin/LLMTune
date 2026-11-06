#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/postgres_env.sh"

# System packages must already be installed; initialize only a new instance.
mkdir -p "$PG_SOCKET"
chmod 700 "$PG_SOCKET"
if [[ ! -e "$PG_DATA" ]]; then
  umask 077
  "$PG_BIN/initdb" -D "$PG_DATA" -U wbjin \
    --auth-local=peer --auth-host=scram-sha-256
  cat >> "$PG_DATA/postgresql.conf" <<'CONF'
listen_addresses = 'localhost'
port = 5432
unix_socket_directories = '/users/wbjin/pgsocket'
unix_socket_permissions = 0700
CONF
elif [[ ! -f "$PG_DATA/PG_VERSION" ]]; then
  echo "$PG_DATA exists but is not initialized; inspect it before proceeding." >&2
  exit 1
fi

if [[ "$(cat "$PG_DATA/PG_VERSION")" != 14 ]]; then
  echo "Expected a PostgreSQL 14 data directory." >&2
  exit 1
fi

"$PG_BIN/psql" --version
"$PG_BIN/pgbench" --version
echo "User-owned instance ready. Run: bash \"$SCRIPT_DIR/start_postgres.sh\""
