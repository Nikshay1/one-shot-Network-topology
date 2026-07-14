# VERDICT

Network-anomaly root-cause assistant. Multi-modal evidence (metrics, logs,
alerts, topology, config) → detected anomalies → ranked, tiered root-cause
hypotheses backed by an immutable evidence ledger.

## STEP 1 — scaffold, contracts, golden harness

This step is the frozen foundation:

- **`/contracts`** — four Draft-7 JSON Schemas (event envelope, anomaly event,
  ranked hypothesis, ledger record) plus the REST/SSE API contract.
- **`backend/models.py`** — pydantic v2 models mirroring every schema exactly.
- **`backend/ingest/normalize.py`** — the *single source of truth* for
  `component_id` (`normalize_component`) and `event_id` (`make_event_id`).
- **`fixtures/`** — hand-written, schema-valid sample data.
- **`tests/` + `scripts/golden.sh`** — a self-checking golden suite every later
  step must keep green.
- **`docs/re2_ss.md`** — layout of the primary **RE2-SS** dataset (unzip to the
  git-ignored `data/re2_ss/`). The contracts and `normalize_component()` are
  verified against the dataset's real pod names, metric vocabulary, and the
  empty-`level` reality of its logs.

## STEP 2 — RE2-SS adapter → event store + topology

Turns a raw RE2-SS case directory into Parquet events + a topology graph.

- **`backend/ingest/explore.py`** — CLI that prints a case's *actual* layout
  (folder tree, every CSV's columns + sample rows, JSON keys). Run it first.
- **`data/README.md`** — the observed layout + how the adapter maps it onto the
  contracts, written against the explorer's real output.
- **`backend/ingest/re2ss_adapter.py`** — `load_case(case_dir) -> CaseBundle`
  {`case_id, events, topology, inject_time`}. Metrics wide→long → metric events;
  logs → raw log events (`template_id` filled in Step 4); topology derived from
  the static sock-shop dependency map restricted to present components (RE2-SS
  ships no trace graph — documented fallback). Ground truth (`inject_time` +
  fault service/type) is quarantined to `data/labels/{case_id}.json`.
- **`backend/ingest/store.py`** — `EventStore` over DuckDB + Parquet, partitioned
  by `case_id`/`source`: `write_case`, `events(...)`, `resolve(...)`.
- **`scripts/fetch_golden_case.sh`** — extracts the golden case (`catalogue_cpu/1`)
  from `RE2-SS.zip` into `data/re2_ss/`.

The golden case yields **185,943 events** (107,908 metric + 78,035 log) across a
16-node topology; every `event_id` is schema-valid and unique and every
`component_id` is a topology node.

## STEP 3 — overlay + scenario generator

Synthetic config/alert events layered on real cases, plus 7 fully-synthetic
scenario types → 25 labeled variants. All ground truth lives in label sidecars,
never in event payloads; scenario events use the same envelope + store paths.

- **`backend/overlay/config_overlay.py`** — `apply(bundle, seed)`: injects 3–6
  config events (70% innocent red herrings 30–120s before the fault, off the
  causal path; 30% plausible triggers on the faulty component when the fault
  type could follow a change) and synthesizes SNMP-style alerts by thresholding
  real metrics. Deterministic per seed; `innocent`/`synthetic` flags go to the
  sidecar only.
- **`backend/overlay/scenarios.py`** — 7 generators (clean cascade, red-herring
  config, alert storm 150+, confounded pair, missing telemetry, topology drift,
  ambiguous "I don't know"), each `build(seed) -> (CaseBundle, LabelFile)`.
- **`scenarios/registry.json`** — 25 parameterized variants;
  `--build-all --seed 42` is fully reproducible (identical `event_id`s on
  rebuild).

```bash
py -m backend.overlay.scenarios --build-all --seed 42 --out data/parquet
py -m backend.overlay.config_overlay data/re2_ss/catalogue_cpu --seed 42 --out data/parquet
```

## STEP 4 — detection layer (4 modalities)

Deterministic detectors → schema-valid `AnomalyEvent`s that cluster after the
fault. Runtime pipeline code: reads NO ground truth (baselines come from the
first 30% of the case window).

- **`backend/detect/metrics.py`** — MAD z-score vs the first-30% baseline over a
  rolling-median-detrended residual (so monotonic drift like memory growth
  isn't flagged); anomalies are sustained runs merged into windows. Secondary
  IsolationForest (contamination 0.05) over per-component residual vectors per
  30s window.
- **`backend/detect/logs.py`** — Drain3 (depth 4, sim 0.4, masked + persisted
  per case) fills `template_id` back onto events; Poisson-tail frequency spikes
  and never-seen-in-baseline rare templates.
- **`backend/detect/alerts.py`** — dedup identical firings within 60s; flap
  suppression (≥3 fire/resolve cycles in 5min → one "flapping" anomaly).
- **`backend/detect/config.py`** — rule-based risky-change classifier
  (acl/route/limit/timeout/replica…) → `config_risky_flag` **candidate** triggers.
- **`backend/detect/runner.py`** — `detect(case_id)` runs all four, writes
  `data/anomalies/{case_id}.{parquet,json}`.

Score contract note: raw magnitudes (`z/3`, `obs/exp`, capped 10) are normalized
to the `AnomalyEvent` `[0,1]` score by ÷10. The golden sanity harness requires
≥80% of anomaly **score×extent** mass (the after-inject overlap of each windowed
anomaly) to fall after `inject_time`, read from the label sidecar in the test only.

```bash
py -m backend.detect.runner --case catalogue_cpu-1
```

### Non-negotiable rules
1. Python 3.11+, type hints everywhere, pydantic v2 for every data shape.
2. `component_id` is produced ONLY by `normalize_component()`.
3. Every id matches its schema regex in `/contracts`.
4. Ground-truth fields are read only inside `/eval` and `/scenarios`.
5. Evidence tiers are assigned ONLY in `backend/rank/tiers.py`.
6. Storage = DuckDB + Parquet; graphs = NetworkX. No agent frameworks / Kafka /
   Docker / deep learning.
7. Every module is runnable/testable via CLI without the API server.

## Quickstart

```bash
make setup      # uv venv + editable install
make golden     # contracts + normalize + CLI smoke — must be green
make test       # full pytest suite
```

On Windows without `make`/`uv`, run the golden suite directly:

```powershell
py -m pip install pydantic jsonschema pytest networkx polars duckdb pyarrow pandas
bash scripts/golden.sh                                                  # full suite + STEP 2 data checks
py -m backend.ingest.normalize normalize "Front-End-6c4d8b9f8d-abcde"   # -> front-end
py -m backend.ingest.normalize event-id metric front-end 1              # -> metric-front_end-000001
```

### STEP 2 ingest pipeline

```bash
bash scripts/fetch_golden_case.sh                                       # -> data/re2_ss/catalogue_cpu/1
py -m backend.ingest.explore data/re2_ss/catalogue_cpu                  # print the real layout
py -m backend.ingest.re2ss_adapter data/re2_ss/catalogue_cpu --out data/parquet
py -m backend.ingest.store events data/parquet catalogue_cpu-1 --source metric
```
