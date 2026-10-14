#!/usr/bin/env bash
# Start the backend, the dashboard and the simulated spacecraft together; Ctrl-C stops all three.
#   scripts/dev.sh [scenario] [speed]        e.g. scripts/dev.sh novel_event 120
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCENARIO="${1:-nominal_day}"
SPEED="${2:-60}"
PY="$ROOT/backend/.venv/bin/python"

trap 'kill 0' EXIT INT TERM

(cd "$ROOT/backend" && .venv/bin/fastapi dev app/main.py --port 8000) &
(cd "$ROOT/frontend" && pnpm dev) &

until curl -sf http://localhost:8000/api/health >/dev/null; do sleep 1; done
echo "backend up; dashboard: http://localhost:3000/dashboard (Mission ops)"
(cd "$ROOT/simulator" && "$PY" -m satsim --api http://localhost:8000 --scenario "$SCENARIO" --speed "$SPEED") &
wait
