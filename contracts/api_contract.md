# VERDICT API Contract (v0.1, frozen)

All bodies are JSON unless noted. IDs follow the regexes frozen in the four
schema files in this directory. `component_id` values are ONLY ever produced by
`backend/ingest/normalize.py:normalize_component()`. Ground-truth fields are
never exposed by any endpoint.

## REST endpoints

| Method | Path | Body | Response |
| ------ | ---- | ---- | -------- |
| GET | `/cases` | — | `200` `[{case_id, title, n_components, n_events}]` |
| GET | `/case/{id}/topology` | — | `200` NetworkX node-link graph (`{directed, multigraph, graph, nodes, links}`) |
| POST | `/case/{id}/run` | `{speed: float, seed: int, twin_enabled: bool}` | `202` `{run_id, stream}` — `stream` is the SSE URL |
| GET | `/run/{id}/verdict` | — | `200` `{run_id, case_id, hypotheses: RankedHypothesis[], done: bool}` |
| GET | `/run/{id}/anomalies` | — | `200` `AnomalyEvent[]` |
| GET | `/run/{id}/ledger` | query: `component_id?`, `kind?`, `hypothesis_id?` | `200` `LedgerRecord[]` (filtered) |
| GET | `/run/{id}/narration` | — | `200` `{run_id, chunks: [{ts, text}]}` |
| GET | `/run/{id}/report.pdf` | — | `200` `application/pdf` |
| POST | `/run/{id}/counterfactual` | `{remove_component: component_id}` | `200` `{removed, anomalies_still_explained_pct, affected_hypotheses}` |
| GET | `/benchmark` | — | `200` `{runs: [...], metrics: {...}}` |
| GET | `/health` | — | `200` `{status: "ok", version}` |

### Status / error semantics
- `POST /case/{id}/run` returns `202 Accepted` with `{run_id, stream}`; work proceeds asynchronously and is observed over SSE.
- Unknown `case_id` / `run_id` → `404 {error}`.
- Malformed body → `422 {error, detail}`.
- `remove_component` must be a valid `component_id` present in the case topology, else `422`.

## SSE event stream (`text/event-stream`)

Emitted by the run stream in causal order. Each `data:` payload is a JSON object.
`pipeline_done` is ALWAYS the last event of a successful run; `pipeline_error`
terminates a failed run.

| SSE `event:` | `data` payload | Notes |
| ------------ | -------------- | ----- |
| `event_ingested` | `EventEnvelope` | one per normalized event |
| `anomaly_detected` | `AnomalyEvent` | one per detected anomaly |
| `blast_radius` | `{component_id, radius, affected: component_id[]}` | topology impact set |
| `hypothesis_ranked` | `RankedHypothesis` | **full-object upsert keyed by `hypothesis_id`** (re-emit replaces prior) |
| `counterfactual_result` | `{hypothesis_id, removed, anomalies_still_explained_pct}` | |
| `twin_started` | `{hypothesis_id, run}` | |
| `twin_result` | `{hypothesis_id, run, similarity, verdict, missing_evidence}` | |
| `challenger_attack` | `{hypothesis_id, claim, contradicting_event_id, upheld}` | |
| `narration_chunk` | `{ts, text}` | streamed narration token/chunk |
| `tier_changed` | `{hypothesis_id, tier, tier_reason}` | **emitted only by the ranking stage** |
| `pipeline_done` | `{run_id, n_hypotheses}` | ALWAYS last on success |
| `pipeline_error` | `{run_id, stage, error}` | terminates a failed run |

### Ordering guarantees
1. `event_ingested` precedes any `anomaly_detected` that cites it.
2. `hypothesis_ranked` upserts are idempotent by `hypothesis_id`; the latest wins.
3. `tier_changed` is emitted ONLY by the ranking stage (tiers are assigned only in `backend/rank/tiers.py`).
4. `pipeline_done` (success) or `pipeline_error` (failure) is the terminal event.
