#!/usr/bin/env bash
# Status code breakdown for a k6 raw JSON/NDJSON output file (--out json=...). No status code
# needs to be known ahead of time -- k6 only keeps a per-tag breakdown for values referenced by
# a threshold, so an un-thresholded code (a stray 400, a 429) silently never shows up in the
# plain terminal summary. Reading the raw stream back has no such limit: every request is
# auto-tagged with its real status. Called automatically by ../run.sh after every run.
#
# Usage: perf/tools/status_code_breakdown.sh <ndjson-file>
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <ndjson-file>" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required (brew install jq) -- skipping status code breakdown" >&2
  exit 0
fi

echo "=== Status code breakdown (all requests, this run) ==="
jq -r 'select(.type=="Point" and .metric=="http_reqs") | .data.tags.status' "$1" \
  | sort | uniq -c | sort -rn
