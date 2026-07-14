"""Counterfactual mechanism + autopilot discounting."""

from __future__ import annotations

from pathlib import Path

from backend.detect import AnomalyBuilder
from backend.ingest.re2ss_adapter import build_topology
from backend.ingest.store import EventStore
from backend.localize.blast import blast_radius, reachable_upstream
from backend.overlay.scenarios import gen_clean_cascade
from backend.rank.autopilot import run as autopilot_run
from backend.rank.counterfactual import remove_and_explain, score_multiplier


def _topo():
    return build_topology({"front-end", "catalogue", "catalogue-db", "carts", "carts-db"})


def _anoms():
    b = AnomalyBuilder("t")
    b.make("catalogue", "metric", "mad_zscore", 120, 300, 1.0, [], "catalogue cpu |z|=90")
    b.make("front-end", "metric", "mad_zscore", 140, 300, 0.5, [], "front-end latency |z|=5")
    return b.anomalies


# --------------------------- pure mechanism ---------------------------
def test_remove_and_explain_redundant_vs_load_bearing() -> None:
    topo = _topo()
    anoms = _anoms()
    blast = blast_radius(topo, {a.component_id for a in anoms})
    reach_by = {c: reachable_upstream(topo, c) for c in ("catalogue", "front-end")}

    # front-end (the symptom) is redundant: catalogue's reach already covers it
    assert remove_and_explain(blast, anoms, reach_by, "front-end") == 100.0
    # catalogue is load-bearing: without it, its own anomaly is unexplained
    assert remove_and_explain(blast, anoms, reach_by, "catalogue") == 50.0


def test_score_multiplier() -> None:
    assert score_multiplier(100.0) == 0.5     # fully redundant
    assert score_multiplier(0.0) == 1.0       # indispensable
    assert score_multiplier(50.0) == 0.75


# --------------------------- autopilot integration ---------------------------
def test_autopilot_discounts_redundant_symptom(tmp_path: Path) -> None:
    from backend.ingest.re2ss_adapter import CaseBundle  # noqa: F401  (type clarity)
    bundle, _ = gen_clean_cascade("cf1", 42, fault_service="catalogue", fault_type="cpu")
    store = EventStore(tmp_path / "p")
    store.write_case(bundle)
    store.write_topology("cf1", bundle.topology)

    from backend.detect.runner import detect
    detect("cf1", store_root=tmp_path / "p", out_dir=tmp_path / "anom", drain_dir=tmp_path / "drain")

    verdict = autopilot_run("cf1", store_root=tmp_path / "p", anomalies_dir=tmp_path / "anom",
                            ledger_dir=tmp_path / "ledger")
    # the injected fault ranks #1
    assert verdict.hypotheses[0].suspect_component == "catalogue"
    # at least one hypothesis went through the counterfactual (removed=True)
    assert any(h.counterfactual.removed for h in verdict.hypotheses)
    # counterfactual_result facts were written to the ledger
    assert verdict.ledger.query(kind="counterfactual_result")
