"""Tests for the agent tool registry, file_finding validation, and budgets."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.budget import Budget, BudgetExceeded
from backend.agents.tools import (
    REGISTRY,
    RankedHypothesis,
    ToolContext,
    call_tool,
)
from backend.detect import AnomalyBuilder
from backend.ingest.re2ss_adapter import CaseBundle, build_topology
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.overlay.config_overlay import EventFactory


@pytest.fixture
def ctx(tmp_path: Path):
    f = EventFactory("case-t")
    e_cpu = f.metric("catalogue", "cpu", 95.0, 100.0, unit="ratio")
    f.log("catalogue", "connection refused to catalogue-db", 101.0)
    topo = build_topology({"front-end", "catalogue", "catalogue-db"})
    bundle = CaseBundle(case_id="case-t", events=f.events, topology=topo, inject_time=100.0)
    store = EventStore(tmp_path / "p")
    store.write_case(bundle)
    store.write_topology("case-t", topo)

    ab = AnomalyBuilder("case-t")
    ab.make("catalogue", "metric", "mad_zscore", 100.0, 130.0, 1.0, [e_cpu.event_id],
            "catalogue cpu |z|=99")
    anomalies = ab.anomalies
    blast = blast_radius(topo, {"catalogue"})
    ledger = Ledger("run-t", "case-t", ledger_dir=tmp_path / "ledger")
    context = ToolContext(case_id="case-t", store=store, topology=topo,
                          anomalies=anomalies, blast=blast, ledger=ledger)
    return context, e_cpu.event_id


# --------------------------- registry ---------------------------
def test_registry_contents_and_costs() -> None:
    expected = {
        "get_anomalies": 0, "get_candidates": 0, "check_path": 0,
        "get_topology_summary": 0, "get_events": 0, "get_ledger": 0,
        "run_counterfactual": 1, "run_twin": 2, "file_finding": 0,
    }
    assert set(REGISTRY) == set(expected)
    for name, cost in expected.items():
        assert REGISTRY[name].cost == cost


# --------------------------- read tools ---------------------------
def test_read_tools(ctx) -> None:
    context, ev = ctx
    an = call_tool("get_anomalies", {"component_id": "catalogue"}, context)
    assert len(an.anomalies) == 1

    cand = call_tool("get_candidates", {}, context)
    assert cand.candidates and isinstance(cand.candidates[0], RankedHypothesis)

    path = call_tool("check_path", {"src": "front-end", "dst": "catalogue-db"}, context)
    assert path.path_exists and path.path[0] == "front-end"

    topo = call_tool("get_topology_summary", {}, context)
    assert any(n["id"] == "catalogue" for n in topo.nodes)
    assert "nodes" in topo.blast

    ev_out = call_tool("get_events", {"event_ids": [ev, "metric-nope-000001"]}, context)
    assert len(ev_out.events) == 1 and ev_out.missing == ["metric-nope-000001"]


def test_run_twin_still_stubbed(ctx) -> None:
    context, _ = ctx
    assert call_tool("run_twin", {"component": "catalogue", "fault_type": "cpu"}, context).status == "unavailable"


def test_run_counterfactual_is_live(ctx) -> None:
    context, _ = ctx
    out = call_tool("run_counterfactual", {"component": "catalogue"}, context)
    assert out.status == "ok"
    assert 0.0 <= out.still_explained_pct <= 100.0
    assert 0.5 <= out.score_multiplier <= 1.0
    assert out.fact_id.startswith("fact-")
    # it auto-filed a counterfactual_result fact
    facts = context.ledger.query(kind="counterfactual_result")
    assert any(f.fact_id == out.fact_id for f in facts)


# --------------------------- file_finding validation ---------------------------
def test_file_finding_valid_appends_to_ledger(ctx) -> None:
    context, ev = ctx
    out = call_tool("file_finding", {
        "kind": "investigation_note",
        "statement": "catalogue cpu is the likely root cause",
        "component_ids": ["catalogue"],
        "event_ids": [ev],
    }, context)
    assert out.ok and out.fact_id and out.fact_id.startswith("fact-catalogue-")
    records = context.ledger.query(component_id="catalogue")
    assert any(r.fact_id == out.fact_id for r in records)
    assert records[-1].confidence == 0.7   # agent-finding confidence


def test_file_finding_rejects_fake_event_id(ctx) -> None:
    context, _ = ctx
    out = call_tool("file_finding", {
        "kind": "investigation_note", "statement": "bogus",
        "component_ids": ["catalogue"], "event_ids": ["metric-fake-000001"],
    }, context)
    assert out.ok is False and out.error == "unresolved_event_id"


def test_file_finding_rejects_unknown_component(ctx) -> None:
    context, ev = ctx
    out = call_tool("file_finding", {
        "kind": "investigation_note", "statement": "bad comp",
        "component_ids": ["not-a-real-service"], "event_ids": [ev],
    }, context)
    assert out.ok is False and out.error == "unknown_component"


def test_file_finding_rejects_bad_kind(ctx) -> None:
    context, ev = ctx
    out = call_tool("file_finding", {
        "kind": "totally_made_up", "statement": "x",
        "component_ids": ["catalogue"], "event_ids": [ev],
    }, context)
    assert out.ok is False and out.error == "invalid_kind"


# --------------------------- budgets ---------------------------
def test_budget_trips_on_max_calls() -> None:
    b = Budget(max_calls=3, max_cost_points=100).start()
    for _ in range(3):
        b.charge(0)
    with pytest.raises(BudgetExceeded) as exc:
        b.charge(0)
    assert exc.value.reason == "max_calls"


def test_budget_trips_on_cost_points() -> None:
    b = Budget(max_calls=100, max_cost_points=3).start()
    b.charge(2)                         # cost 2
    with pytest.raises(BudgetExceeded) as exc:
        b.charge(2)                     # would be 4 > 3
    assert exc.value.reason == "max_cost_points"
    assert b.cost == 2                  # nothing incremented on trip


def test_budget_trips_on_wall_clock() -> None:
    ticks = iter([0.0, 0.0, 100.0])     # start, elapsed check < , elapsed check >
    b = Budget(max_calls=100, max_cost_points=100, wall_clock_s=60.0, clock=lambda: next(ticks))
    b.start()
    b.charge(0)                         # elapsed 0 -> ok
    with pytest.raises(BudgetExceeded) as exc:
        b.charge(0)                     # elapsed 100 > 60
    assert exc.value.reason == "wall_clock"


def test_call_tool_charges_budget(ctx) -> None:
    context, _ = ctx
    b = Budget(max_calls=1, max_cost_points=10).start()
    call_tool("get_anomalies", {}, context, budget=b)
    assert b.calls == 1
    with pytest.raises(BudgetExceeded):
        call_tool("get_anomalies", {}, context, budget=b)


def test_call_tool_charges_cost_points(ctx) -> None:
    context, _ = ctx
    b = Budget(max_calls=10, max_cost_points=2).start()
    call_tool("run_twin", {"component": "catalogue", "fault_type": "cpu"}, context, budget=b)
    assert b.cost == 2
