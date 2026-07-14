"""Config + alert overlay: synthetic config-change events and SNMP-style alerts
layered onto a case, fully deterministic per seed.

Ground truth (`innocent` per injected config, `synthetic` markers) is written
ONLY to the label sidecar `data/labels/{case_id}.json` — NEVER into the event
payloads served at runtime (AlertPayload/ConfigPayload have no such field, and
these models forbid extras).

    py -m backend.overlay.config_overlay data/re2_ss/catalogue_cpu --seed 42 --out data/parquet
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

from backend.ingest.normalize import make_event_id, normalize_component
from backend.models import (
    AlertPayload,
    ConfigPayload,
    EventEnvelope,
    LogPayload,
    MetricPayload,
    TopologyPayload,
)

_DEFAULT_LABELS_DIR = Path("data/labels")

# Fault types that could *plausibly* follow a config change (so a "plausible
# trigger" config is only injected for these).
PLAUSIBLE_CONFIG_FAULTS = frozenset({"cpu", "mem", "socket", "delay", "loss"})

# Innocent red-herring config templates (key, old, new). Some LOOK risky on
# purpose — the whole point of a red herring is to be tempting. Their
# innocence lives in the sidecar, not the payload.
INNOCENT_CONFIG_TEMPLATES: list[tuple[str, Any, Any]] = [
    ("log_level", "INFO", "DEBUG"),
    ("replicas", 3, 2),
    ("cache_ttl_s", 300, 600),
    ("feature_flag_beta", False, True),
    ("max_connections", 200, 150),
    ("gc_mode", "default", "aggressive"),
    ("request_timeout_s", 30, 60),
]

# Risky config templates used for "plausible trigger" injections.
RISKY_CONFIG_TEMPLATES: list[tuple[str, Any, Any]] = [
    ("max_connections", 200, 20),
    ("memory_limit_mb", 512, 128),
    ("cpu_quota", 100000, 20000),
    ("socket_timeout_ms", 5000, 100),
    ("thread_pool_size", 50, 4),
]

# Alert thresholds, tuned to the RE2-SS metric UNITS (cpu is a scaled value that
# tops ~50; latency is in SECONDS ~0.05-0.08; mem in bytes). The mechanism is
# "threshold a real metric -> firing alert"; scenario generators emit metrics in
# these same units so one table serves both.
ALERT_THRESHOLDS: dict[str, float] = {
    "cpu": 40.0,
    "mem": 4.0e8,
    "diskio": 2.0e6,
    "latency-90": 0.05,
    "latency-50": 0.05,
    "socket": 20.0,
    "workload": 25.0,
    "error": 5.0,
}
ALERT_NAME: dict[str, str] = {
    "cpu": "HighCPU",
    "mem": "HighMemory",
    "diskio": "HighDiskIO",
    "latency-90": "HighLatency",
    "latency-50": "HighLatency",
    "socket": "SocketExhaustion",
    "workload": "HighLoad",
    "error": "HighErrorRate",
}


class EventFactory:
    """Builds contract-valid EventEnvelopes with deterministic per-(source,
    component) sequence numbers. Seeded from any pre-existing events so injected
    ids never collide."""

    def __init__(self, case_id: str, existing_events: list[EventEnvelope] | None = None) -> None:
        self.case_id = case_id
        self._counters: dict[tuple[str, str], int] = defaultdict(int)
        self.events: list[EventEnvelope] = []
        for ev in existing_events or []:
            self._counters[(ev.source, ev.component_id)] += 1

    def _next(self, source: str, comp: str) -> int:
        seq = self._counters[(source, comp)]
        self._counters[(source, comp)] = seq + 1
        return seq

    def _envelope(self, source: str, comp: str, ts: float, payload) -> EventEnvelope:
        comp = normalize_component(comp)
        ev = EventEnvelope(
            event_id=make_event_id(source, comp, self._next(source, comp)),
            case_id=self.case_id,
            source=source,
            component_id=comp,
            ts=float(ts),
            payload=payload,
        )
        self.events.append(ev)
        return ev

    def metric(self, comp: str, name: str, value: float, ts: float, unit: str | None = None):
        return self._envelope("metric", comp, ts, MetricPayload(
            kind="metric", name=name, value=float(value), unit=unit))

    def log(self, comp: str, message: str, ts: float, level: str | None = None,
            template_id: str | None = None):
        return self._envelope("log", comp, ts, LogPayload(
            kind="log", level=level, message=message, template_id=template_id))

    def alert(self, comp: str, name: str, severity: float, ts: float, state: str = "firing"):
        return self._envelope("alert", comp, ts, AlertPayload(
            kind="alert", name=name, severity=float(severity), state=state))

    def config(self, comp: str, key: str, old: Any, new: Any, risky: bool, ts: float):
        return self._envelope("config", comp, ts, ConfigPayload(
            kind="config", key=key, old_value=old, new_value=new, risky=risky))

    def topology(self, comp: str, target: str, relation: str, ts: float):
        return self._envelope("topology", comp, ts, TopologyPayload(
            kind="topology", target_component_id=normalize_component(target), relation=relation))


def causal_and_offpath(topo: nx.DiGraph, fault_service: str | None) -> tuple[set[str], list[str]]:
    """Return (causal_set, offpath_components) for a fault at fault_service.

    causal = the faulty node plus everything up/downstream of it. Off-path =
    everything else (safe places for innocent red herrings)."""
    nodes = set(topo.nodes)
    if not fault_service or fault_service not in topo:
        return set(), sorted(nodes)
    causal = {fault_service} | nx.ancestors(topo, fault_service) | nx.descendants(topo, fault_service)
    offpath = sorted(nodes - causal)
    return causal, offpath


@dataclass
class InjectedConfig:
    event_id: str
    component_id: str
    innocent: bool
    ts: float

    def as_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "component_id": self.component_id,
                "innocent": self.innocent, "ts": self.ts}


def inject_config_events(
    factory: EventFactory,
    topo: nx.DiGraph,
    fault_service: str | None,
    fault_type: str | None,
    inject_time: float,
    rng: random.Random,
) -> list[InjectedConfig]:
    """Inject 3-6 config events: 70% innocent red herrings off the causal path,
    30s-120s BEFORE inject_time; 30% plausible triggers on the faulty component
    (only when the fault type could plausibly follow a change)."""
    causal, offpath = causal_and_offpath(topo, fault_service)
    n = rng.randint(3, 6)
    plausible = (fault_type in PLAUSIBLE_CONFIG_FAULTS) and bool(fault_service)
    n_plausible = round(0.3 * n) if plausible else 0
    n_innocent = n - n_plausible

    injected: list[InjectedConfig] = []
    herring_pool = offpath or sorted(set(topo.nodes) - {fault_service}) or [fault_service or "front-end"]

    for _ in range(n_innocent):
        comp = rng.choice(herring_pool)
        ts = inject_time - rng.uniform(30.0, 120.0)
        key, old, new = rng.choice(INNOCENT_CONFIG_TEMPLATES)
        risky = key in {"max_connections", "gc_mode"}  # some herrings look risky
        ev = factory.config(comp, key, old, new, risky, ts)
        injected.append(InjectedConfig(ev.event_id, ev.component_id, True, ts))

    for _ in range(n_plausible):
        comp = fault_service  # on the faulty component
        ts = inject_time - rng.uniform(30.0, 120.0)
        key, old, new = rng.choice(RISKY_CONFIG_TEMPLATES)
        ev = factory.config(comp, key, old, new, True, ts)
        injected.append(InjectedConfig(ev.event_id, ev.component_id, False, ts))

    return injected


def synth_alert_events(events: list[EventEnvelope], factory: EventFactory) -> list[EventEnvelope]:
    """SNMP-style: threshold real metric events -> one deduped firing alert per
    (component, metric) that crosses its threshold (at the first crossing)."""
    agg: dict[tuple[str, str], list[float]] = {}
    for ev in events:
        if ev.source != "metric":
            continue
        name = ev.payload.name
        thr = ALERT_THRESHOLDS.get(name)
        if thr is None or ev.payload.value <= thr:
            continue
        key = (ev.component_id, name)
        cur = agg.get(key)
        if cur is None:
            agg[key] = [ev.payload.value, ev.ts]
        else:
            cur[0] = max(cur[0], ev.payload.value)
            cur[1] = min(cur[1], ev.ts)

    out: list[EventEnvelope] = []
    for (comp, name) in sorted(agg):
        maxv, ts = agg[(comp, name)]
        thr = ALERT_THRESHOLDS[name]
        severity = min(1.0, 0.5 + 0.5 * min(1.0, (maxv - thr) / max(thr, 1e-9)))
        out.append(factory.alert(comp, ALERT_NAME.get(name, "HighMetric"), severity, ts))
    return out


# --------------------------- label sidecar I/O ---------------------------
def read_label(labels_dir: Path, case_id: str) -> dict[str, Any]:
    p = Path(labels_dir) / f"{case_id}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"case_id": case_id}


def write_label(labels_dir: Path, case_id: str, label: dict[str, Any]) -> Path:
    Path(labels_dir).mkdir(parents=True, exist_ok=True)
    out = Path(labels_dir) / f"{case_id}.json"
    out.write_text(json.dumps(label, indent=2), encoding="utf-8")
    return out


def apply(bundle, seed: int, labels_dir: str | Path = _DEFAULT_LABELS_DIR):
    """Inject deterministic config + alert overlay onto `bundle`; update the
    label sidecar with ground-truth innocence + synthetic markers. Returns the
    same bundle (events extended in place)."""
    rng = random.Random(seed)
    label = read_label(Path(labels_dir), bundle.case_id)
    fault_service = label.get("fault_service")
    fault_type = label.get("fault_type")
    inject_time = label.get("inject_time")
    if inject_time is None:
        metric_ts = [e.ts for e in bundle.events if e.source == "metric"]
        inject_time = min(metric_ts) + 300.0 if metric_ts else 0.0

    factory = EventFactory(bundle.case_id, existing_events=bundle.events)
    injected = inject_config_events(
        factory, bundle.topology, fault_service, fault_type, float(inject_time), rng
    )
    synth_alert_events(bundle.events, factory)

    bundle.events.extend(factory.events)

    synthetic_ids = sorted({e.event_id for e in factory.events} | set(label.get("synthetic_event_ids", [])))
    label["injected_configs"] = [c.as_dict() for c in injected]
    label["ground_truth_innocent"] = sorted(c.event_id for c in injected if c.innocent)
    label["synthetic_event_ids"] = synthetic_ids
    label.setdefault("synthetic", False)
    write_label(Path(labels_dir), bundle.case_id, label)
    return bundle


def _main(argv: list[str]) -> int:
    from backend.ingest.re2ss_adapter import load_case
    from backend.ingest.store import EventStore

    ap = argparse.ArgumentParser(prog="config_overlay")
    ap.add_argument("case_dir")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/parquet")
    ap.add_argument("--labels-dir", default=str(_DEFAULT_LABELS_DIR))
    args = ap.parse_args(argv[1:])

    bundle = load_case(args.case_dir, labels_dir=args.labels_dir)
    before = len(bundle.events)
    apply(bundle, args.seed, labels_dir=args.labels_dir)
    added = len(bundle.events) - before
    n_cfg = sum(1 for e in bundle.events if e.source == "config")
    n_alert = sum(1 for e in bundle.events if e.source == "alert")
    print(f"case_id={bundle.case_id} added={added} configs={n_cfg} alerts={n_alert}")
    store = EventStore(args.out)
    store.write_case(bundle)
    store.write_topology(bundle.case_id, bundle.topology)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
