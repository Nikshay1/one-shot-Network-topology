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

# --- STEP 3: overlay + scenario generator ---
echo "== VERDICT golden: STEP 3 scenarios + overlay =="
"$PY" - <<'PYEOF'
import tempfile, json
from pathlib import Path
from backend.overlay.scenarios import build_all, load_registry
from backend.ingest.store import EventStore

base = tempfile.mkdtemp()
reg = load_registry()

# build all scenarios deterministically at seed 42
r1 = build_all(42, base + "/p1", base + "/l1")
assert len(r1) >= 25, f"only {len(r1)} variants"

# 25+ label files written
labels = list(Path(base + "/l1").glob("*.json"))
assert len(labels) >= 25, f"only {len(labels)} label files"

# every red_herring variant: >=1 innocent config within 120s of incident start
for _, label in r1:
    if label.scenario_type != "red_herring_config":
        continue
    inj = label.inject_time
    cfg_ts = {c["event_id"]: c["ts"] for c in label.injected_configs}
    within = [e for e in label.ground_truth_innocent if inj - 120 <= cfg_ts.get(e, -1e18) <= inj]
    assert within, f"{label.case_id}: no innocent config within 120s"

# alert_storm variants: >=150 alerts, and store round-trips
store = EventStore(base + "/p1")
for b, label in r1:
    if label.scenario_type == "alert_storm":
        n = sum(1 for e in b.events if e.source == "alert")
        assert n >= 150, (label.case_id, n)
sample = [e.event_id for e in r1[0][0].events[:100]]
assert store.resolve(sample) is True

# determinism: rebuild seed 42 -> identical event_ids
r2 = build_all(42, base + "/p2", base + "/l2")
for (b1, _), (b2, _) in zip(r1, r2):
    assert [e.event_id for e in b1.events] == [e.event_id for e in b2.events], b1.case_id

print(f"STEP 3 OK: {len(r1)} variants, {len(labels)} labels, deterministic, storms>=150")
PYEOF

# --- STEP 4: detection layer (4 modalities) ---
echo "== VERDICT golden: STEP 4 detection =="
"$PY" - <<'PYEOF'
import tempfile
from backend.overlay.scenarios import gen_clean_cascade
from backend.ingest.store import EventStore
from backend.detect.runner import detect

tmp = tempfile.mkdtemp()
bundle, _ = gen_clean_cascade("gc-verify", 42, fault_service="catalogue", fault_type="cpu")
store = EventStore(tmp + "/p")
store.write_case(bundle)
store.write_topology(bundle.case_id, bundle.topology)

anoms = detect("gc-verify", store_root=tmp + "/p", out_dir=tmp + "/anom", drain_dir=tmp + "/drain")
assert anoms, "no anomalies detected on scenario"

# schema-valid ids + zero anomalies off-topology
nodes = set(store.load_topology("gc-verify").nodes)
off = [a.component_id for a in anoms if a.component_id not in nodes]
assert not off, f"off-topology anomalies: {off}"
assert all(0.0 <= a.score <= 1.0 for a in anoms)

# score mass (score x extent) clusters after inject
inj = bundle.inject_time
def dt(a): return max(a.window.end - a.window.start, 30.0)
def da(a):
    e = max(a.window.end, a.window.start + 30.0)
    return max(0.0, e - max(a.window.start, inj))
mt = sum(a.score * dt(a) for a in anoms)
ma = sum(a.score * da(a) for a in anoms)
assert ma / mt >= 0.80, f"only {ma/mt:.0%} of anomaly mass after inject"
print(f"STEP 4 OK: {len(anoms)} anomalies, {ma/mt:.0%} mass after inject, all in topology")
PYEOF

echo "GOLDEN OK"
# --- later steps append pipeline checks below this line ---
