"""Twin runner: build + calibrate the twin, run seeded repetitions under a wall
budget, compare against the real signature, and hand the verdict to the tiers.

    py -m backend.twin.runner --case catalogue_cpu-1 --component catalogue --fault cpu
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import networkx as nx

from backend.ingest.store import EventStore
from backend.twin.compare import compare
from backend.twin.faults import inject
from backend.twin.model import FEATURES, Calibration, TwinModel

_DEFAULT_STORE = "data/parquet"
_SIM_FAULT_AT = 8.0
_SIM_END = 24.0
_REPS = 3
_WALL_BUDGET_S = 30.0

# real metric name -> FEATURES index
_METRIC_TO_FEATURE = {"latency-50": 0, "latency-90": 1, "error": 2, "workload": 3, "cpu": 4}


def _calibrate(store: EventStore, case_id: str, topology: nx.DiGraph) -> Calibration:
    """Base service time from pre-incident latency means; arrival rate from
    pre-incident workload at the entry."""
    df = store.events(case_id, source="metric")
    cal = Calibration()
    if df.height == 0:
        return cal
    t0 = float(df["ts"].min())
    bend = t0 + 0.3 * (float(df["ts"].max()) - t0)
    base = df.filter(df["ts"] <= bend)
    for key, sub in base.partition_by(["component_id", "metric_name"], as_dict=True,
                                      include_key=True).items():
        comp, metric = key
        mean = sub["value"].mean()
        if mean is None:
            continue
        if metric in ("latency-50", "latency-90"):
            cal.base_service[comp] = max(0.002, min(0.2, float(mean) or 0.015))
        if metric == "workload" and comp in ("front-end", "loadgenerator"):
            cal.arrival_rate = max(5.0, min(200.0, float(mean) or 30.0))
    return cal


def _avg_sim_deltas(topology, cal, component, fault_type, reps, budget, seed):
    start = time.monotonic()
    acc: dict[str, list[float]] = {}
    n = 0
    for r in range(reps):
        if time.monotonic() - start > budget:
            break
        model = TwinModel(topology, calibration=cal, seed=seed + r)
        model.run(_SIM_END, hooks=[(_SIM_FAULT_AT, lambda m: inject(m, fault_type, component))])
        for c in model.components():
            b = model.aggregate(c, 0.0, _SIM_FAULT_AT)
            f = model.aggregate(c, _SIM_FAULT_AT + 2.0, _SIM_END)
            acc.setdefault(c, [0.0] * len(FEATURES))
            for i in range(len(FEATURES)):
                acc[c][i] += f[i] - b[i]
        n += 1
    n = max(1, n)
    return {c: [v / n for v in vec] for c, vec in acc.items()}


def _real_deltas(store: EventStore, case_id: str):
    df = store.events(case_id, source="metric")
    if df.height == 0:
        return {}, set()
    t0 = float(df["ts"].min())
    bend = t0 + 0.3 * (float(df["ts"].max()) - t0)
    deltas: dict[str, list[float]] = {}
    instrumented: set[str] = set()
    for key, sub in df.partition_by(["component_id", "metric_name"], as_dict=True,
                                    include_key=True).items():
        comp, metric = key
        f = _METRIC_TO_FEATURE.get(metric)
        if f is None:
            continue
        instrumented.add(comp)
        base = sub.filter(sub["ts"] <= bend)["value"].mean() or 0.0
        late = sub.filter(sub["ts"] > bend)["value"].mean() or 0.0
        deltas.setdefault(comp, [0.0] * len(FEATURES))[f] = float(late) - float(base)
    return deltas, instrumented


def twin(
    case_id: str,
    component: str,
    fault_type: str,
    *,
    store_root: str | Path = _DEFAULT_STORE,
    ledger=None,
    reps: int = _REPS,
    wall_budget_s: float = _WALL_BUDGET_S,
    seed: int = 0,
) -> dict:
    """Run the twin for (component, fault_type) and return the twin block
    {run, similarity, verdict, missing_evidence}. Files a twin_result fact if a
    ledger is provided."""
    store = EventStore(store_root)
    topology = store.load_topology(case_id)
    if topology is None:
        return {"run": f"twin-{component}-{fault_type}", "similarity": 0.0,
                "verdict": "mismatch", "missing_evidence": []}

    cal = _calibrate(store, case_id, topology)
    sim_deltas = _avg_sim_deltas(topology, cal, component, fault_type, reps, wall_budget_s, seed)
    real_deltas, instrumented = _real_deltas(store, case_id)
    cmp = compare(sim_deltas, real_deltas, instrumented)

    block = {
        "run": f"twin-{component}-{fault_type}",
        "similarity": cmp["similarity"],
        "verdict": cmp["verdict"],
        "missing_evidence": cmp["missing_evidence"],
    }
    if ledger is not None:
        ledger.twin_result(
            statement=f"Twin {component}/{fault_type}: verdict={cmp['verdict']}, "
                      f"similarity={cmp['similarity']}, shared={cmp['shared']}.",
            component_ids=[component], ts_range=(0.0, 0.0), hypothesis_id=None,
        )
    return block


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="twin.runner")
    ap.add_argument("--case", required=True)
    ap.add_argument("--component", default=None)
    ap.add_argument("--fault", default="cpu")
    ap.add_argument("--store", default=_DEFAULT_STORE)
    args = ap.parse_args(argv[1:])

    store = EventStore(args.store)
    topo = store.load_topology(args.case)
    if topo is None:
        print(f"no topology for {args.case!r}", file=sys.stderr)
        return 1
    comp = args.component or ("catalogue" if "catalogue" in topo else next(iter(topo.nodes)))
    block = twin(args.case, comp, args.fault, store_root=args.store)
    print(f"case={args.case} component={comp} fault={args.fault}")
    print(f"  run={block['run']}")
    print(f"  similarity={block['similarity']}  verdict={block['verdict']}")
    print(f"  missing_evidence={block['missing_evidence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
