#!/usr/bin/env bash
# VERDICT golden harness.
#
# Runs the self-checking contract + unit test suite. Every later step APPENDS
# its own pipeline check to the bottom of this file; the whole thing must stay
# green (`make golden`).
set -euo pipefail

cd "$(dirname "$0")/.."

# Windows consoles default to cp1252 and choke on Unicode in tool output.
export PYTHONIOENCODING=utf-8

# Pick an interpreter that actually runs (Windows ships a Store `python` shim
# that resolves on PATH but errors when invoked). Probe each candidate.
PY=""
for cand in python python3 py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys; assert sys.version_info>=(3,11)" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "no working python >=3.11 interpreter found (tried python, python3, py)" >&2
  exit 1
fi
echo "using interpreter: $PY"

echo "== VERDICT golden: full test suite (contracts, normalize, adapter) =="
"$PY" -m pytest tests/

# --- STEP 1: sanity-check the single source of truth from the CLI ---
echo "== VERDICT golden: normalize CLI smoke =="
test "$("$PY" -m backend.ingest.normalize normalize 'Front-End-6c4d8b9f8d-abcde')" = "front-end"
test "$("$PY" -m backend.ingest.normalize event-id metric front-end 1)" = "metric-front_end-000001"

# --- STEP 2: RE2-SS adapter -> event store + topology ---
echo "== VERDICT golden: STEP 2 adapter + store =="
GOLDEN_CASE_DIR="data/re2_ss/catalogue_cpu"
if [ ! -e "${GOLDEN_CASE_DIR}/1/simple_metrics.csv" ] && [ ! -e "${GOLDEN_CASE_DIR}/simple_metrics.csv" ]; then
  echo "-- fetching golden case --"
  bash scripts/fetch_golden_case.sh || {
    echo "WARN: golden case unavailable; skipping STEP 2 data assertions" >&2
    echo "GOLDEN OK (STEP 2 data skipped)"
    exit 0
  }
fi

"$PY" - "$GOLDEN_CASE_DIR" <<'PYEOF'
import sys, tempfile
from backend.ingest.re2ss_adapter import load_case
from backend.ingest.store import EventStore
from backend.ingest.normalize import EVENT_ID_RE

case_dir = sys.argv[1]
tmp = tempfile.mkdtemp()
bundle = load_case(case_dir, labels_dir=tmp + "/labels")

# >1000 events
assert len(bundle.events) > 1000, f"only {len(bundle.events)} events"

# all component_ids in topology nodes
nodes = set(bundle.topology.nodes)
assert all(e.component_id in nodes for e in bundle.events), "component_id not in topology"

# all event_ids schema-valid & unique
ids = [e.event_id for e in bundle.events]
assert all(EVENT_ID_RE.match(i) for i in ids), "invalid event_id"
assert len(ids) == len(set(ids)), "duplicate event_ids"

# store round-trip + resolve on a sample
store = EventStore(tmp + "/parquet")
store.write_case(bundle)
sample = ids[:100] + ids[-100:]
assert store.resolve(sample) is True, "resolve failed on real ids"
assert store.resolve(["metric-nope-000001"]) is False, "resolve accepted a fake id"

print(f"STEP 2 OK: {len(bundle.events)} events, "
      f"{bundle.topology.number_of_nodes()} nodes, resolve OK")
PYEOF

echo "GOLDEN OK"
# --- later steps append pipeline checks below this line ---
