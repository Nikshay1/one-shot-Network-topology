# VERDICT

**V**erdict **E**vidence-**R**anked **D**iagnosis of **I**ncident **C**ausal **T**opology —
a network-anomaly root-cause assistant.

Multi-modal evidence (metrics, logs, alerts, topology, config) → detected
anomalies → ranked, tiered root-cause hypotheses backed by an immutable evidence
ledger.

```
 RE2-SS case ─▶ ingest/adapter ─▶ EventStore (DuckDB+Parquet, by case/source)
                                        │
              overlay + scenarios ──────┤   (synthetic config/alert + 7 scenario
                                        │    types → 25 labeled variants)
                                        ▼
                                    detection ── metrics · logs · alerts · config
                                        │        → schema-valid AnomalyEvents
                                        ▼
                                    [ rank · tiers · ledger · API ]   ← upcoming
```

Every stage is deterministic and runnable/testable from the CLI without the API
server, and every data shape is a frozen contract.

## Status

| Stage | What | State |
| ----- | ---- | ----- |
| 1 | Contracts, pydantic models, normalization, golden harness | ✅ |
| 2 | RE2-SS adapter → event store + topology | ✅ |
| 3 | Config/alert overlay + 7-type scenario generator (25 variants) | ✅ |
| 4 | Detection layer (metrics, logs, alerts, config) | ✅ |
| 5+ | Ranking, tiers, evidence ledger, twin/counterfactual, API/UI | ⏳ |

`scripts/golden.sh` — the self-checking gate every stage keeps green — currently
runs **113 tests** plus end-to-end data checks for stages 2–4.

## Repository layout

```
contracts/              4 Draft-7 JSON Schemas + api_contract.md   (frozen)
backend/
  ingest/               normalize · explore · re2ss_adapter · store
  overlay/              config_overlay · scenarios
  detect/               metrics · logs · alerts · config · runner
  models.py             pydantic v2 models mirroring every schema
fixtures/               hand-written schema-valid sample data
scenarios/registry.json 25 scenario variants
scripts/                golden.sh · fetch_golden_case.sh
docs/re2_ss.md          RE2-SS dataset reference
data/                   (git-ignored) re2_ss/ parquet/ labels/ anomalies/ drain3/
tests/                  contracts · normalize · adapter · overlay · detect
```

## Setup

Requires Python 3.11+. Intended toolchain is `uv` + `make`:

```bash
make setup      # uv venv + editable install
make golden     # the self-checking gate — must be green
make test       # full pytest suite
```

On a machine without `uv`/`make` (e.g. Windows with only the `py` launcher):

```powershell
py -m pip install pydantic jsonschema pytest networkx polars duckdb pyarrow pandas scikit-learn scipy drain3
bash scripts/golden.sh          # probes for a working interpreter; runs the whole gate
```

## Pipeline

### 1 — Contracts & normalization (`contracts/`, `backend/models.py`, `backend/ingest/normalize.py`)

Four frozen Draft-7 schemas — **event envelope**, **anomaly event**, **ranked
hypothesis**, **ledger record** — plus the REST/SSE `api_contract.md`. Pydantic
v2 models mirror each schema exactly (`extra="forbid"`, discriminated payload
union, `score_breakdown` sums to `score`).

`normalize_component()` is the **single source of truth** for `component_id`
(lowercase → strip k8s pod-hash suffixes → separators to hyphens → alias map →
regex-validate); `make_event_id()` is the single source for `event_id`. Verified
against the real RE2-SS pod names and metric vocabulary.

```bash
py -m backend.ingest.normalize normalize "Front-End-6c4d8b9f8d-abcde"   # -> front-end
py -m backend.ingest.normalize event-id metric front-end 1              # -> metric-front_end-000001
```

### 2 — Ingest: RE2-SS → event store + topology (`backend/ingest/`)

`explore.py` prints a case's *actual* layout; `data/README.md` documents it and
drives the adapter. `re2ss_adapter.py:load_case()` turns a case directory into a
`CaseBundle{case_id, events, topology, inject_time}` — metrics wide→long, logs →
raw events (`template_id` filled in stage 4), topology from the static sock-shop
dependency map restricted to present components (RE2-SS ships no trace graph).
Ground truth (`inject_time`, fault service/type) is quarantined into
`data/labels/{case_id}.json`. `store.py` is an `EventStore` over DuckDB + Parquet
partitioned by `case_id`/`source` (`write_case`, `events`, `resolve`, topology
persistence).

The golden case (`catalogue_cpu/1`) yields **185,943 events** (107,908 metric +
78,035 log) across a 16-node topology; all `event_id`s are unique and every
`component_id` is a topology node.

```bash
bash scripts/fetch_golden_case.sh                    # -> data/re2_ss/catalogue_cpu/1
py -m backend.ingest.explore data/re2_ss/catalogue_cpu
py -m backend.ingest.re2ss_adapter data/re2_ss/catalogue_cpu --out data/parquet
py -m backend.ingest.store events data/parquet catalogue_cpu-1 --source metric
```

### 3 — Overlay & scenarios (`backend/overlay/`, `scenarios/registry.json`)

`config_overlay.apply(bundle, seed)` layers synthetic events onto a case: 3–6
config changes (70% innocent red herrings 30–120s pre-fault off the causal path;
30% plausible triggers on the faulty component when the fault type could follow a
change) plus SNMP-style alerts by thresholding real metrics. `scenarios.py` has 7
generators — clean cascade, red-herring config, alert storm (150+), confounded
pair, missing telemetry, topology drift, ambiguous "I don't know" — parameterized
to **25 reproducible variants**. All ground truth lives in label sidecars, never
in payloads; scenario events use the same envelope + store paths.

```bash
py -m backend.overlay.scenarios --build-all --seed 42 --out data/parquet
py -m backend.overlay.config_overlay data/re2_ss/catalogue_cpu --seed 42 --out data/parquet
```

### 4 — Detection (`backend/detect/`)

Deterministic detectors → schema-valid `AnomalyEvent`s. Runtime pipeline code:
reads **no** ground truth — baselines come from the first 30% of the case window.

- **metrics** — MAD z-score on a rolling-median-detrended residual (so monotonic
  drift like memory growth isn't flagged); sustained runs merged into windows.
  Secondary IsolationForest (contamination 0.05) over per-component residual
  vectors per 30s window.
- **logs** — Drain3 (depth 4, sim 0.4, masked + persisted per case) fills
  `template_id` back onto events; Poisson-tail frequency spikes and
  rare-after-baseline templates.
- **alerts** — 60s dedup + flap suppression (≥3 fire/resolve cycles in 5min).
- **config** — rule-based risky-change classifier (acl/route/limit/timeout/
  replica…) → `config_risky_flag` **candidate** triggers.

Raw magnitudes (`z/3`, `obs/exp`, capped 10) are normalized to the `AnomalyEvent`
`[0,1]` contract by ÷10. The golden sanity harness requires ≥80% of anomaly
**score × extent** mass (each windowed anomaly's overlap after `inject_time`) to
fall after the fault, with `inject_time` read from the sidecar in the test only.

```bash
py -m backend.detect.runner --case catalogue_cpu-1
```

## Non-negotiable rules

1. Python 3.11+, type hints everywhere, pydantic v2 for every data shape.
2. `component_id` is produced ONLY by `normalize_component()`.
3. Every id matches its schema regex in `/contracts`.
4. Ground-truth fields (`fault_service`, `inject_time`, `ground_truth_innocent`)
   are read only inside `/eval` and `/scenarios` label files — never by pipeline
   code at runtime.
5. Evidence tiers are assigned ONLY in `backend/rank/tiers.py`.
6. Storage = DuckDB + Parquet; graphs = NetworkX. No agent frameworks / Kafka /
   Docker / deep learning.
7. Every module is runnable/testable via CLI without the API server.

## Testing

`bash scripts/golden.sh` runs the full pytest suite (`tests/`) followed by
in-process end-to-end checks:

- **stage 2** — golden case loads to >1000 events, all `component_id`s in
  topology, all `event_id`s valid + unique, `store.resolve()` round-trips.
- **stage 3** — 25 scenario labels exist, every red-herring variant has an
  innocent config within 120s of incident start, alert storms ≥150, rebuild at
  seed 42 is byte-identical.
- **stage 4** — detected anomalies cluster after `inject_time` (≥80% score×extent
  mass) with none off-topology.

## Dataset

Primary dataset is **RE2-SS** (RCAEval Sock-Shop fault-injection benchmark): 30
cases = 5 services × 6 fault types, 3 runs each. Extract to the git-ignored
`data/re2_ss/`. Full layout + adapter mapping in `docs/re2_ss.md` and
`data/README.md`.
