# RE2-SS dataset layout

VERDICT's primary dataset is **RE2-SS** (Sock-Shop micro-service fault-injection
benchmark). Unzip it to `data/re2_ss/` (git-ignored). This note is the reference
for the STEP 2 ingest layer; nothing here reads ground truth at pipeline runtime
(see Rule 4 — `inject_time`, fault service, and innocent labels are read ONLY in
`/eval` and `/scenarios`).

## Directory structure

```
RE2-SS/
  <service>_<faulttype>/        # 30 cases = 5 services x 6 fault types
    1/ 2/ 3/                     # 3 runs per case
      inject_time.txt           # GROUND TRUTH: single epoch-second int
      metrics.csv               # wide, 444 cols: time + <comp>_<prom-metric>
      simple_metrics.csv        # wide, curated: time + <comp>_<signal>
      logs.csv                  # raw logs (see schema below)
      logts.csv                 # wide: time + <comp>_<templateId> counts
      cluster_info.json         # Drain3 log-template catalog
      pod-node-1.csv            # POD -> NODE_NAME (before)
      pod-node-2.csv            # POD -> NODE_NAME (after)
      metrics_postprocess.log   # collection log (ignored)
```

### Ground truth (labels only — never read by pipeline code)
- **Fault service + fault type** are encoded in the case directory name,
  e.g. `payment_mem` -> service `payment`, fault `mem`.
- **`inject_time.txt`** — the epoch second the fault was injected.

## Fault types  ->  `fault_type_guess` enum

| dir suffix | fault_type_guess | primary metric signal |
| ---------- | ---------------- | --------------------- |
| `cpu`      | `cpu`            | `<comp>_cpu`          |
| `mem`      | `mem`            | `<comp>_mem`          |
| `disk`     | `disk`           | `<comp>_diskio`       |
| `delay`    | `delay`          | `<comp>_latency-50/90`|
| `loss`     | `loss`           | `<comp>_error`, latency |
| `socket`   | `socket`         | `<comp>_socket`       |

`config_push` and `unknown` are contract values that RE2-SS does not exercise.

## Components (15 pods)

`carts, carts-db, catalogue, catalogue-db, front-end, loadgenerator, orders,
orders-db, payment, queue-master, rabbitmq, session-db, shipping, user, user-db`
(plus a `rabbitmq-exporter` metric column — a sidecar, not a pod).

Pod names carry k8s hashes (`catalogue-db-554cbfd749-sbwkr`). **All are
canonicalized by `normalize_component()`** — see `tests/test_normalize.py`.

## File schemas

### `simple_metrics.csv` (wide)
`time` (epoch s) + one column per `<component>_<signal>` where signal ∈
`{cpu, mem, diskio, socket, workload, error, latency-50, latency-90}`.
Not every component has every signal (blank cells = not collected).
Parse component/signal by splitting each column on its **first** `_` (component
tokens never contain `_`).

### `logs.csv`
Columns: `time` (HH:MM), `timestamp` (epoch **ns**), `container_name`, `message`,
`level`, `req_path`, `error`.
**`level` is empty for essentially all rows** — this is why `LogPayload.level`
is optional/nullable in the contract. Map `container_name` through
`normalize_component()`; map `timestamp` (ns) to the envelope `ts` (epoch s).

### `logts.csv` (wide)
`time` (epoch s) + one column per `<component>_<templateId>` holding the count
of that log template in the bucket. This is the substrate for the
`log_freq_spike` and `log_rare_template` detectors.

### `cluster_info.json`
`{ "<templateId>": { "template": "<mined template w/ <:PLACEHOLDER:> tokens>",
"container": ["<service>", ...] } }` — a Drain3-style template catalog. The
`template`/`template_id` map onto `LogPayload.template` / `LogPayload.template_id`.

## What RE2-SS does NOT provide
- **No alert stream** (`alert` source / `alert_dedup` method) — contract-supported,
  synthesized elsewhere.
- **No config-change stream** (`config` source / `config_risky_flag` method,
  `config_push` fault) — contract-supported, not in RE2-SS.
- **No explicit topology** — the sock-shop call graph is static domain knowledge
  (`fixtures/sample_topology.json`), not shipped with the dataset.
