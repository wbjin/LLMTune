#!/usr/bin/env bash
# Shared settings for the wbjin-owned instance used by both tuners.
if [[ "$EUID" -eq 0 || "$(id -un)" != wbjin ]]; then
  echo "Run as wbjin, without sudo." >&2
  exit 1
fi

PG_BIN=/usr/lib/postgresql/14/bin
PG_DATA=/users/wbjin/pgdata
PG_SOCKET=/users/wbjin/pgsocket
PG_LOG=/users/wbjin/postgres.log
CONNECTION=(-h "$PG_SOCKET" -p 5432 -U wbjin)

for program in initdb pg_ctl psql pg_isready createdb pgbench; do
  if [[ ! -x "$PG_BIN/$program" ]]; then
    echo "Missing $PG_BIN/$program; PostgreSQL 14 must be installed first." >&2
    exit 1
  fi
done

if [[ -e "$PG_DATA" && ! -O "$PG_DATA" ]]; then
  echo "$PG_DATA must be owned by wbjin." >&2
  exit 1
fi
