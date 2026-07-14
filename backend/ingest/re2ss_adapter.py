"""RE2-SS case directory -> CaseBundle(events, topology, inject_time).

THE everything-blocker. Written against ``data/README.md`` (which was written
against the real output of ``backend.ingest.explore``), never against
hardcoded assumptions the explorer contradicts.

Ground truth (``inject_time`` + fault service/type parsed from the folder name)
is quarantined into an eval-side sidecar ``data/labels/{case_id}.json``. The
pipeline-facing CaseBundle events carry NO ground truth.

    py -m backend.ingest.re2ss_adapter data/re2_ss/catalogue_cpu --out data/parquet
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import polars as pl

from backend.ingest.normalize import make_event_id, normalize_component
from backend.models import EventEnvelope, LogPayload, MetricPayload

# ---------------------------------------------------------------------------
# Static sock-shop dependency map — the DOCUMENTED FALLBACK.
# RE2-SS ships no trace/call graph (cluster_info.json is a log-template catalog,
# not topology), so the adapter derives topology from this known sock-shop
# call graph, restricted to the components actually present in the case.
# See data/README.md ("Topology derivation").
# ---------------------------------------------------------------------------
SOCK_SHOP_DEPS: dict[str, list[str]] = {
    "loadgenerator": ["front-end"],
    "front-end": ["catalogue", "carts", "orders", "user", "session-db"],
    "orders": ["orders-db", "payment", "shipping", "user", "carts"],
    "catalogue": ["catalogue-db"],
    "carts": ["carts-db"],
    "user": ["user-db"],
    "shipping": ["rabbitmq"],
    "queue-master": ["rabbitmq"],
}

_FAULT_TYPES = {"cpu", "mem", "disk", "delay", "loss", "socket"}
_LOG_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR", "FATAL", "TRACE"}

# metric-name -> unit, for readability only (contract keeps `unit` optional).
_UNIT_BY_METRIC = {
    "cpu": "cores",
    "mem": "bytes",
    "diskio": "bytes/s",
    "socket": "count",
    "workload": "count",
    "error": "count",
    "latency-50": "ms",
    "latency-90": "ms",
}

_DEFAULT_LABELS_DIR = Path("data/labels")


@dataclass
class CaseBundle:
    """Pipeline-facing bundle. Contains NO ground truth in `events`."""

    case_id: str
    events: list[EventEnvelope] = field(default_factory=list)
    topology: nx.DiGraph = field(default_factory=nx.DiGraph)
    inject_time: float | None = None  # eval-only metadata; never inside events


# ---------------------------------------------------------------------------
# Path / label helpers
# ---------------------------------------------------------------------------
def _resolve_run_dir(case_dir: Path) -> Path:
    """Accept either a run dir (has simple_metrics.csv) or a case dir (has
    numeric run subdirs); return the run dir holding the CSVs."""
    if (case_dir / "simple_metrics.csv").exists():
        return case_dir
    subdirs = sorted(
        (p for p in case_dir.iterdir() if p.is_dir() and (p / "simple_metrics.csv").exists()),
        key=lambda p: (len(p.name), p.name),
    )
    if not subdirs:
        raise FileNotFoundError(f"no simple_metrics.csv under {case_dir} or its run subdirs")
    return subdirs[0]


def _parse_case_identity(run_dir: Path) -> tuple[str, str, str]:
    """Return (case_id, fault_service, fault_type) from the folder names.

    Folder convention: ``<service>_<faulttype>/<run>``.
    """
    case_name = run_dir.parent.name  # e.g. "catalogue_cpu"
    run = run_dir.name  # e.g. "1"
    if "_" in case_name:
        service_raw, fault_type = case_name.rsplit("_", 1)
    else:  # pragma: no cover - defensive
        service_raw, fault_type = case_name, "unknown"
    if fault_type not in _FAULT_TYPES:
        fault_type = "unknown"
    fault_service = normalize_component(service_raw)
    case_id = f"{case_name}-{run}"
    return case_id, fault_service, fault_type


def _read_inject_time(run_dir: Path) -> float | None:
    p = run_dir / "inject_time.txt"
    if not p.exists():
        return None
    txt = p.read_text(encoding="utf-8").strip()
    try:
        return float(txt)
    except ValueError:
        return None


def _write_label_sidecar(
    labels_dir: Path,
    case_id: str,
    fault_service: str,
    fault_type: str,
    inject_time: float | None,
    source_dir: Path,
) -> Path:
    """Quarantine ground truth into an eval-side sidecar (Rule 4)."""
    labels_dir.mkdir(parents=True, exist_ok=True)
    out = labels_dir / f"{case_id}.json"
    out.write_text(
        json.dumps(
            {
                "case_id": case_id,
                "fault_service": fault_service,
                "fault_type": fault_type,
                "inject_time": inject_time,
                "ground_truth_innocent": [],
                "source_dir": str(source_dir),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


# ---------------------------------------------------------------------------
# Metric + log ingestion
# ---------------------------------------------------------------------------
def _norm_map(raw_values: list[str]) -> dict[str, str]:
    """Normalize each unique raw component once (cheap; ~15 uniques)."""
    out: dict[str, str] = {}
    for r in raw_values:
        try:
            out[r] = normalize_component(r)
        except ValueError:
            continue  # drop un-normalizable series (e.g. stray columns)
    return out


def _metric_events(run_dir: Path, case_id: str, counters: dict[tuple, int]) -> list[EventEnvelope]:
    path = run_dir / "simple_metrics.csv"
    df = pl.read_csv(path, infer_schema_length=None)
    time_col = df.columns[0]
    value_cols = df.columns[1:]
    # robust numeric coercion (whole-empty columns can infer as str/Null)
    df = df.select(
        [pl.col(time_col).cast(pl.Float64, strict=False)]
        + [pl.col(c).cast(pl.Float64, strict=False) for c in value_cols]
    )
    long = (
        df.unpivot(index=time_col, on=value_cols, variable_name="series", value_name="value")
        .drop_nulls("value")
        # split "<component>_<metric>" on the FIRST underscore (components have none)
        .with_columns(pl.col("series").str.split_exact("_", 1).alias("parts"))
        .unnest("parts")
        .rename({"field_0": "comp_raw", "field_1": "metric"})
        .drop_nulls("metric")
    )

    comp_map = _norm_map(long["comp_raw"].unique().to_list())
    ts_list = long[time_col].to_list()
    craw_list = long["comp_raw"].to_list()
    metric_list = long["metric"].to_list()
    val_list = long["value"].to_list()

    events: list[EventEnvelope] = []
    for ts, craw, metric, val in zip(ts_list, craw_list, metric_list, val_list):
        comp = comp_map.get(craw)
        if comp is None:
            continue
        key = ("metric", comp)
        seq = counters[key]
        counters[key] = seq + 1
        events.append(
            EventEnvelope(
                event_id=make_event_id("metric", comp, seq),
                case_id=case_id,
                source="metric",
                component_id=comp,
                ts=float(ts),
                payload=MetricPayload(
                    kind="metric",
                    name=metric,
                    value=float(val),
                    unit=_UNIT_BY_METRIC.get(metric),
                ),
            )
        )
    return events


def _log_events(run_dir: Path, case_id: str, counters: dict[tuple, int]) -> list[EventEnvelope]:
    path = run_dir / "logs.csv"
    if not path.exists():
        return []
    df = pl.read_csv(path, infer_schema_length=None)
    cols = set(df.columns)
    have = lambda c: c in cols  # noqa: E731

    ns = df["timestamp"].to_list() if have("timestamp") else [None] * df.height
    containers = df["container_name"].to_list() if have("container_name") else [""] * df.height
    messages = df["message"].to_list() if have("message") else [""] * df.height
    levels = df["level"].to_list() if have("level") else [None] * df.height
    req_paths = df["req_path"].to_list() if have("req_path") else [None] * df.height
    errors = df["error"].to_list() if have("error") else [None] * df.height

    comp_map = _norm_map([c for c in set(containers) if c])

    events: list[EventEnvelope] = []
    for raw_ts, cont, msg, lvl, rp, err in zip(ns, containers, messages, levels, req_paths, errors):
        comp = comp_map.get(cont)
        if comp is None:
            continue
        ts = float(raw_ts) / 1e9 if raw_ts not in (None, "") else 0.0
        level = None
        if lvl not in (None, ""):
            up = str(lvl).strip().upper()
            level = up if up in _LOG_LEVELS else None
        key = ("log", comp)
        seq = counters[key]
        counters[key] = seq + 1
        events.append(
            EventEnvelope(
                event_id=make_event_id("log", comp, seq),
                case_id=case_id,
                source="log",
                component_id=comp,
                ts=ts,
                payload=LogPayload(
                    kind="log",
                    level=level,
                    message="" if msg is None else str(msg),
                    template=None,
                    template_id=None,  # Step 4 fills via Drain3
                    req_path=(str(rp) if rp not in (None, "") else None),
                    error=(str(err) if err not in (None, "") else None),
                ),
            )
        )
    return events


def _pod_components(run_dir: Path) -> set[str]:
    comps: set[str] = set()
    for name in ("pod-node-1.csv", "pod-node-2.csv"):
        p = run_dir / name
        if not p.exists():
            continue
        try:
            df = pl.read_csv(p)
        except Exception:  # pragma: no cover - malformed csv
            continue
        col = "POD" if "POD" in df.columns else df.columns[0]
        for pod in df[col].to_list():
            try:
                comps.add(normalize_component(str(pod)))
            except ValueError:
                continue
    return comps


def build_topology(components: set[str]) -> nx.DiGraph:
    """Static sock-shop dependency graph restricted to present components.

    Guarantees every present component is a node (so every event's
    component_id is in the topology).
    """
    g = nx.DiGraph()
    g.add_nodes_from(sorted(components))
    for src, dsts in SOCK_SHOP_DEPS.items():
        if src not in components:
            continue
        for dst in dsts:
            if dst in components:
                g.add_edge(src, dst, relation="calls")
    return g


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def load_case(
    case_dir: str | Path,
    *,
    write_labels: bool = True,
    labels_dir: str | Path | None = _DEFAULT_LABELS_DIR,
) -> CaseBundle:
    """Load a RE2-SS case into a CaseBundle. Writes the eval-side label sidecar."""
    run_dir = _resolve_run_dir(Path(case_dir))
    case_id, fault_service, fault_type = _parse_case_identity(run_dir)
    inject_time = _read_inject_time(run_dir)

    counters: dict[tuple, int] = defaultdict(int)
    events = _metric_events(run_dir, case_id, counters)
    events += _log_events(run_dir, case_id, counters)

    components = {ev.component_id for ev in events} | _pod_components(run_dir)
    topology = build_topology(components)

    if write_labels and labels_dir is not None:
        _write_label_sidecar(
            Path(labels_dir), case_id, fault_service, fault_type, inject_time, run_dir
        )

    return CaseBundle(
        case_id=case_id, events=events, topology=topology, inject_time=inject_time
    )


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="re2ss_adapter")
    ap.add_argument("case_dir")
    ap.add_argument("--out", default="data/parquet", help="EventStore root")
    ap.add_argument("--labels-dir", default=str(_DEFAULT_LABELS_DIR))
    ap.add_argument("--no-store", action="store_true", help="load only, do not write parquet")
    args = ap.parse_args(argv[1:])

    bundle = load_case(args.case_dir, labels_dir=args.labels_dir)
    print(f"case_id       : {bundle.case_id}")
    print(f"events        : {len(bundle.events)}")
    by_source: dict[str, int] = defaultdict(int)
    for ev in bundle.events:
        by_source[ev.source] += 1
    print(f"  by source   : {dict(by_source)}")
    print(f"topology      : {bundle.topology.number_of_nodes()} nodes, "
          f"{bundle.topology.number_of_edges()} edges")
    print(f"inject_time   : {bundle.inject_time}  (quarantined to sidecar)")

    if not args.no_store:
        from backend.ingest.store import EventStore

        store = EventStore(args.out)
        n = store.write_case(bundle)
        store.write_topology(bundle.case_id, bundle.topology)
        print(f"wrote         : {n} rows (+ topology.json) -> {store.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
