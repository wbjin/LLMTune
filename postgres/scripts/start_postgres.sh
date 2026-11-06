#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/postgres_env.sh"

if [[ ! -f "$PG_DATA/PG_VERSION" ]]; then
  echo "Run install_postgres.sh to initialize the instance first." >&2
  exit 1
fi

mkdir -p "$PG_SOCKET"
chmod 700 "$PG_SOCKET"
if "$PG_BIN/pg_ctl" -D "$PG_DATA" status >/dev/null 2>&1; then
  "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$PG_LOG" -m fast -w restart
else
  "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$PG_LOG" -w start
fi
"$PG_BIN/pg_isready" "${CONNECTION[@]}"
"$PG_BIN/psql" "${CONNECTION[@]}" -X -v ON_ERROR_STOP=1 -d postgres \
  -c "SELECT current_user, current_setting('data_directory');"

echo "User-owned PostgreSQL is running on port 5432."
