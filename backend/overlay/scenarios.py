"""Fully-synthetic scenario generators (eval/scenario-side).

Seven scenario types, parameterized to >=25 labeled variants (see
`scenarios/registry.json`). Each generator is deterministic per seed and emits
events through the SAME EventEnvelope + EventStore paths as real cases; the only
"synthetic" marker lives in the label sidecar / cases metadata, never in a
payload.

    py -m backend.overlay.scenarios --build-all --seed 42 --out data/parquet
    py -m backend.overlay.scenarios --variant clean_cascade-01 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable

import networkx as nx
from pydantic import BaseModel, ConfigDict

from backend.ingest.re2ss_adapter import SOCK_SHOP_DEPS, CaseBundle, build_topology
from backend.ingest.store import EventStore
from backend.overlay.config_overlay import (
    EventFactory,
    inject_config_events,
    synth_alert_events,
    write_label,
)

_DEFAULT_LABELS_DIR = Path("data/labels")
_REGISTRY = Path(__file__).resolve().parents[2] / "scenarios" / "registry.json"

SCENARIO_T0 = 1_700_000_000.0
INJECT_OFFSET = 300.0
STEPS = 24
STEP_DT = 15.0

CANON_COMPONENTS = sorted(
    set(SOCK_SHOP_DEPS) | {d for v in SOCK_SHOP_DEPS.values() for d in v}
)

_BASE = {"cpu": 10.0, "latency-90": 0.03, "mem": 2.0e8, "diskio": 5.0e5,
         "socket": 8.0, "error": 0.0, "workload": 12.0}
# fault_type -> (metric_name, faulting_value, unit)
FAULT_METRIC = {
    "cpu": ("cpu", 95.0, "ratio"),
    "mem": ("mem", 5.0e8, "bytes"),
    "disk": ("diskio", 5.0e6, "bytes/s"),
    "delay": ("latency-90", 0.7, "s"),
    "loss": ("error", 40.0, "count"),
    "socket": ("socket", 120.0, "count"),
}


class LabelFile(BaseModel):
    """Eval-side ground-truth label for a scenario variant."""

    model_config = ConfigDict(extra="allow")

    case_id: str
    scenario_type: str
    synthetic: bool = True
    fault_service: str | None = None
    fault_type: str | None = None
    inject_time: float | None = None
    expected_root_cause: str | None = None
    expected_tier: str | None = None
    expected_top1_innocent_config: bool | None = None
    ground_truth_innocent: list[str] = []
    injected_configs: list[dict] = []
    synthetic_event_ids: list[str] = []
    notes: str = ""


# --------------------------- shared helpers ---------------------------
def _new_topology(uninstrumented: tuple[str, ...] = ()) -> nx.DiGraph:
    topo = build_topology(set(CANON_COMPONENTS))
    for n in topo.nodes:
        topo.nodes[n]["instrumented"] = True
    for n in uninstrumented:
        if n in topo:
            topo.nodes[n]["instrumented"] = False
    return topo


def _instrumented(topo: nx.DiGraph) -> list[str]:
    return [n for n in sorted(topo.nodes) if topo.nodes[n].get("instrumented", True)]


def _cascade(topo: nx.DiGraph, fault_service: str | None) -> set[str]:
    if not fault_service or fault_service not in topo:
        return set()
    return {fault_service} | nx.ancestors(topo, fault_service)


def _emit_baseline(
    factory: EventFactory,
    topo: nx.DiGraph,
    inject_time: float,
    fault_service: str | None,
    fault_type: str | None,
    rng: random.Random,
    steps: int = STEPS,
    dt: float = STEP_DT,
) -> None:
    """cpu + latency-90 for every instrumented node across a window; latency
    elevated on the cascade (faulty node + its callers) after inject; the
    fault-specific metric elevated on the faulty node after inject."""
    cascade = _cascade(topo, fault_service)
    fm = FAULT_METRIC.get(fault_type) if fault_type else None
    start = inject_time - (steps // 2) * dt
    for step in range(steps):
        ts = start + step * dt
        faulting = ts >= inject_time
        for node in _instrumented(topo):
            j = rng.uniform(-1.5, 1.5)
            cpu = (95.0 + j) if (node == fault_service and faulting and fault_type == "cpu") \
                else (_BASE["cpu"] + j)
            factory.metric(node, "cpu", cpu, ts, unit="ratio")
            lat = (0.6 + rng.uniform(-0.05, 0.05)) if (node in cascade and faulting) \
                else (_BASE["latency-90"] + rng.uniform(-0.005, 0.005))
            factory.metric(node, "latency-90", lat, ts, unit="s")
        # fault-specific metric on the faulty node (skip if cpu/latency already emitted)
        if fm and fault_service and topo.nodes.get(fault_service, {}).get("instrumented", True):
            name, hi, unit = fm
            if name not in ("cpu", "latency-90"):
                val = (hi + rng.uniform(-hi * 0.03, hi * 0.03)) if faulting else _BASE.get(name, 0.0)
                factory.metric(fault_service, name, val, ts, unit=unit)


def _emit_fault_logs(factory: EventFactory, comp: str, inject_time: float, n: int = 5) -> None:
    for i in range(n):
        factory.log(comp, f"{comp} request failed: upstream error (attempt {i})",
                    inject_time + i * 3.0, level=None, template_id=None)


def _label(bundle: CaseBundle, scenario_type: str, **kw: Any) -> LabelFile:
    return LabelFile(
        case_id=bundle.case_id,
        scenario_type=scenario_type,
        inject_time=bundle.inject_time,
        synthetic_event_ids=[e.event_id for e in bundle.events],
        **kw,
    )


# --------------------------- the 7 generators ---------------------------
def gen_clean_cascade(case_id: str, seed: int, *, fault_service: str, fault_type: str):
    rng = random.Random(seed)
    topo = _new_topology()
    inject = SCENARIO_T0 + INJECT_OFFSET
    f = EventFactory(case_id)
    _emit_baseline(f, topo, inject, fault_service, fault_type, rng)
    _emit_fault_logs(f, fault_service, inject)
    bundle = CaseBundle(case_id=case_id, events=f.events, topology=topo, inject_time=inject)
    label = _label(bundle, "clean_cascade", fault_service=fault_service, fault_type=fault_type,
                   expected_root_cause=fault_service, expected_tier="CONFIRMED",
                   expected_top1_innocent_config=None,
                   notes=f"Clean {fault_type} fault on {fault_service} cascading upstream.")
    return bundle, label


def gen_red_herring_config(case_id: str, seed: int, *, fault_service: str, fault_type: str):
    rng = random.Random(seed)
    topo = _new_topology()
    inject = SCENARIO_T0 + INJECT_OFFSET
    f = EventFactory(case_id)
    _emit_baseline(f, topo, inject, fault_service, fault_type, rng)
    _emit_fault_logs(f, fault_service, inject)
    injected = inject_config_events(f, topo, fault_service, fault_type, inject, rng)
    synth_alert_events(f.events, f)
    bundle = CaseBundle(case_id=case_id, events=f.events, topology=topo, inject_time=inject)
    label = _label(
        bundle, "red_herring_config", fault_service=fault_service, fault_type=fault_type,
        expected_root_cause=fault_service, expected_tier="CONFIRMED",
        expected_top1_innocent_config=True,
        ground_truth_innocent=sorted(c.event_id for c in injected if c.innocent),
        injected_configs=[c.as_dict() for c in injected],
        notes="Real fault plus innocent config red herrings placed 30-120s before inject.",
    )
    return bundle, label


def gen_alert_storm(case_id: str, seed: int, *, fault_service: str, fault_type: str, size: int = 160):
    rng = random.Random(seed)
    topo = _new_topology()
    inject = SCENARIO_T0 + INJECT_OFFSET
    f = EventFactory(case_id)
    _emit_baseline(f, topo, inject, fault_service, fault_type, rng)
    nodes = _instrumented(topo)
    names = ["HighCPU", "HighMemory", "HighLatency", "HighDiskIO", "SocketExhaustion", "HighErrorRate"]
    for i in range(max(size, 150)):
        comp = nodes[i % len(nodes)]
        name = names[i % len(names)]
        ts = inject + (i % 40) * 2.0
        f.alert(comp, name, round(0.5 + 0.5 * rng.random(), 3), ts)
    bundle = CaseBundle(case_id=case_id, events=f.events, topology=topo, inject_time=inject)
    n_alerts = sum(1 for e in bundle.events if e.source == "alert")
    label = _label(bundle, "alert_storm", fault_service=fault_service, fault_type=fault_type,
                   expected_root_cause=fault_service, expected_tier="CORRELATED",
                   expected_top1_innocent_config=None,
                   notes=f"Alert storm of {n_alerts} alerts burying a {fault_type} fault on {fault_service}.")
    return bundle, label


def gen_confounded_pair(case_id: str, seed: int, *, upstream: str, downstream: str):
    rng = random.Random(seed)
    topo = _new_topology()
    inject = SCENARIO_T0 + INJECT_OFFSET
    f = EventFactory(case_id)
    # upstream is the true root (cpu fault); downstream also looks hot (confounder)
    _emit_baseline(f, topo, inject, upstream, "cpu", rng)
    if topo.nodes.get(downstream, {}).get("instrumented", True):
        for step in range(STEPS):
            ts = inject - (STEPS // 2) * STEP_DT + step * STEP_DT
            if ts >= inject:
                f.metric(downstream, "cpu", 80.0 + rng.uniform(-3, 3), ts, unit="ratio")
    bundle = CaseBundle(case_id=case_id, events=f.events, topology=topo, inject_time=inject)
    label = _label(bundle, "confounded_pair", fault_service=upstream, fault_type="cpu",
                   expected_root_cause=upstream, expected_tier="CORRELATED",
                   expected_top1_innocent_config=None,
                   notes=f"{upstream} is root; {downstream} co-fires as a confounder.")
    return bundle, label


def gen_missing_telemetry(case_id: str, seed: int, *, fault_service: str, fault_type: str):
    rng = random.Random(seed)
    # the faulty component is uninstrumented: no telemetry emitted for it
    topo = _new_topology(uninstrumented=(fault_service,))
    inject = SCENARIO_T0 + INJECT_OFFSET
    f = EventFactory(case_id)
    _emit_baseline(f, topo, inject, fault_service, fault_type, rng)
    bundle = CaseBundle(case_id=case_id, events=f.events, topology=topo, inject_time=inject)
    label = _label(bundle, "missing_telemetry", fault_service=fault_service, fault_type=fault_type,
                   expected_root_cause=fault_service, expected_tier="MISSING_EVIDENCE",
                   expected_top1_innocent_config=None,
                   notes=f"{fault_service} is instrumented:false; only upstream symptoms observable.")
    return bundle, label


def gen_topology_drift(case_id: str, seed: int, *, fault_service: str, fault_type: str):
    rng = random.Random(seed)
    topo = _new_topology()
    inject = SCENARIO_T0 + INJECT_OFFSET
    f = EventFactory(case_id)
    _emit_baseline(f, topo, inject, fault_service, fault_type, rng)
    # topology change events mid-case: front-end re-routes + fault_service gains a dep
    f.topology("front-end", "shipping", "calls", inject)
    f.topology(fault_service, "session-db", "reads_from", inject + STEP_DT)
    if topo.has_edge("orders", "carts"):
        f.topology("orders", "carts", "calls", inject + 2 * STEP_DT)
    bundle = CaseBundle(case_id=case_id, events=f.events, topology=topo, inject_time=inject)
    label = _label(bundle, "topology_drift", fault_service=fault_service, fault_type=fault_type,
                   expected_root_cause=fault_service, expected_tier="CORRELATED",
                   expected_top1_innocent_config=None,
                   notes=f"Mid-case topology drift alongside a {fault_type} fault on {fault_service}.")
    return bundle, label


def gen_ambiguous(case_id: str, seed: int, *, tag: str = "a"):
    rng = random.Random(seed)
    topo = _new_topology()
    inject = SCENARIO_T0 + INJECT_OFFSET
    f = EventFactory(case_id)
    # no dominant fault: pure baseline plus small, scattered, contradictory bumps
    _emit_baseline(f, topo, inject, None, None, rng)
    for comp in rng.sample(_instrumented(topo), 3):
        f.metric(comp, "cpu", 25.0 + rng.uniform(-5, 5), inject + rng.uniform(0, 60), unit="ratio")
    bundle = CaseBundle(case_id=case_id, events=f.events, topology=topo, inject_time=inject)
    label = _label(bundle, "ambiguous", fault_service=None, fault_type=None,
                   expected_root_cause=None, expected_tier="MISSING_EVIDENCE",
                   expected_top1_innocent_config=None,
                   notes=f"Ambiguous variant {tag}: weak, contradictory evidence; correct answer is 'I don't know'.")
    return bundle, label


_GENERATORS: dict[str, Callable[..., tuple[CaseBundle, LabelFile]]] = {
    "clean_cascade": gen_clean_cascade,
    "red_herring_config": gen_red_herring_config,
    "alert_storm": gen_alert_storm,
    "confounded_pair": gen_confounded_pair,
    "missing_telemetry": gen_missing_telemetry,
    "topology_drift": gen_topology_drift,
    "ambiguous": gen_ambiguous,
}


# --------------------------- registry / build ---------------------------
def load_registry(path: Path = _REGISTRY) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def effective_seed(base_seed: int, seed_key: int) -> int:
    return base_seed * 100_000 + seed_key


def build_variant(variant: dict[str, Any], base_seed: int) -> tuple[CaseBundle, LabelFile]:
    gen = _GENERATORS[variant["scenario_type"]]
    seed = effective_seed(base_seed, variant["seed_key"])
    return gen(variant["variant_id"], seed, **variant.get("params", {}))


def build_all(
    base_seed: int,
    out: str | Path,
    labels_dir: str | Path = _DEFAULT_LABELS_DIR,
    registry_path: Path = _REGISTRY,
) -> list[tuple[CaseBundle, LabelFile]]:
    reg = load_registry(registry_path)
    store = EventStore(out)
    results: list[tuple[CaseBundle, LabelFile]] = []
    for variant in reg["variants"]:
        bundle, label = build_variant(variant, base_seed)
        store.write_case(bundle)
        write_label(Path(labels_dir), bundle.case_id, label.model_dump())
        results.append((bundle, label))
    return results


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="scenarios")
    ap.add_argument("--build-all", action="store_true")
    ap.add_argument("--variant", default=None, help="build a single variant_id")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/parquet")
    ap.add_argument("--labels-dir", default=str(_DEFAULT_LABELS_DIR))
    args = ap.parse_args(argv[1:])

    if args.variant:
        reg = load_registry()
        variant = next((v for v in reg["variants"] if v["variant_id"] == args.variant), None)
        if variant is None:
            print(f"unknown variant {args.variant!r}", file=sys.stderr)
            return 1
        bundle, label = build_variant(variant, args.seed)
        EventStore(args.out).write_case(bundle)
        write_label(Path(args.labels_dir), bundle.case_id, label.model_dump())
        print(f"built {bundle.case_id}: {len(bundle.events)} events "
              f"(expected_root_cause={label.expected_root_cause}, tier={label.expected_tier})")
        return 0

    if args.build_all:
        results = build_all(args.seed, args.out, args.labels_dir)
        total = sum(len(b.events) for b, _ in results)
        by_type: dict[str, int] = {}
        for _, label in results:
            by_type[label.scenario_type] = by_type.get(label.scenario_type, 0) + 1
        print(f"built {len(results)} variants, {total} events -> {args.out}")
        print(f"by type: {by_type}")
        print(f"labels -> {args.labels_dir}")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
