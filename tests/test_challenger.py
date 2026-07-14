"""The Challenger: an attack is exactly as strong as its citation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.budget import Budget
from backend.agents.challenger import (
    CHALLENGER_TOOLS,
    MODEL,
    challenge,
    parse_attacks,
    render_prompt,
    validate_attack,
)
from backend.agents.harness import STATUS_COMPLETED, LLMDecision, ScriptedLLM
from backend.agents.tools import ToolContext
from backend.detect import AnomalyBuilder
from backend.ingest.re2ss_adapter import CaseBundle, build_topology
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.overlay.config_overlay import EventFactory
from backend.rank.rescore import rescore_from_ledger
from backend.rank.scorer import rank

CASE = "ch1"


@pytest.fixture
def case(tmp_path: Path):
    topo = build_topology({"front-end", "catalogue", "catalogue-db", "carts", "carts-db"})
    f = EventFactory(CASE)
    e_cat = f.metric("catalogue", "cpu", 95.0, 100.0, unit="ratio")
    e_fe = f.metric("front-end", "latency-90", 0.7, 110.0, unit="s")
    e_far = f.metric("carts-db", "mem", 5.0e8, 900.0, unit="bytes")   # unrelated + far in time
    store = EventStore(tmp_path / "p")
    store.write_case(CaseBundle(case_id=CASE, events=f.events, topology=topo, inject_time=100.0))
    store.write_topology(CASE, topo)

    b = AnomalyBuilder(CASE)
    b.make("catalogue", "metric", "mad_zscore", 100, 200, 1.0, [e_cat.event_id], "catalogue cpu |z|=99")
    b.make("front-end", "metric", "mad_zscore", 110, 200, 0.6, [e_fe.event_id], "front-end latency |z|=6")
    anomalies = b.anomalies

    ledger = Ledger(CASE, CASE, tmp_path / "l")
    ctx = ToolContext(case_id=CASE, store=store, topology=topo, anomalies=anomalies,
                      blast=blast_radius(topo, {a.component_id for a in anomalies}),
                      ledger=ledger)
    hyps = rank(CASE, anomalies, topo, store=store)
    return {"ctx": ctx, "hyps": hyps, "tmp": tmp_path,
            "e_cat": e_cat.event_id, "e_fe": e_fe.event_id, "e_far": e_far.event_id,
            "anomalies": anomalies, "topo": topo, "store": store, "ledger": ledger}


def _challenge(case, final_text, budget=None):
    return challenge(case["ctx"], case["hyps"][0], run_id=CASE,
                     llm=ScriptedLLM([LLMDecision(final=final_text)]),
                     budget=budget, transcripts_dir=case["tmp"] / "t")


# --------------------------- wiring ---------------------------
def test_challenger_is_read_only_and_cheap() -> None:
    from backend.agents.tools import REGISTRY
    assert CHALLENGER_TOOLS == ["get_ledger", "get_events", "check_path"]
    assert all(REGISTRY[t].cost == 0 for t in CHALLENGER_TOOLS)   # 0-cost, read-only
    assert "file_finding" not in CHALLENGER_TOOLS                 # cannot mutate state
    assert "run_twin" not in CHALLENGER_TOOLS
    assert MODEL == "gpt-4o-mini"


def test_prompt_states_the_citation_contract(case) -> None:
    text = render_prompt(case["ctx"], case["hyps"][0],
                         Budget(max_calls=5, max_cost_points=0, wall_clock_s=30))
    assert "exactly as strong as its citation" in text
    assert "silently discarded" in text
    assert case["hyps"][0].suspect_component in text


def test_parse_attacks_tolerates_prose_and_junk() -> None:
    assert parse_attacks(None) == []
    assert parse_attacks("no json here") == []
    assert parse_attacks('[{"claim":"x","contradicting_event_id":"metric-a-000001"}]')[0]["claim"] == "x"
    # missing fields are dropped
    assert parse_attacks('[{"claim":"only"}]') == []


# --------------------------- THE GATE: fake citations die ---------------------------
def test_fake_citation_attack_is_discarded(case) -> None:
    attacks, res = _challenge(case, json.dumps(
        [{"claim": "actually it was DNS", "contradicting_event_id": "metric-dns-000001"}]))
    assert res.status == STATUS_COMPLETED
    assert attacks == []                       # the id does not resolve -> silently discarded


def test_irrelevant_citation_attack_is_discarded(case) -> None:
    # a REAL event, but on an unrelated component and far outside the anomaly window
    assert case["ctx"].store.get_by_ids([case["e_far"]]).height == 1
    attacks, _ = _challenge(case, json.dumps(
        [{"claim": "carts-db memory disproves it", "contradicting_event_id": case["e_far"]}]))
    assert attacks == []                       # resolves, but does not pertain


def test_pertinent_citation_is_upheld(case) -> None:
    attacks, _ = _challenge(case, json.dumps(
        [{"claim": "front-end latency started first", "contradicting_event_id": case["e_fe"]}]))
    assert len(attacks) == 1 and attacks[0]["upheld"] is True
    assert validate_attack(case["ctx"], case["hyps"][0], attacks[0])


def test_validate_attack_component_and_time_rules(case) -> None:
    top = case["hyps"][0]
    # same component -> pertains
    assert validate_attack(case["ctx"], top, {"contradicting_event_id": case["e_cat"]})
    # unresolvable -> never pertains
    assert not validate_attack(case["ctx"], top, {"contradicting_event_id": "metric-nope-000009"})


# --------------------------- upheld attacks cost score + tier ---------------------------
def test_upheld_attacks_lower_score_and_annotate_tier(case) -> None:
    top = case["hyps"][0]
    attacks, _ = _challenge(case, json.dumps(
        [{"claim": "front-end latency started first", "contradicting_event_id": case["e_fe"]}]))
    assert attacks

    after = rescore_from_ledger(CASE, case["anomalies"], case["topo"], case["store"],
                                case["ledger"], {top.hypothesis_id: attacks})
    challenged = next(h for h in after if h.hypothesis_id == top.hypothesis_id)
    assert challenged.score == pytest.approx(max(0.0, top.score - 0.1), abs=1e-3)  # -0.1 each
    assert challenged.challenger is not None and challenged.challenger.attacks[0].upheld
    assert challenged.tier != "CONFIRMED"
    assert "challenger" in challenged.tier_reason.lower()
    assert abs(sum(challenged.score_breakdown.model_dump().values()) - challenged.score) < 1e-6


def test_challenger_makes_one_pass_only(case) -> None:
    """It is invoked once; it does not loop back over its own conclusions."""
    attacks, res = _challenge(case, "[]")
    assert attacks == [] and res.status == STATUS_COMPLETED
    assert len(res.steps) == 0                 # this scripted pass used no tools
