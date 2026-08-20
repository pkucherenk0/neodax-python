#!/usr/bin/env bash
# Canonical way to run any perf/ k6 script. Always captures raw JSON output and automatically
# runs both post-run analyses against it (status-code breakdown, time-trend first-half-vs-
# second-half) -- use this instead of calling `k6 run` directly to get that for free every time.
#
# Usage:
#   perf/run.sh perf/scripts/market-data/smoke.js
#   perf/run.sh perf/scripts/market-data/load.js -e PERF_MARKET=ETHUSDT-PERP
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <k6-script.js> [extra k6 run args...]" >&2
  exit 1
fi

SCRIPT="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$RESULTS_DIR"

TS=$(date +%Y%m%d-%H%M%S)
BASENAME=$(basename "$SCRIPT" .js)
JSON_FILE="$RESULTS_DIR/${BASENAME}-${TS}.ndjson"

echo "Raw output will be kept at: $JSON_FILE"
echo

# k6 exits non-zero when thresholds fail -- expected sometimes (a stress test SHOULD cross its
# looser thresholds), don't let that stop the analysis below from running.
k6 run --out "json=$JSON_FILE" "$@" "$SCRIPT" || true

echo
"$SCRIPT_DIR/tools/status_code_breakdown.sh" "$JSON_FILE"
python3 "$SCRIPT_DIR/tools/time_trend_report.py" "$JSON_FILE"
