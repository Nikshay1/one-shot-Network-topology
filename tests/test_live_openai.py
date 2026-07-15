"""The live OpenAI path. Opt-in only: `pytest tests/test_live_openai.py --live`.

These are the only tests in the suite that spend money, and they are deliberately
tiny — one gpt-4o-mini agent, a 3-call budget, one read-only 0-cost tool. Together
they cost well under a cent.

They exist because three things could only ever be proven with a real key, and all
three were wrong the first time one was used:

  * the harness speaks OpenAI's function-calling protocol (it did not — it narrated
    the conversation as prose and taught the model to answer "calling get_anomalies");
  * spend is metered and capped in code, per rule 10;
  * rule 13's transcript really does replay — every `data/transcripts/*.jsonl` on disk
    had zero steps, so "cached for OFFLINE demo replay" had never once been executed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents import usage
from backend.agents.budget import Budget
from backend.agents.harness import STATUS_COMPLETED, run_agent
from backend.agents.tools import ToolContext
from backend.detect import AnomalyBuilder
from backend.ingest.re2ss_adapter import CaseBundle, build_topology
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.overlay.config_overlay import EventFactory

pytestmark = pytest.mark.live

CASE = "live-1"
MODEL = "gpt-4o-mini"                       # the cheap one; this is real money
SYSTEM = ("You are a network incident investigator. Call get_topology_summary exactly "
          "once to see the topology, then reply with a one-sentence summary. Do not "
          "call any other tool.")


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    topo = build_topology({"front-end", "catalogue", "catalogue-db"})
    f = EventFactory(CASE)
    eid = f.metric("catalogue", "cpu", 95.0, 100.0, unit="ratio").event_id
    store = EventStore(tmp_path / "p")
    store.write_case(CaseBundle(case_id=CASE, events=f.events, topology=topo,
                                inject_time=100.0))
    store.write_topology(CASE, topo)
    b = AnomalyBuilder(CASE)
    b.make("catalogue", "metric", "mad_zscore", 100, 200, 1.0, [eid], "catalogue cpu")
    return ToolContext(case_id=CASE, store=store, topology=topo, anomalies=b.anomalies,
                       blast=blast_radius(topo, {a.component_id for a in b.anomalies}),
                       ledger=Ledger(CASE, CASE, tmp_path / "l"))


def _run(ctx, tmp_path, *, run_id, llm=None):
    return run_agent(
        agent="investigator", model=MODEL, system_prompt=SYSTEM,
        task="Summarise the topology for this case.",
        tools=["get_topology_summary"], ctx=ctx,
        budget=Budget(max_calls=3, max_cost_points=0, wall_clock_s=60.0),
        run_id=run_id, prompt_version="live-test-v1", llm=llm,
        transcripts_dir=tmp_path / "t",
    )


def test_live_agent_completes_and_uses_the_tool(ctx, tmp_path, live_key) -> None:
    """The end-to-end proof that the protocol is right.

    The assertion that matters is the last one. When the harness paraphrased tool
    calls into prose, gpt-4o echoed the paraphrase back as its answer and the agent
    "completed" having filed a final report reading `calling get_anomalies`.
    """
    res = _run(ctx, tmp_path, run_id="live-ok")

    assert res.status == STATUS_COMPLETED, f"live agent did not complete: {res.error}"
    assert res.tools_used == ["get_topology_summary"]
    assert res.final_text
    assert not res.final_text.startswith("calling "), \
        f"model echoed the harness's own placeholder as its answer: {res.final_text!r}"


def test_live_calls_are_metered(ctx, tmp_path, live_key) -> None:
    """Rule 10: budgets are enforced in code. Dollars are a budget."""
    meter = usage.Meter()
    before = meter.usd
    from backend.agents.harness import OpenAIClient
    _run(ctx, tmp_path, run_id="live-meter", llm=OpenAIClient(MODEL, meter=meter))

    snap = meter.snapshot()
    assert snap["calls"] >= 1, "a live run recorded no API calls"
    assert snap["prompt_tokens"] > 0 and meter.usd > before
    assert snap["by_model"][MODEL]["calls"] == snap["calls"]


def test_spend_cap_degrades_to_autopilot_rather_than_crashing(ctx, tmp_path, live_key) -> None:
    """Rule 11: an exhausted budget must still produce a well-formed result. A cap of
    $0 refuses the first call, so this asserts the shape of failure without buying
    anything."""
    from backend.agents.harness import OpenAIClient
    broke = usage.Meter(cap_usd=0.0)
    res = _run(ctx, tmp_path, run_id="live-cap", llm=OpenAIClient(MODEL, meter=broke))

    assert res.status != STATUS_COMPLETED
    assert "spend cap" in (res.error or "").lower()
    assert res.transcript_path, "a failed run must still write a transcript (rule 13)"
    assert broke.usd == 0.0, "the cap let a billed call through"


def test_transcript_replays_offline_with_zero_api_calls(ctx, tmp_path, monkeypatch,
                                                        live_key) -> None:
    """Rule 13, finally exercised.

    Record live, then replay the SAME run_id offline and assert the replay reproduces
    the recorded tool calls while spending nothing. Until a live key existed, every
    transcript on disk had zero steps, so `ReplayLLM` had never actually replayed one
    — which is precisely how the hollow-`completed` bug survived for a whole stage.
    """
    live = _run(ctx, tmp_path, run_id="live-replay")
    assert live.status == STATUS_COMPLETED and live.steps, "nothing worth replaying"
    recorded = [s.tool for s in live.steps]

    # now: no key at all, OFFLINE=1 -> the cache is the only possible source
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-would-explode-if-used")
    meter_before = usage.METER.usd
    replay = _run(ctx, tmp_path, run_id="live-replay")

    assert replay.replayed is True, "offline run did not replay the recorded transcript"
    assert [s.tool for s in replay.steps] == recorded
    assert usage.METER.usd == meter_before, "a 'replay' reached the network"
