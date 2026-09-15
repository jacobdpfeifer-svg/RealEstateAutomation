#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="$ROOT/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="python3"

"$PYTHON" -m leads run --all-enabled --since 7d
"$PYTHON" -m leads pipeline enrich-pending
"$PYTHON" -m leads pipeline score
# Optional: notify when backlog > N
# python3 -m leads state | python3 -c "..." 
