# RE2-SS data layout (observed) + adapter mapping

This file documents the **actual** layout of the RE2-SS dataset as reported by
`python -m backend.ingest.explore <case_dir>`, and how `re2ss_adapter.py` maps
it onto the frozen contracts. The adapter is written against THIS file; this
file is written against the explorer's real output (case `catalogue_cpu/1`).

Extract the dataset under `data/re2_ss/` (git-ignored). See
`scripts/fetch_golden_case.sh` to materialize the golden case.

## Directory tree (3 levels)

```
data/re2_ss/
  <service>_<faulttype>/          # 30 cases: 5 services x 6 fault types
    <run>/                        # repetitions: 1, 2, 3
      simple_metrics.csv          # curated wide metrics  <-- adapter METRIC SOURCE
      metrics.csv                 # raw Prometheus wide metrics (434-444 cols, varies)
      logs.csv                    # raw logs               <-- adapter LOG SOURCE
      logts.csv                   # per-template counts (Step 4 uses this)
      cluster_info.json           # Drain3 log-template catalog (Step 4)
      inject_time.txt             # GROUND TRUTH epoch-second int (-> sidecar only)
      pod-node-1.csv              # POD,NODE_NAME (before)
      pod-node-2.csv              # POD,NODE_NAME (after)
      metrics_postprocess.log     # collection log (ignored)
```

**Column counts vary per case** (observed: `metrics.csv` 434 cols for
`catalogue_cpu`, 444 for `payment_mem`; `simple_metrics.csv` 76 cols). The
adapter is schema-agnostic: it melts whatever `<component>_<signal>` columns are
present rather than hardcoding a column list.

## File schemas (observed)

### `simple_metrics.csv`  — the metric source (wide)
- Column 0: `time` (epoch **seconds**, int).
- Columns 1..N: `<component>_<signal>`, split on the **first** `_`
  (component tokens never contain `_`). Signals observed:
  `cpu, mem, diskio, socket, workload, error, latency-50, latency-90`.
- Not every component has every signal; **empty cells** (`''`) mean "not
  collected" and are dropped (no event emitted). E.g. `front-end_cpu` is empty
  in `catalogue_cpu/1`.

Adapter: wide → long → one **metric** `EventEnvelope` per non-null
`(time, component, signal, value)`. Component via `normalize_component`,
`event_id` via `make_event_id("metric", component, seq)` (seq unique per
`(source, component)`). `severity`/anomaly scoring is NOT done here (Step 3).

### `logs.csv` — the log source
Columns: `time` (HH:MM), `timestamp` (epoch **nanoseconds**), `container_name`,
`message`, `level`, `req_path`, `error`.
- **`level` is empty for ~100% of rows** — mapped to `null` (contract makes
  `LogPayload.level` optional; a non-empty value must be in the level enum or is
  dropped to null).
- `timestamp` (ns) → envelope `ts` (÷ 1e9, epoch seconds).
- `container_name` → `component_id` via `normalize_component`.
- `req_path` / `error` → optional `LogPayload` fields; `template_id` is left
  `null` (Step 4 fills it via Drain3 against `cluster_info.json`).

Adapter: one **log** `EventEnvelope` per row.

### `inject_time.txt`  (GROUND TRUTH — never in events)
Single epoch-second int (e.g. `1705600751`). Loaded by the adapter ONLY to write
the eval-side sidecar `data/labels/{case_id}.json`; it never appears in any
pipeline-facing event. (Rule 4.)

### `pod-node-*.csv`
`POD,NODE_NAME`. Pod names carry k8s hashes
(`catalogue-db-554cbfd749-sbwkr`) and are canonicalized by
`normalize_component`. Used to seed topology nodes.

### `cluster_info.json` (Step 4)
`{ "<templateId>": { "template": "...<:PLACEHOLDER:>...", "container": [svc] } }`
— a Drain3-style template catalog, not topology. Unused by Step 2.

## Case identity & ground truth

- `case_id` = `"<service>_<faulttype>-<run>"`, e.g. `catalogue_cpu-1`.
- Folder name → `fault_service` (before last `_`, normalized) and `fault_type`
  (after last `_`, one of `cpu/mem/disk/delay/loss/socket`).
- These + `inject_time` are written to `data/labels/{case_id}.json` and read
  ONLY by `/eval` and `/scenarios`.

## Topology derivation — STATIC FALLBACK (recorded per requirement)

RE2-SS ships **no trace/call graph** (`cluster_info.json` is a log-template
catalog, not topology). The adapter therefore derives the service dependency
graph from the **known sock-shop call graph**, restricted to the components
actually present in the case. Edges used (`backend/ingest/re2ss_adapter.py:SOCK_SHOP_DEPS`):

```
loadgenerator -> front-end
front-end     -> catalogue, carts, orders, user, session-db
orders        -> orders-db, payment, shipping, user, carts
catalogue     -> catalogue-db
carts         -> carts-db
user          -> user-db
shipping      -> rabbitmq
queue-master  -> rabbitmq
```

Every component observed in events (plus pods from `pod-node-*.csv`) becomes a
node, so **every event's `component_id` is guaranteed to be a topology node**.
The observed `catalogue_cpu/1` case yields 16 nodes (15 pods + the
`rabbitmq-exporter` metric sidecar) and 16 edges.

## Observed volume (golden case `catalogue_cpu/1`)

| source | events |
| ------ | ------ |
| metric | 107,908 |
| log    | 78,035 |
| total  | 185,943 |

All `event_id`s are schema-valid and unique; all `component_id`s are topology
nodes.
