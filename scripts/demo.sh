#!/usr/bin/env bash
# make demo-N  ->  reset run state for demo scenario N, assert its caches are warm,
# then fire it. OFFLINE is honored: OFFLINE=1 makes the whole path zero-API-call.
#
#   make demo-1                 # scenario 1, against the API if it is up
#   OFFLINE=1 make demo-3       # ...with no network at all
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8

N="${1:?usage: demo.sh <scenario-number, 1-based>}"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

PY=""
for cand in python python3 py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys; assert sys.version_info>=(3,11)" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || { echo "no working python >=3.11 interpreter found" >&2; exit 1; }

CASE="$("$PY" -m backend.narrate.cache --case-for "$N")"
echo "== demo $N: case=$CASE  OFFLINE=${OFFLINE:-0} =="

# --- reset: the RUN's state, never the caches (they are the demo's safety net) ---
rm -f "data/ledger/${CASE}.jsonl" "data/reports/${CASE}.pdf"
echo "-- reset ledger + report for $CASE"

# --- assert the caches this demo depends on are warm ---
"$PY" -m backend.narrate.cache --assert-warm "$CASE"

# --- fire it: through the API if it is up, else in-process ---
if curl -fsS --max-time 2 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "-- API is up: firing through POST /case/${CASE}/run"
  RUN=$(curl -fsS -X POST "http://${HOST}:${PORT}/case/${CASE}/run" \
        -H 'content-type: application/json' \
        -d '{"speed":0,"seed":42,"twin_enabled":true}' | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
  echo "-- run_id=$RUN  streaming ..."
  curl -fsS -N --max-time 300 "http://${HOST}:${PORT}/stream/${RUN}" \
    | grep -m1 -E '^event: pipeline_(done|error)' || true
  curl -fsS "http://${HOST}:${PORT}/run/${RUN}/verdict" \
    | "$PY" -c 'import json,sys; v=json.load(sys.stdin); [print(f"  #{h[\"rank\"]} {h[\"suspect_component\"]:<14} {h[\"score\"]:.3f}  {h[\"tier\"]}") for h in v["hypotheses"][:3]]'
  curl -fsS "http://${HOST}:${PORT}/run/${RUN}/remediation" -o /dev/null -w '  remediation: HTTP %{http_code}\n' || true
else
  echo "-- API is not up on ${HOST}:${PORT}: running the pipeline in-process"
  "$PY" -m backend.pipeline --case "$CASE" --top 3
fi

echo "DEMO $N OK ($CASE)"
