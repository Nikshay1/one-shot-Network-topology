"""The Fix-Rehearsal agent: gate, rehearsals, arithmetic recommendation, honesty."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.budget import Budget
from backend.agents.harness import LLMDecision, ScriptedLLM
from backend.agents.remediation import (
    CLEARED_THRESHOLD,
    REMEDIATION_TOOLS,
    RemediationReport,
    eligible,
    recommend,
    render_prompt,
)
from backend.agents.tools import REGISTRY, ToolContext, call_tool
from backend.detect import AnomalyBuilder
from backend.ingest.re2ss_adapter import CaseBundle, build_topology
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.models import Twin
from backend.overlay.config_overlay import EventFactory
from backend.rank.scorer import rank

CASE = "rem1"


@pytest.fixture
def case(tmp_path: Path):
    topo = build_topology({"loadgenerator", "front-end", "catalogue", "catalogue-db",
                           "carts", "carts-db", "orders", "orders-db"})
    f = EventFactory(CASE)
    ev = f.metric("carts", "cpu", 95.0, 100.0, unit="ratio")
    store = EventStore(tmp_path / "p")
    store.write_case(CaseBundle(case_id=CASE, events=f.events, topology=topo, inject_time=100.0))
    store.write_topology(CASE, topo)
    b = AnomalyBuilder(CASE)
    b.make("carts", "metric", "mad_zscore", 100, 300, 1.0, [ev.event_id], "carts cpu |z|=90")
    anomalies = b.anomalies
    ctx = ToolContext(case_id=CASE, store=store, topology=topo, anomalies=anomalies,
                      blast=blast_radius(topo, {"carts"}),
                      ledger=Ledger(CASE, CASE, tmp_path / "l"))
    hyps = rank(CASE, anomalies, topo, store=store)
    return {"ctx": ctx, "hyps": hyps, "tmp": tmp_path}


def _strong(hyps, verdict="partial", fault="cpu"):
    """Top-1 CORRELATED with a twin partial+ -> eligible."""
    top = hyps[0].model_copy(update={
        "twin": Twin(run="t", similarity=0.6, verdict=verdict, missing_evidence=[]),
        "fault_type_guess": fault, "suspect_component": "carts", "tier": "CORRELATED"})
    return [top] + list(hyps[1:])


# --------------------------- wiring ---------------------------
def test_tools_and_costs() -> None:
    assert REMEDIATION_TOOLS == ["get_verdict_summary", "list_remedies", "rehearse_fix",
                                 "file_finding"]
    assert REGISTRY["get_verdict_summary"].cost == 0
    assert REGISTRY["list_remedies"].cost == 0
    assert REGISTRY["rehearse_fix"].cost == 1          # the expensive one
    assert REGISTRY["file_finding"].cost == 0


def test_list_remedies_tool(case) -> None:
    out = call_tool("list_remedies", {"fault_type": "cpu"}, case["ctx"])
    assert out.remedies == ["restart", "scale_replicas", "throttle_upstream"]
    assert call_tool("list_remedies", {"fault_type": "nonsense"}, case["ctx"]).remedies == []


def test_rehearse_fix_tool_files_a_remediation_fact(case) -> None:
    out = call_tool("rehearse_fix", {"component": "carts", "fault_type": "cpu",
                                     "remedy": "restart"}, case["ctx"])
    assert out.status == "ok" and out.remedy == "restart"
    assert 0.0 <= out.symptoms_cleared_pct <= 100.0
    facts = case["ctx"].ledger.query(kind="remediation_result")
    assert any(f.fact_id == out.fact_id for f in facts)


def test_prompt_contract(case) -> None:
    text = render_prompt(CASE, _strong(case["hyps"])[0],
                         Budget(max_calls=6, max_cost_points=3, wall_clock_s=45))
    assert "2-3 PLAUSIBLE remediations" in text
    assert "symptoms_cleared_pct FIRST" in text
    assert "Report side effects HONESTLY" in text
    assert "human review" in text                      # the honest-uncertainty instruction
    assert "rehearse_fix costs 1 point" in text


# --------------------------- the gate ---------------------------
def test_gate_rejects_weak_verdicts(case) -> None:
    hyps = case["hyps"]                                # CORRELATED, twin=None at the floor
    assert eligible(hyps) is None
    rep = recommend(case["ctx"], hyps, run_id=CASE, transcripts_dir=case["tmp"] / "t")
    assert rep.status == "skipped" and "not strong enough" in rep.caveat
    assert rep.rehearsals == []


def test_gate_accepts_confirmed_and_top1_correlated_with_twin(case) -> None:
    hyps = case["hyps"]
    assert eligible(_strong(hyps, verdict="partial")) is not None      # CORRELATED + partial
    assert eligible(_strong(hyps, verdict="match")) is not None        # CORRELATED + match
    assert eligible(_strong(hyps, verdict="mismatch")) is None         # mismatch is not enough
    confirmed = [hyps[0].model_copy(update={"tier": "CONFIRMED"})]
    assert eligible(confirmed) is not None                              # CONFIRMED anywhere


# --------------------------- rehearsal + recommendation ---------------------------
def test_agent_rehearses_and_arithmetic_recommends(case) -> None:
    llm = ScriptedLLM([
        LLMDecision(tool="get_verdict_summary", args={}),
        LLMDecision(tool="list_remedies", args={"fault_type": "cpu"}),
        LLMDecision(tool="rehearse_fix", args={"component": "carts", "fault_type": "cpu",
                                               "remedy": "restart"}),
        LLMDecision(tool="rehearse_fix", args={"component": "carts", "fault_type": "cpu",
                                               "remedy": "scale_replicas"}),
        LLMDecision(final="restart looks best"),
    ])
    events: list[tuple] = []
    rep = recommend(case["ctx"], _strong(case["hyps"]), run_id=CASE, llm=llm,
                    emit=lambda e, d: events.append((e, d)),
                    transcripts_dir=case["tmp"] / "t")
    assert rep.status in ("ok", "uncertain")
    assert len(rep.rehearsals) == 2                    # 2-3 rehearsed, within budget
    if rep.status == "ok":
        assert rep.recommended.symptoms_cleared_pct > CLEARED_THRESHOLD
        # arithmetic: the winner is the best clearance (ties -> faster recovery)
        best = max(r.symptoms_cleared_pct for r in rep.rehearsals)
        assert rep.recommended.symptoms_cleared_pct == best
    assert [e for e, _ in events].count("remediation_result") == 2      # SSE per rehearsal
    assert case["ctx"].ledger.query(kind="remediation_result")          # facts filed


def test_deterministic_fallback_rehearses_without_an_llm(case) -> None:
    rep = recommend(case["ctx"], _strong(case["hyps"]), run_id=CASE,
                    transcripts_dir=case["tmp"] / "t")
    assert rep.status in ("ok", "uncertain")
    assert rep.rehearsals                              # catalog remedies rehearsed anyway
    assert rep.agent_status is None                    # no agent ran


def test_budget_caps_the_rehearsals(case) -> None:
    llm = ScriptedLLM([LLMDecision(tool="rehearse_fix", args={"component": "carts",
                                                              "fault_type": "cpu",
                                                              "remedy": r})
                       for r in ("restart", "scale_replicas", "throttle_upstream")]
                      + [LLMDecision(final="done")])
    rep = recommend(case["ctx"], _strong(case["hyps"]), run_id=CASE, llm=llm,
                    budget=Budget(max_calls=6, max_cost_points=1, wall_clock_s=45),
                    transcripts_dir=case["tmp"] / "t")
    assert len(rep.rehearsals) == 1                    # 1 cost point -> exactly 1 rehearsal


def test_uncertain_when_nothing_clears_half(case, monkeypatch) -> None:
    from backend.agents import remediation as rem

    class Weak:                                        # every rehearsal is a dud
        def __init__(self, *a, **k): ...
    def _weak(inp, ctx):
        from backend.agents.tools import RehearseFixOut
        fid = ctx.ledger.remediation_result("weak rehearsal", [inp.component], (0, 0), None)
        return RehearseFixOut(status="ok", remedy=inp.remedy, symptoms_cleared_pct=10.0,
                              sim_time_to_recover_s=30.0, residual_symptoms=["front-end"],
                              side_effects=[], fact_id=fid)
    monkeypatch.setitem(REGISTRY, "rehearse_fix",
                        REGISTRY["rehearse_fix"].__class__(
                            name="rehearse_fix", input_model=REGISTRY["rehearse_fix"].input_model,
                            output_model=REGISTRY["rehearse_fix"].output_model, fn=_weak, cost=1))
    rep = recommend(case["ctx"], _strong(case["hyps"]), run_id=CASE,
                    transcripts_dir=case["tmp"] / "t")
    assert rep.status == "uncertain"
    assert rep.recommended is None                     # it refuses to recommend
    assert "human review" in rep.caveat
    assert rep.alternatives                            # but reports what it tried
