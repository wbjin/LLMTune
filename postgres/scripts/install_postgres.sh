#!/usr/bin/env bash
set -euo pipefail

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script requires Ubuntu/Debian with apt-get." >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "This script requires systemd to manage PostgreSQL." >&2
  exit 1
fi

SUDO=()
if [[ "$EUID" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Run as root or install sudo." >&2
    exit 1
  fi
  sudo -v
  SUDO=(sudo)
fi

"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y \
  postgresql postgresql-client postgresql-contrib
"${SUDO[@]}" systemctl enable --now postgresql

psql --version
pgbench --version
"${SUDO[@]}" runuser -u postgres -- pg_isready
"${SUDO[@]}" runuser -u postgres -- \
  psql -v ON_ERROR_STOP=1 -c 'SELECT version();'

echo "PostgreSQL and pgbench are installed and verified."
