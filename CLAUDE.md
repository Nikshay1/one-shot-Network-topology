You are building VERDICT, a network-anomaly root-cause assistant.

Rules that apply to every task:

1.  Python 3.11+, type hints everywhere, pydantic v2 models for every data shape.
2.  component_id may ONLY be produced by backend/ingest/normalize.py:normalize_component(). Never write ad-hoc string cleanup.
3.  Every event/anomaly/hypothesis/fact ID must match its schema regex in /contracts.
4.  Ground-truth fields (fault_service, inject_time, ground_truth_innocent) may only be read inside /eval and /scenarios label files — never by pipeline code at runtime.
5.  Evidence tiers (CONFIRMED/CORRELATED/MISSING_EVIDENCE) are assigned ONLY in backend/rank/tiers.py.
6.  No agent frameworks, no Kafka, no Docker, no deep learning. DuckDB+Parquet for storage, NetworkX for graphs.
7.  Every module must be runnable/testable via CLI without the API server.
8.  After implementing, run the VERIFY block and fix until green.
9.  Agents may only mutate system state through file_finding() (ledger append). All other tools are read-only or sandboxed (twin/counterfactual run on copies).
10. Agent budgets are enforced by the harness in code (call counters, cost points, wall-clock timeout) — never by prompt text alone.
11. Any agent error, timeout, or budget exhaustion triggers the deterministic autopilot (fixed pipeline) — the run must still complete with a valid verdict.
12. Final scores and tiers are ALWAYS computed by backend/rank/scorer.py and backend/rank/tiers.py from the ledger — no agent output is ever the verdict.
13. Every agent run writes a transcript (JSONL of steps) that is cached for OFFLINE demo replay, same as narrator responses.
