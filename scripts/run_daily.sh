#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Default target is Postgres when DATABASE_URL is set in config/secrets.env;
# this script does not pass --db, so do not invoke it as a scratch rehearsal.
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="${LEADS_PYTHON:-$ROOT/.venv/bin/python3}"
[ -x "$PYTHON" ] || PYTHON="python3"

# run already enriches and scores; repeating them retries paid failures twice.
exec "$PYTHON" -m leads run --all-enabled --since 7d --max-enrich 5 --append-only
