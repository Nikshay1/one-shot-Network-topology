"""Tests for blast radius, deterministic candidates, and the scorer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.detect import AnomalyBuilder
from backend.ingest.re2ss_adapter import build_topology
from backend.ingest.store import EventStore
from backend.localize.blast import blast_radius
from backend.rank.candidates import fault_type_from_anomaly, generate_candidates
from backend.rank.constants import WEIGHTS
from backend.rank.scorer import load_anomalies, rank

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "parquet"
LABELS = ROOT / "data" / "labels"
ANOMALIES = ROOT / "data" / "anomalies"
GOLDEN = "catalogue_cpu-1"


def _topo():
    return build_topology({"front-end", "catalogue", "catalogue-db", "carts", "carts-db"})


def _anoms(specs):
    """specs: list of (comp, source, method, score, start, end, summary, evidence)."""
    b = AnomalyBuilder("t")
    for comp, source, method, score, start, end, summary, ev in specs:
        b.make(comp, source, method, start, end, score, ev, summary)
    return b.anomalies


# --------------------------- blast ---------------------------
def test_blast_radius_both_directions_and_impact() -> None:
    topo = _topo()
    blast = blast_radius(topo, {"catalogue"}, k=2)
    assert "catalogue" in blast.nodes
    assert "catalogue-db" in blast.nodes   # downstream callee
    assert "front-end" in blast.nodes      # upstream caller
    assert all(e.latency_direction == "callee->caller" for e in blast.edges)
    # front-end reaches the most downstream and is the most critical
    assert blast.impact["front-end"] == max(blast.impact.values())
    assert blast.impact["catalogue"] > 0


# --------------------------- candidates ---------------------------
def test_fault_type_inference() -> None:
    specs = [
        ("catalogue", "metric", "mad_zscore", 1.0, 100, 130, "catalogue cpu |z|=90", []),
        ("carts", "metric", "mad_zscore", 1.0, 100, 130, "carts mem |z|=40", []),
        ("catalogue-db", "config", "config_risky_flag", 0.6, 90, 90, "risky change max_connections", []),
    ]
    a_cpu, a_mem, a_cfg = _anoms(specs)
    assert fault_type_from_anomaly(a_cpu) == "cpu"
    assert fault_type_from_anomaly(a_mem) == "mem"
    assert fault_type_from_anomaly(a_cfg) == "config_push"


def test_candidates_suspects_symptoms_and_config_target() -> None:
    topo = _topo()
    anoms = _anoms([
        ("catalogue", "metric", "mad_zscore", 1.0, 120, 200, "catalogue cpu |z|=90", ["metric-catalogue-000000"]),
        ("front-end", "metric", "mad_zscore", 0.5, 130, 200, "front-end latency |z|=5", []),
        ("catalogue-db", "config", "config_risky_flag", 0.6, 90, 90, "risky change max_connections limit", ["config-catalogue_db-000000"]),
    ])
    blast = blast_radius(topo, {a.component_id for a in anoms})
    cands = generate_candidates("t", anoms, topo, blast)
    suspects = {c.suspect for c in cands}
    assert {"catalogue", "front-end", "catalogue-db"} <= suspects   # incl. config target

    cat = next(c for c in cands if c.suspect == "catalogue")
    assert cat.fault_type_guess == "cpu"
    assert cat.trigger_event_id == "metric-catalogue-000000"
    # front-end depends on catalogue and is anomalous -> observed True symptom
    fe_sym = [s for s in cat.predicted_symptoms if s["component_id"] == "front-end"]
    assert fe_sym and fe_sym[0]["observed"] is True

    db = next(c for c in cands if c.suspect == "catalogue-db")
    assert db.fault_type_guess == "config_push"


# --------------------------- scorer ---------------------------
def test_scorer_breakdown_sums_to_score_and_uses_weights() -> None:
    topo = _topo()
    anoms = _anoms([
        ("catalogue", "metric", "mad_zscore", 1.0, 120, 200, "catalogue cpu |z|=90", []),
        ("catalogue", "log", "log_rare_template", 0.8, 121, 200, "catalogue connection refused", []),
        ("front-end", "metric", "mad_zscore", 0.5, 130, 200, "front-end latency |z|=5", []),
    ])
    ranked = rank("t", anoms, topo)
    assert ranked
    for h in ranked:
        assert abs(sum(h.score_breakdown.model_dump().values()) - h.score) < 1e-6
        assert 0.0 <= h.score <= 1.0
        # each component pre-weighted -> bounded by its weight
        for key, val in h.score_breakdown.model_dump().items():
            assert val <= WEIGHTS[key] + 1e-9


def test_scorer_ranks_root_above_symptom() -> None:
    topo = _topo()
    # catalogue is the root (cpu + log); front-end shows only latency symptom
    anoms = _anoms([
        ("catalogue", "metric", "mad_zscore", 1.0, 120, 300, "catalogue cpu |z|=90", []),
        ("catalogue", "log", "log_rare_template", 0.8, 121, 300, "catalogue connection refused", []),
        ("front-end", "metric", "mad_zscore", 0.5, 140, 300, "front-end latency |z|=5", []),
    ])
    ranked = rank("t", anoms, topo)
    order = [h.suspect_component for h in ranked]
    assert order.index("catalogue") < order.index("front-end")
    assert ranked[0].rank == 1


# --------------------------- golden integration ---------------------------
@pytest.mark.skipif(
    not (ANOMALIES / f"{GOLDEN}.json").exists() or not (STORE / f"case_id={GOLDEN}" / "topology.json").exists(),
    reason="golden anomalies/topology not present (run detect + adapter)",
)
def test_golden_top3_contains_true_fault() -> None:
    topology = EventStore(STORE).load_topology(GOLDEN)
    anomalies = load_anomalies(GOLDEN, ANOMALIES)
    ranked = rank(GOLDEN, anomalies, topology)
    top3 = [h.suspect_component for h in ranked[:3]]
    fault_service = json.loads((LABELS / f"{GOLDEN}.json").read_text())["fault_service"]  # test-only
    assert fault_service in top3, f"{fault_service} not in top-3 {top3}"
