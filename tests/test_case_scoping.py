"""event_id is unique WITHIN a case, never across cases.

The generator numbers events per component, so `metric-catalogue-000023` exists in
every case in the store. `EventStore.get_by_ids` used to semi-join on event_id alone
and document itself as "(any case)" — one lookup against `data/parquet` returned 26
rows from 26 unrelated cases. Everything below is a regression test for one of the
four places that fed.

Two kinds of id matter here and the difference is the whole test:

  * a COLLIDING id  — `metric-catalogue-000000`, real in both cases. Scoping decides
    *which* row you get; an unscoped lookup returns both.
  * a FOREIGN-ONLY id — `metric-catalogue-000001`, real only in the other case.
    Scoping decides whether it resolves *at all*.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.challenger import validate_attack
from backend.agents.tools import ToolContext, call_tool
from backend.detect import AnomalyBuilder
from backend.ingest.re2ss_adapter import CaseBundle, build_topology
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.overlay.config_overlay import EventFactory
from backend.rank.scorer import rank

MINE, THEIRS = "scope-mine", "scope-theirs"


@pytest.fixture
def two_cases(tmp_path: Path):
    topo = build_topology({"front-end", "catalogue", "catalogue-db"})
    store = EventStore(tmp_path / "p")

    # MINE: one catalogue metric  -> metric-catalogue-000000
    mine_f = EventFactory(MINE)
    mine_id = mine_f.metric("catalogue", "cpu", 95.0, 100.0, unit="ratio").event_id
    store.write_case(CaseBundle(case_id=MINE, events=mine_f.events,
                                topology=topo, inject_time=100.0))
    store.write_topology(MINE, topo)

    # THEIRS: two catalogue metrics -> ...000000 (collides) and ...000001 (foreign-only)
    theirs_f = EventFactory(THEIRS)
    collide_id = theirs_f.metric("catalogue", "cpu", 95.0, 100.0, unit="ratio").event_id
    foreign_id = theirs_f.metric("catalogue", "mem", 5.0e8, 900.0, unit="bytes").event_id
    store.write_case(CaseBundle(case_id=THEIRS, events=theirs_f.events,
                                topology=topo, inject_time=100.0))
    store.write_topology(THEIRS, topo)

    assert mine_id == collide_id, "fixture is pointless unless the ids collide"
    assert foreign_id != mine_id

    b = AnomalyBuilder(MINE)
    b.make("catalogue", "metric", "mad_zscore", 100, 200, 1.0, [mine_id], "catalogue cpu")
    anomalies = b.anomalies
    ctx = ToolContext(case_id=MINE, store=store, topology=topo, anomalies=anomalies,
                      blast=blast_radius(topo, {a.component_id for a in anomalies}),
                      ledger=Ledger(MINE, MINE, tmp_path / "l"))
    return {"ctx": ctx, "store": store, "collide_id": collide_id, "foreign_id": foreign_id,
            "topo": topo, "hyps": rank(MINE, anomalies, topo, store=store)}


# =========================================================================
# guard the guard
# =========================================================================
def test_the_ids_really_do_collide(two_cases) -> None:
    """Without this, every assertion below could pass for the wrong reason."""
    store, eid = two_cases["store"], two_cases["collide_id"]
    assert store.get_by_ids([eid], case_id=MINE)["case_id"].to_list() == [MINE]
    assert store.get_by_ids([eid], case_id=THEIRS)["case_id"].to_list() == [THEIRS]


def test_the_foreign_id_is_real_but_only_over_there(two_cases) -> None:
    store, fid = two_cases["store"], two_cases["foreign_id"]
    assert store.get_by_ids([fid], case_id=THEIRS).height == 1, "foreign id must be REAL"
    assert store.get_by_ids([fid], case_id=MINE).height == 0, "...and absent from MINE"


# =========================================================================
# the four blast sites
# =========================================================================
def test_get_by_ids_never_returns_a_foreign_case(two_cases) -> None:
    df = two_cases["store"].get_by_ids([two_cases["collide_id"]], case_id=MINE)
    assert set(df["case_id"].to_list()) == {MINE}, "lookup crossed a case boundary"


def test_get_events_tool_shows_the_agent_only_its_own_case(two_cases) -> None:
    """The agent's evidence must come from the case under investigation, full stop."""
    out = call_tool("get_events", {"event_ids": [two_cases["collide_id"]]}, two_cases["ctx"])
    assert len(out.events) == 1, "the agent was shown one event per case in the store"
    assert out.events[0].case_id == MINE


def test_get_events_reports_a_foreign_id_as_missing(two_cases) -> None:
    out = call_tool("get_events", {"event_ids": [two_cases["foreign_id"]]}, two_cases["ctx"])
    assert out.events == []
    assert two_cases["foreign_id"] in out.missing


def test_file_finding_cannot_resolve_a_citation_from_another_case(two_cases) -> None:
    """file_finding is the ONE mutating tool (rule 9). A citation that resolves only
    in a different case must not be allowed to write a fact into this case's ledger."""
    out = call_tool("file_finding", {
        "kind": "investigation_note", "component_ids": ["catalogue"],
        "statement": "cites an event that belongs to another case",
        "event_ids": [two_cases["foreign_id"]],
    }, two_cases["ctx"])
    assert out.ok is False, "a foreign event_id was accepted as a resolved citation"
    assert out.error == "unresolved_event_id"     # not merely False — False for the RIGHT reason
    assert two_cases["foreign_id"] in (out.detail or "")


def test_challenger_citation_must_exist_in_this_case(two_cases) -> None:
    """validate_attack's contract is 'the cited event must EXIST and PERTAIN'. An
    event from another case satisfies neither, however real it looks."""
    ok = validate_attack(two_cases["ctx"], two_cases["hyps"][0],
                         {"claim": "this disproves the hypothesis",
                          "contradicting_event_id": two_cases["foreign_id"]})
    assert ok is False, "an attack citing another case's event was accepted"
