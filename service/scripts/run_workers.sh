#!/usr/bin/env bash
# Launch N delivery workers (default 4) against a running API. Each claims
# independently via SKIP LOCKED, so they share the queue without coordination.
# PIDs are recorded to a pidfile so `make stop` kills exactly these workers, not
# every worker.py on the box. Ctrl-C stops them all.
#   bash scripts/run_workers.sh [count]
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$SERVICE_DIR/.venv/bin/python"
PIDFILE="/tmp/notifier_workers.pids"
count="${1:-4}"

pids=()
: > "$PIDFILE"
for index in $(seq 1 "$count"); do
    (cd "$SERVICE_DIR" && exec "$PY" worker.py >>"/tmp/worker_${index}.log" 2>&1) &
    pids+=("$!")
    echo "$!" >> "$PIDFILE"
done
echo "started $count workers: ${pids[*]}"
trap 'kill "${pids[@]}" 2>/dev/null || true; rm -f "$PIDFILE"' INT TERM
wait
