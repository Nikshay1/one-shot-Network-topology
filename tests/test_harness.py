"""The bounded ReAct harness: budget, timeout, allow-list, transcripts, replay.

Every test scripts the LLM — zero API calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents import transcript as tr
from backend.agents.budget import Budget
from backend.agents.harness import (
    STATUS_BUDGET,
    STATUS_COMPLETED,
    STATUS_ERROR,
    AgentResult,
    LLMDecision,
    ScriptedLLM,
    run_agent,
)
from backend.agents.tools import REGISTRY, ToolContext
from backend.detect import AnomalyBuilder
from backend.ingest.re2ss_adapter import CaseBundle, build_topology
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.overlay.config_overlay import EventFactory

TOOLS = ["get_anomalies", "get_candidates", "check_path", "get_topology_summary",
         "get_events", "get_ledger", "run_counterfactual", "run_twin", "file_finding"]


@pytest.fixture
def ctx(tmp_path: Path):
    f = EventFactory("h1")
    ev = f.metric("catalogue", "cpu", 95.0, 100.0, unit="ratio")
    topo = build_topology({"front-end", "catalogue", "catalogue-db"})
    store = EventStore(tmp_path / "p")
    store.write_case(CaseBundle(case_id="h1", events=f.events, topology=topo, inject_time=100.0))
    store.write_topology("h1", topo)
    b = AnomalyBuilder("h1")
    b.make("catalogue", "metric", "mad_zscore", 100.0, 160.0, 1.0, [ev.event_id],
           "catalogue cpu |z|=99")
    anomalies = b.anomalies
    return ToolContext(case_id="h1", store=store, topology=topo, anomalies=anomalies,
                       blast=blast_radius(topo, {"catalogue"}),
                       ledger=Ledger("h1", "h1", tmp_path / "ledger")), tmp_path


def _run(ctx, tmp, decisions, budget=None, tools=None, emit=None):
    return run_agent(
        agent="investigator", model="gpt-4o", system_prompt="sys", task="task",
        tools=tools or TOOLS, ctx=ctx,
        budget=budget or Budget(max_calls=10, max_cost_points=3, wall_clock_s=60),
        run_id="h1", prompt_version="test-v1", llm=ScriptedLLM(decisions),
        transcripts_dir=tmp / "transcripts", emit=emit,
    )


# --------------------------- termination paths ---------------------------
def test_completes_on_final_message(ctx) -> None:
    c, tmp = ctx
    r = _run(c, tmp, [LLMDecision(tool="get_candidates", args={}),
                      LLMDecision(final="summary text")])
    assert isinstance(r, AgentResult)
    assert r.status == STATUS_COMPLETED and r.final_text == "summary text"
    assert r.tools_used == ["get_candidates"] and r.transcript_path


def test_budget_exhausted_on_max_calls(ctx) -> None:
    c, tmp = ctx
    r = _run(c, tmp, [LLMDecision(tool="get_candidates", args={}),
                      LLMDecision(tool="get_anomalies", args={})],
             budget=Budget(max_calls=1, max_cost_points=3))
    assert r.status == STATUS_BUDGET and len(r.steps) == 1     # still well-formed


def test_budget_exhausted_on_cost_points(ctx) -> None:
    c, tmp = ctx
    r = _run(c, tmp, [LLMDecision(tool="run_twin", args={"component": "catalogue",
                                                         "fault_type": "cpu"}),
                      LLMDecision(tool="run_twin", args={"component": "carts",
                                                         "fault_type": "cpu"})],
             budget=Budget(max_calls=10, max_cost_points=2))   # twin costs 2 -> 2nd trips
    assert r.status == STATUS_BUDGET


def test_timeout_is_budget_exhausted(ctx) -> None:
    c, tmp = ctx
    ticks = iter([0.0, 0.0, 500.0])            # start, first charge ok, second over wall
    b = Budget(max_calls=10, max_cost_points=3, wall_clock_s=1.0, clock=lambda: next(ticks))
    r = _run(c, tmp, [LLMDecision(tool="get_candidates", args={}),
                      LLMDecision(tool="get_anomalies", args={})], budget=b)
    assert r.status == STATUS_BUDGET and "wall_clock" in (r.error or "")


def test_llm_exception_returns_well_formed_error(ctx) -> None:
    c, tmp = ctx

    class Boom:
        def decide(self, messages, specs):
            raise RuntimeError("api exploded")

    r = run_agent(agent="investigator", model="gpt-4o", system_prompt="s", task="t",
                  tools=TOOLS, ctx=c, budget=Budget(), run_id="h1",
                  prompt_version="test-v1", llm=Boom(), transcripts_dir=tmp / "transcripts")
    assert r.status == STATUS_ERROR and "api exploded" in r.error
    assert r.transcript_path                                    # still wrote a transcript


def test_no_llm_available_is_well_formed(ctx) -> None:
    c, tmp = ctx
    r = run_agent(agent="investigator", model="gpt-4o", system_prompt="s", task="t",
                  tools=TOOLS, ctx=c, budget=Budget(), run_id="h1",
                  prompt_version="test-v1", llm=None, transcripts_dir=tmp / "transcripts")
    assert r.status == STATUS_ERROR and r.steps == [] and r.transcript_path


# --------------------------- isolation ---------------------------
def test_only_the_agents_tool_subset_is_exposed(ctx) -> None:
    c, tmp = ctx
    r = _run(c, tmp, [LLMDecision(tool="run_twin", args={"component": "catalogue",
                                                         "fault_type": "cpu"}),
                      LLMDecision(final="done")],
             tools=["get_ledger", "get_events", "check_path"])   # challenger subset
    assert r.status == STATUS_COMPLETED
    assert r.steps[0].ok is False and "not available" in r.steps[0].result_summary


def test_no_agent_may_call_another_agent() -> None:
    assert not any(n in ("investigate", "challenge", "run_agent") or "agent" in n
                   for n in REGISTRY)


def test_tool_error_does_not_kill_the_loop(ctx) -> None:
    c, tmp = ctx
    r = _run(c, tmp, [LLMDecision(tool="check_path", args={"src": "a"}),   # missing dst
                      LLMDecision(final="recovered")])
    assert r.status == STATUS_COMPLETED and any(not s.ok for s in r.steps)


# --------------------------- transcript + replay ---------------------------
def test_transcript_written_with_bounded_summaries(ctx) -> None:
    c, tmp = ctx
    r = _run(c, tmp, [LLMDecision(tool="get_candidates", args={}), LLMDecision(final="ok")])
    steps, status, final = tr.read(r.transcript_path)
    assert status == STATUS_COMPLETED and final == "ok"
    assert [s["tool"] for s in steps] == ["get_candidates"]
    for s in steps:
        assert len(s["result_summary"]) <= tr.SUMMARY_MAX
        assert "ts" in s and "args" in s


def test_cache_key_is_deterministic_and_ledger_bound(ctx) -> None:
    c, _ = ctx
    d0 = tr.ledger_digest(c.ledger)
    k1 = tr.cache_key("h1", d0, "v1")
    assert k1 == tr.cache_key("h1", d0, "v1")           # deterministic
    assert k1 != tr.cache_key("h1", d0, "v2")           # prompt version bound
    c.ledger.investigation_note("a new fact", ["catalogue"])
    assert tr.cache_key("h1", tr.ledger_digest(c.ledger), "v1") != k1   # ledger bound


def test_offline_replays_cached_transcript_with_zero_api_calls(ctx, monkeypatch) -> None:
    c, tmp = ctx
    live = _run(c, tmp, [LLMDecision(tool="get_candidates", args={}),
                         LLMDecision(tool="get_anomalies", args={}),
                         LLMDecision(final="cached summary")])
    assert live.status == STATUS_COMPLETED

    # OFFLINE=1 with no llm passed -> must replay the cache, never touch the API
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-would-explode-if-used")
    seen: list[tuple[str, dict]] = []
    replay = run_agent(agent="investigator", model="gpt-4o", system_prompt="sys", task="task",
                       tools=TOOLS, ctx=c, budget=Budget(), run_id="h1",
                       prompt_version="test-v1", llm=None,
                       transcripts_dir=tmp / "transcripts",
                       emit=lambda ev, data: seen.append((ev, data)))
    assert replay.replayed is True
    assert replay.status == STATUS_COMPLETED
    assert replay.tools_used == live.tools_used                # identical investigation
    assert [e for e, _ in seen].count("agent_step") == len(live.tools_used)  # same SSE


# =========================================================================
# A FAILED transcript is a record, not a script (found by scripts/harden.sh)
# =========================================================================
def test_a_failed_transcript_is_never_replayed(monkeypatch) -> None:
    """OFFLINE replay of an errored run used to fabricate a `completed` agent.

    The cached file holds `status=error` and zero steps. ReplayLLM ran out of
    decisions on call 1, returned a final, and the agent 'completed' having done
    nothing — so rule 11's autopilot never fired and the twin never ran.
    """
    from backend.agents.harness import STATUS_COMPLETED, replayable, resolve_llm

    monkeypatch.setenv("OFFLINE", "1")
    assert replayable("error", [], None) is False
    assert replayable(None, [], None) is False
    assert replayable(STATUS_COMPLETED, [], None) is False       # completed but nothing to replay
    assert replayable(STATUS_COMPLETED, [{"tool": "get_anomalies"}], None) is True

    # the SELF-PROPAGATING form: the hollow run was written back as completed with
    # the ReplayLLM's placeholder final, so the poison re-armed on every replay.
    assert replayable(STATUS_COMPLETED, [], "replayed") is False

    # the exact shape harden.sh hit: an errored transcript must NOT drive a replay
    llm, replayed = resolve_llm("investigator", "gpt-4o", None, [], None, "error")
    assert llm is None and replayed is False

    llm, replayed = resolve_llm("investigator", "gpt-4o", None,
                                [{"tool": "get_anomalies", "args": {}}], "done", STATUS_COMPLETED)
    assert llm is not None and replayed is True


def test_a_replayed_run_does_not_rewrite_its_own_transcript(ctx, monkeypatch) -> None:
    """Replay is a read path. Rewriting the recording is what let a hollow run
    persist itself as `completed` and poison every later replay."""
    ctx, tmp_path = ctx
    key = tr.cache_key("r1", tr.ledger_digest(ctx.ledger), "v1")
    path = tr.path_for(tmp_path / "t", "investigator", key)
    tr.write(path, "investigator",
             [tr.TranscriptStep(ts=0.0, tool="get_anomalies", args={}, result_summary="[]")],
             "completed", "done")
    before = path.read_text(encoding="utf-8")

    monkeypatch.setenv("OFFLINE", "1")
    res = run_agent(agent="investigator", model="gpt-4o", system_prompt="s", task="t",
                    tools=["get_anomalies"], ctx=ctx, budget=Budget(max_calls=5),
                    run_id="r1", prompt_version="v1", transcripts_dir=tmp_path / "t")
    assert res.replayed is True and res.status == "completed"
    assert path.read_text(encoding="utf-8") == before, "replay rewrote the recording"
