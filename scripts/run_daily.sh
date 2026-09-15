#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

python3 -m leads run --all-enabled --since 7d
python3 -m leads pipeline enrich-pending
python3 -m leads pipeline score
# Optional: notify when backlog > N
# python3 -m leads state | python3 -c "..." 
