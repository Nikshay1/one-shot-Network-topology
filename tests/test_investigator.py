"""The Investigator: it decides what gets investigated; the scorer decides the verdict.

The centrepiece is `test_agent_spend_flips_rank2_to_rank1`: the agent spends its
twin on the rank-2 candidate and its counterfactual on rank-1, and after the
evidence lands the deterministic rescore promotes rank-2 to rank-1. The agent
never wrote a score.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.budget import Budget
from backend.agents.harness import STATUS_BUDGET, STATUS_COMPLETED, STATUS_ERROR, LLMDecision, ScriptedLLM
from backend.agents.investigator import (
    INVESTIGATOR_TOOLS,
    investigate_and_rescore,
    render_prompt,
)
from backend.agents.tools import REGISTRY, ToolContext
from backend.detect import AnomalyBuilder
from backend.ingest.re2ss_adapter import CaseBundle, build_topology
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.overlay.config_overlay import EventFactory
from backend.rank.scorer import rank

CASE = "inv1"


@pytest.fixture
def case(tmp_path: Path):
    """A 2-candidate case where rank-1 (catalogue) is REDUNDANT: its anomalies are
    covered by catalogue-db's reachable set, so a counterfactual on it discounts it
    below rank-2."""
    topo = build_topology({"front-end", "catalogue", "catalogue-db"})
    f = EventFactory(CASE)
    e1 = f.metric("catalogue", "cpu", 95.0, 100.0, unit="ratio")
    e2 = f.log("catalogue", "connection refused", 101.0)
    e3 = f.alert("catalogue", "HighCPU", 0.9, 102.0)
    e4 = f.config("catalogue", "cpu_quota", 100000, 20000, True, 99.0)
    e5 = f.metric("catalogue-db", "latency-90", 0.9, 200.0, unit="s")

    store = EventStore(tmp_path / "p")
    store.write_case(CaseBundle(case_id=CASE, events=f.events, topology=topo, inject_time=100.0))
    store.write_topology(CASE, topo)

    b = AnomalyBuilder(CASE)
    b.make("catalogue", "metric", "mad_zscore", 100, 300, 1.0, [e1.event_id], "catalogue cpu |z|=99")
    b.make("catalogue", "log", "log_rare_template", 101, 300, 0.9, [e2.event_id], "catalogue connection refused")
    b.make("catalogue", "alert", "alert_dedup", 102, 300, 0.9, [e3.event_id], "catalogue HighCPU firing")
    b.make("catalogue", "config", "config_risky_flag", 99, 99, 0.8, [e4.event_id], "risky cpu_quota change")
    b.make("catalogue-db", "metric", "mad_zscore", 200, 300, 1.0, [e5.event_id], "catalogue-db latency |z|=40")

    anom_dir = tmp_path / "anom"
    anom_dir.mkdir()
    (anom_dir / f"{CASE}.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in b.anomalies]), encoding="utf-8")

    return {"tmp": tmp_path, "store": store, "topo": topo, "anomalies": b.anomalies,
            "anom_dir": anom_dir, "evidence": e1.event_id}


def _dirs(case, ledger="l"):
    return dict(store_root=case["tmp"] / "p", anomalies_dir=case["anom_dir"],
                ledger_dir=case["tmp"] / ledger, transcripts_dir=case["tmp"] / "t")


# --------------------------- wiring ---------------------------
def test_investigator_gets_exactly_the_nine_tools() -> None:
    assert INVESTIGATOR_TOOLS == [
        "get_anomalies", "get_candidates", "check_path", "get_topology_summary",
        "get_events", "get_ledger", "run_counterfactual", "run_twin", "file_finding"]
    assert all(t in REGISTRY for t in INVESTIGATOR_TOOLS)


def test_prompt_contract_renders(case) -> None:
    ctx = ToolContext(case_id=CASE, store=case["store"], topology=case["topo"],
                      anomalies=case["anomalies"],
                      blast=blast_radius(case["topo"], {"catalogue"}),
                      ledger=Ledger(CASE, CASE, case["tmp"] / "lp"))
    text = render_prompt(ctx, Budget(max_calls=10, max_cost_points=3, wall_clock_s=60),
                         rank(CASE, case["anomalies"], case["topo"]))
    assert "STARTING POINT, not your conclusion" in text
    assert "You do NOT decide the verdict" in text
    assert "DISCRIMINATE" in text
    assert "file_finding EVERY conclusion" in text
    assert "Declaring ambiguity is a correct outcome" in text
    assert "run_twin costs 2 points" in text
    assert "#1 catalogue" in text                  # the deterministic ranking is shown


# --------------------------- THE CENTREPIECE ---------------------------
def test_agent_spend_flips_rank2_to_rank1(case) -> None:
    floor = rank(CASE, case["anomalies"], case["topo"], store=case["store"])
    assert [h.suspect_component for h in floor[:2]] == ["catalogue", "catalogue-db"]
    rank1, rank2 = floor[0].suspect_component, floor[1].suspect_component

    # the agent decides where to spend: counterfactual on rank-1, twin on rank-2
    llm = ScriptedLLM([
        LLMDecision(tool="get_candidates", args={}),
        LLMDecision(tool="run_counterfactual", args={"component": rank1}),
        LLMDecision(tool="run_twin", args={"component": rank2, "fault_type": "delay"}),
        LLMDecision(tool="file_finding", args={
            "kind": "investigation_note",
            "statement": f"{rank1} looks redundant; {rank2} reproduces in the twin",
            "component_ids": [rank1], "event_ids": [case["evidence"]]}),
        LLMDecision(final="spent the twin on the rank-2 candidate"),
    ])
    inv = investigate_and_rescore(CASE, run_id=CASE, llm=llm, **_dirs(case))

    assert inv.result.status == STATUS_COMPLETED
    assert inv.used_autopilot is False
    assert "run_twin" in inv.result.tools_used and "run_counterfactual" in inv.result.tools_used

    # ...and the SCORER promoted rank-2 to rank-1
    assert inv.hypotheses[0].suspect_component == rank2, \
        [(h.rank, h.suspect_component, h.score) for h in inv.hypotheses]
    assert inv.hypotheses[1].suspect_component == rank1
    assert inv.hypotheses[0].twin is not None and inv.hypotheses[0].twin.verdict in (
        "match", "partial", "mismatch")

    # the demoted rank-1 was discounted by its counterfactual, not by the agent's say-so
    demoted = inv.hypotheses[1]
    assert demoted.counterfactual.removed is True
    assert demoted.score < floor[0].score
    for h in inv.hypotheses:                      # arithmetic stayed consistent
        assert abs(sum(h.score_breakdown.model_dump().values()) - h.score) < 1e-6


# --------------------------- rescore always runs ---------------------------
def test_rescore_runs_even_when_budget_exhausted(case) -> None:
    floor = rank(CASE, case["anomalies"], case["topo"], store=case["store"])
    rank1 = floor[0].suspect_component
    # 2 calls allowed; the 3rd trips the budget AFTER a counterfactual landed
    llm = ScriptedLLM([
        LLMDecision(tool="get_candidates", args={}),
        LLMDecision(tool="run_counterfactual", args={"component": rank1}),
        LLMDecision(tool="get_anomalies", args={}),
    ])
    inv = investigate_and_rescore(CASE, run_id=CASE, llm=llm,
                                  budget=Budget(max_calls=2, max_cost_points=3),
                                  **_dirs(case, ledger="l2"))
    assert inv.result.status == STATUS_BUDGET
    assert inv.used_autopilot is False            # it contributed a counterfactual fact
    assert inv.hypotheses                          # rescored anyway
    assert any(h.counterfactual.removed for h in inv.hypotheses)


# --------------------------- fallback (rule 11) ---------------------------
def test_llm_raises_still_produces_verdict_via_autopilot(case) -> None:
    class Boom:
        def decide(self, messages, specs):
            raise RuntimeError("openai is down")

    inv = investigate_and_rescore(CASE, run_id=CASE, llm=Boom(), **_dirs(case, ledger="l3"))
    assert inv.result.status == STATUS_ERROR
    assert inv.used_autopilot is True             # ...and the run still finished
    assert inv.hypotheses and inv.hypotheses[0].rank == 1
    assert "autopilot" in inv.note                # run status notes the fallback


def test_completed_but_lazy_agent_rescores_without_autopilot(case) -> None:
    """The fallback rule is (status != completed) AND (no expensive-check facts).
    An agent that finishes cleanly but buys nothing is not a failure — the
    deterministic rescore simply reproduces the floor."""
    floor = rank(CASE, case["anomalies"], case["topo"], store=case["store"])
    llm = ScriptedLLM([LLMDecision(tool="get_candidates", args={}),
                       LLMDecision(final="the free evidence already discriminates")])
    inv = investigate_and_rescore(CASE, run_id=CASE, llm=llm, **_dirs(case, ledger="l4"))
    assert inv.result.status == STATUS_COMPLETED
    assert inv.used_autopilot is False
    assert [h.suspect_component for h in inv.hypotheses] == \
           [h.suspect_component for h in floor]          # unchanged: it bought nothing
    assert not any(h.counterfactual.removed for h in inv.hypotheses)


def test_incomplete_agent_with_no_facts_falls_back_to_autopilot(case) -> None:
    """status != completed AND no counterfactual/twin facts -> autopilot."""
    llm = ScriptedLLM([LLMDecision(tool="get_candidates", args={}),
                       LLMDecision(tool="get_anomalies", args={})])
    inv = investigate_and_rescore(CASE, run_id=CASE, llm=llm,
                                  budget=Budget(max_calls=1, max_cost_points=3),
                                  **_dirs(case, ledger="l6"))
    assert inv.result.status == STATUS_BUDGET
    assert inv.used_autopilot is True and inv.hypotheses
    assert "autopilot" in inv.note


def test_uncitable_conclusion_is_rejected(case) -> None:
    llm = ScriptedLLM([
        LLMDecision(tool="run_counterfactual", args={"component": "catalogue"}),
        LLMDecision(tool="file_finding", args={
            "kind": "investigation_note", "statement": "vibes say catalogue",
            "component_ids": ["catalogue"], "event_ids": ["metric-fake-000001"]}),
        LLMDecision(final="done"),
    ])
    inv = investigate_and_rescore(CASE, run_id=CASE, llm=llm, **_dirs(case, ledger="l5"))
    filing = [s for s in inv.result.steps if s.tool == "file_finding"][0]
    assert json.loads(filing.result_summary)["error"] == "unresolved_event_id"
