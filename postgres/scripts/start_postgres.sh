#!/usr/bin/env bash
set -euo pipefail

# Restart also starts the cluster if it is currently stopped.
sudo pg_ctlcluster --mode fast 14 main restart
sudo -u postgres pg_isready -h /var/run/postgresql -p 5432

echo "PostgreSQL 14/main is running on port 5432."
