"""Adapter + store tests.

A fast synthetic RE2-SS-shaped case exercises all adapter logic without the
245 MB dataset; a real-data integration test runs the golden-case assertions
when ``data/re2_ss/catalogue_cpu`` is present (skipped otherwise).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.ingest.normalize import EVENT_ID_RE
from backend.ingest.re2ss_adapter import CaseBundle, load_case
from backend.ingest.store import EventStore
from backend.models import EventEnvelope

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_CASE_DIR = ROOT / "data" / "re2_ss" / "catalogue_cpu"

_SIMPLE_METRICS = (
    "time,catalogue_cpu,catalogue-db_cpu,front-end_cpu,catalogue_mem,front-end_latency-90\n"
    "1705600031,0.5,0.6,,100.0,12.3\n"
    "1705600032,0.7,,0.1,110.0,13.0\n"
)
# container_name includes a k8s pod name to exercise normalization; level empty.
_LOGS = (
    "time,timestamp,container_name,message,level,req_path,error\n"
    "17:47,1705600032200637252,catalogue,health check ok,,,\n"
    "17:47,1705600033200637252,front-end,GET /catalogue,,/catalogue,\n"
    "17:47,1705600034200637252,catalogue-6999fd64d9-pjhhn,pod-named log,INFO,,\n"
)
_POD_NODE = (
    "POD,NODE_NAME\n"
    "catalogue-6999fd64d9-pjhhn,node-a\n"
    "catalogue-db-554cbfd749-sbwkr,node-b\n"
    "carts-795df7fd79-7x8z8,node-c\n"
)
_INJECT_TIME = "1705600751"


def _make_synthetic_case(tmp: Path) -> Path:
    run = tmp / "re2_ss" / "catalogue_cpu" / "1"
    run.mkdir(parents=True)
    (run / "simple_metrics.csv").write_text(_SIMPLE_METRICS, encoding="utf-8")
    (run / "logs.csv").write_text(_LOGS, encoding="utf-8")
    (run / "pod-node-1.csv").write_text(_POD_NODE, encoding="utf-8")
    (run / "inject_time.txt").write_text(_INJECT_TIME, encoding="utf-8")
    return tmp / "re2_ss" / "catalogue_cpu"


@pytest.fixture
def synthetic(tmp_path: Path) -> tuple[CaseBundle, Path]:
    case_dir = _make_synthetic_case(tmp_path)
    labels = tmp_path / "labels"
    bundle = load_case(case_dir, labels_dir=labels)
    return bundle, labels


# --------------------------- adapter behavior ---------------------------
def test_case_identity(synthetic) -> None:
    bundle, _ = synthetic
    assert bundle.case_id == "catalogue_cpu-1"
    assert bundle.inject_time == 1705600751.0


def test_event_counts_and_sources(synthetic) -> None:
    bundle, _ = synthetic
    # 8 non-empty metric cells + 3 log rows
    metrics = [e for e in bundle.events if e.source == "metric"]
    logs = [e for e in bundle.events if e.source == "log"]
    assert len(metrics) == 8
    assert len(logs) == 3
    assert len(bundle.events) == 11


def test_all_event_ids_valid_and_unique(synthetic) -> None:
    bundle, _ = synthetic
    ids = [e.event_id for e in bundle.events]
    assert len(ids) == len(set(ids)), "event ids must be unique"
    for eid in ids:
        assert EVENT_ID_RE.match(eid), f"invalid event_id {eid!r}"


def test_every_component_is_a_topology_node(synthetic) -> None:
    bundle, _ = synthetic
    nodes = set(bundle.topology.nodes)
    for e in bundle.events:
        assert e.component_id in nodes, f"{e.component_id} missing from topology"
    # pod-node seeded 'carts' even though it has no events
    assert "carts" in nodes
    assert bundle.topology.has_edge("catalogue", "catalogue-db")
    assert bundle.topology.has_edge("front-end", "catalogue")


def test_wide_to_long_metric_values(synthetic) -> None:
    bundle, _ = synthetic
    # catalogue cpu at t=1705600031 should be 0.5
    hit = [
        e for e in bundle.events
        if e.source == "metric"
        and e.component_id == "catalogue"
        and e.payload.name == "cpu"
        and e.ts == 1705600031.0
    ]
    assert len(hit) == 1
    assert hit[0].payload.value == 0.5
    assert hit[0].payload.unit == "cores"


def test_empty_metric_cells_dropped(synthetic) -> None:
    bundle, _ = synthetic
    # front-end_cpu is empty at t=...31 (present at ...32); only 1 front-end cpu event
    fe_cpu = [
        e for e in bundle.events
        if e.source == "metric" and e.component_id == "front-end" and e.payload.name == "cpu"
    ]
    assert len(fe_cpu) == 1
    assert fe_cpu[0].ts == 1705600032.0


def test_log_level_nullability_and_pod_normalization(synthetic) -> None:
    bundle, _ = synthetic
    logs = [e for e in bundle.events if e.source == "log"]
    # k8s pod name normalized to 'catalogue'
    assert any(e.component_id == "catalogue" and e.payload.level == "INFO" for e in logs)
    # empty level -> None; ns timestamp -> epoch seconds
    empties = [e for e in logs if e.payload.level is None]
    assert len(empties) == 2
    fe = [e for e in logs if e.component_id == "front-end"][0]
    assert fe.payload.req_path == "/catalogue"
    assert abs(fe.ts - 1705600033.2) < 1.0


def test_ground_truth_quarantined_to_sidecar(synthetic) -> None:
    bundle, labels = synthetic
    # events carry ONLY envelope keys (no fault_service / inject_time leakage)
    allowed = {"event_id", "case_id", "source", "component_id", "ts", "payload"}
    for e in bundle.events:
        assert set(e.model_dump().keys()) == allowed
    sidecar = labels / "catalogue_cpu-1.json"
    assert sidecar.exists()
    label = json.loads(sidecar.read_text(encoding="utf-8"))
    assert label["fault_service"] == "catalogue"
    assert label["fault_type"] == "cpu"
    assert label["inject_time"] == 1705600751.0


# --------------------------- store round-trip ---------------------------
def test_store_write_query_resolve(synthetic, tmp_path: Path) -> None:
    bundle, _ = synthetic
    store = EventStore(tmp_path / "parquet")
    n = store.write_case(bundle)
    assert n == len(bundle.events)

    metrics = store.events(bundle.case_id, source="metric")
    assert metrics.height == 8
    cat = store.events(bundle.case_id, source="metric", component_id="catalogue")
    assert cat.height == 4  # cpu(31,32) + mem(31,32)

    sample = [e.event_id for e in bundle.events[:5]]
    assert store.resolve(sample) is True
    assert store.resolve(sample + ["metric-nope-999999"]) is False
    assert store.resolve([]) is True

    # time filter
    early = store.events(bundle.case_id, t0=1705600031.0, t1=1705600031.0)
    assert early.height >= 1
    assert all(r == 1705600031.0 for r in early["ts"].to_list())


def test_store_partitioning_on_disk(synthetic, tmp_path: Path) -> None:
    bundle, _ = synthetic
    store = EventStore(tmp_path / "parquet")
    store.write_case(bundle)
    root = tmp_path / "parquet"
    assert (root / "case_id=catalogue_cpu-1" / "source=metric" / "events.parquet").exists()
    assert (root / "case_id=catalogue_cpu-1" / "source=log" / "events.parquet").exists()


# --------------------------- real golden case ---------------------------
@pytest.mark.skipif(
    not (GOLDEN_CASE_DIR / "1" / "simple_metrics.csv").exists()
    and not (GOLDEN_CASE_DIR / "simple_metrics.csv").exists(),
    reason="golden case not extracted (run scripts/fetch_golden_case.sh)",
)
def test_golden_case_integration(tmp_path: Path) -> None:
    bundle = load_case(GOLDEN_CASE_DIR, labels_dir=tmp_path / "labels")
    assert len(bundle.events) > 1000

    nodes = set(bundle.topology.nodes)
    ids = [e.event_id for e in bundle.events]
    assert len(ids) == len(set(ids))
    for e in bundle.events:
        assert EVENT_ID_RE.match(e.event_id)
        assert e.component_id in nodes

    store = EventStore(tmp_path / "parquet")
    store.write_case(bundle)
    sample = ids[:200] + ids[-200:]
    assert store.resolve(sample) is True
    assert store.resolve(["metric-does-not-exist-000001"]) is False
