"""SimPy twin: cascade behavior, fault injectors, remedies, compare, runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.ingest.re2ss_adapter import build_topology
from backend.ingest.store import EventStore
from backend.overlay.scenarios import gen_clean_cascade
from backend.twin.compare import compare
from backend.twin.faults import inject
from backend.twin.model import FEATURES, TwinModel
from backend.twin.remedies import REMEDIES, Remedy, rehearse
from backend.twin.runner import twin

_COMPS = {"loadgenerator", "front-end", "catalogue", "catalogue-db",
          "carts", "carts-db", "orders", "orders-db", "payment", "user", "user-db"}


def _topo():
    return build_topology(_COMPS)


# --------------------------- model + faults ---------------------------
def test_cpu_fault_on_carts_cascades_to_frontend() -> None:
    topo = _topo()
    base = TwinModel(topo, seed=1)
    base.run(30.0)
    fe_base = base.aggregate("front-end", 5.0, 30.0)[0]

    faulted = TwinModel(topo, seed=1)
    faulted.run(30.0, hooks=[(8.0, lambda m: inject(m, "cpu", "carts"))])
    fe_fault = faulted.aggregate("front-end", 12.0, 30.0)[0]
    carts_fault = faulted.aggregate("carts", 12.0, 30.0)[0]
    carts_base = base.aggregate("carts", 5.0, 30.0)[0]

    assert carts_fault > carts_base * 1.5          # carts is the bottleneck
    assert fe_fault > fe_base * 1.2                # latency cascades to front-end


@pytest.mark.parametrize("fault_type", ["cpu", "mem", "disk", "delay", "loss", "socket", "config_push"])
def test_every_fault_type_injects_without_error(fault_type: str) -> None:
    topo = _topo()
    m = TwinModel(topo, seed=2)
    m.run(20.0, hooks=[(6.0, lambda mm: inject(mm, fault_type, "catalogue"))])
    assert m.components()                          # sim produced traffic


# --------------------------- remedies ---------------------------
def test_remedies_catalog_covers_every_fault_type() -> None:
    for ft in ("cpu", "mem", "disk", "delay", "loss", "socket", "config_push"):
        assert REMEDIES[ft], ft
    assert any(r.name == "restart" for r in REMEDIES["cpu"])
    assert REMEDIES["config_push"][0].name == "rollback_config"


def test_rehearse_restart_clears_at_least_half() -> None:
    report = rehearse(_topo(), "carts", "cpu", Remedy("restart"), seed=1)
    assert report.remedy == "restart"
    assert report.symptoms_cleared_pct >= 50.0
    assert report.sim_time_to_recover_s > 0


def test_rehearse_scale_replicas_clears_symptoms() -> None:
    report = rehearse(_topo(), "carts", "cpu", Remedy("scale_replicas", (2,)), seed=1)
    assert report.symptoms_cleared_pct >= 50.0


# --------------------------- compare ---------------------------
def test_compare_match_and_mismatch() -> None:
    sim = {"carts": [0.1, 0.1, 0.0, 0.0, 0.2], "front-end": [0.05, 0.05, 0.0, 0.0, 0.05]}
    instrumented = {"carts", "front-end"}
    # identical real signature -> match
    m = compare(sim, {c: v[:] for c, v in sim.items()}, instrumented)
    assert m["verdict"] == "match" and m["similarity"] >= 0.8
    # opposite real signature -> mismatch
    opp = {c: [-x for x in v] for c, v in sim.items()}
    mm = compare(sim, opp, instrumented)
    assert mm["verdict"] == "mismatch"


def test_compare_missing_evidence_for_uninstrumented_sim_symptom() -> None:
    sim = {"carts-db": [0.3, 0.3, 0.0, 0.0, 0.0]}   # a sim symptom...
    out = compare(sim, {}, set())                    # ...at an uninstrumented comp
    assert "carts-db" in out["missing_evidence"]
    assert out["recommendations"]


# --------------------------- runner ---------------------------
def test_twin_runner_produces_verdict_block(tmp_path: Path) -> None:
    bundle, _ = gen_clean_cascade("tw1", 42, fault_service="catalogue", fault_type="cpu")
    store = EventStore(tmp_path / "p")
    store.write_case(bundle)
    store.write_topology("tw1", bundle.topology)

    block = twin("tw1", "catalogue", "cpu", store_root=tmp_path / "p")
    assert block["run"] == "twin-catalogue-cpu"
    assert block["verdict"] in ("match", "partial", "mismatch")
    assert 0.0 <= block["similarity"] <= 1.0
    assert isinstance(block["missing_evidence"], list)
