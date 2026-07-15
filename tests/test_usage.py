"""The spend meter, the spend cap, and the guarantee that tests never bill anyone."""

from __future__ import annotations

import os

import pytest

from backend.agents import usage
from backend.agents.harness import LLMDecision, record_turn


# =========================================================================
# guard the guard
# =========================================================================
def test_the_suite_cannot_spend_money() -> None:
    """conftest strips the key before collection. If this ever fails, every other
    test in the suite is potentially issuing billed API calls."""
    assert os.getenv("OPENAI_API_KEY") is None


# =========================================================================
# metering
# =========================================================================
def test_price_uses_real_rates() -> None:
    # 1M input tokens of gpt-4o == $2.50 exactly
    assert usage.price("gpt-4o", 1_000_000, 0) == pytest.approx(2.50)
    assert usage.price("gpt-4o", 0, 1_000_000) == pytest.approx(10.00)
    assert usage.price("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)


def test_unknown_model_prices_at_the_expensive_rate() -> None:
    """A typo must never make a run look cheaper than it was."""
    assert usage.price("gpt-4o-typo", 1_000_000, 0) == usage.price("gpt-4o", 1_000_000, 0)


def test_meter_accumulates_per_model() -> None:
    m = usage.Meter()
    m.record("gpt-4o", 1000, 100)
    m.record("gpt-4o-mini", 2000, 200)
    m.record("gpt-4o", 1000, 100)
    snap = m.snapshot()
    assert snap["calls"] == 3
    assert snap["by_model"]["gpt-4o"]["calls"] == 2
    assert snap["by_model"]["gpt-4o"]["prompt_tokens"] == 2000
    assert snap["usd"] == pytest.approx(
        usage.price("gpt-4o", 2000, 200) + usage.price("gpt-4o-mini", 2000, 200))


# =========================================================================
# the cap
# =========================================================================
def test_no_cap_never_raises() -> None:
    m = usage.Meter(cap_usd=None)
    m.record("gpt-4o", 10_000_000, 0)          # $25
    m.check()                                   # must not raise


def test_cap_raises_once_spent() -> None:
    m = usage.Meter(cap_usd=0.01)
    m.check()                                   # nothing spent yet
    m.record("gpt-4o", 10_000, 0)               # $0.025 — past the ceiling
    with pytest.raises(usage.SpendCap) as exc:
        m.check()
    assert exc.value.cap == 0.01


def test_cap_is_checked_before_the_call_not_after() -> None:
    """A request already sent is a request already paid for. The cap has to bite
    on the NEXT call, which means `check()` must be callable with zero usage and
    must raise strictly on the basis of what is already spent."""
    m = usage.Meter(cap_usd=0.0)
    with pytest.raises(usage.SpendCap):
        m.check()                               # $0 spent, $0 allowed -> refuse


# =========================================================================
# the tool-call protocol (the "calling get_anomalies" bug)
# =========================================================================
def test_record_turn_uses_the_real_tool_protocol() -> None:
    """A real assistant turn must be echoed verbatim and answered by a role:"tool"
    message carrying the SAME tool_call_id — not paraphrased into prose."""
    msgs: list[dict] = []
    raw = {"role": "assistant",
           "tool_calls": [{"id": "call_abc", "type": "function",
                           "function": {"name": "get_anomalies", "arguments": "{}"}}]}
    d = LLMDecision(tool="get_anomalies", args={}, raw_message=raw, tool_call_id="call_abc")
    record_turn(msgs, d, "get_anomalies", "3 anomalies")

    assert msgs[0] is raw
    assert msgs[1] == {"role": "tool", "tool_call_id": "call_abc", "content": "3 anomalies"}
    # the prose that taught gpt-4o to answer "calling get_anomalies" must be gone
    assert not any(m.get("content") == "calling get_anomalies" for m in msgs)


def test_record_turn_answers_every_tool_call_id() -> None:
    """Every assistant tool_call needs exactly one tool reply or the next request
    400s. Error paths append too, so the pairing must hold there as well."""
    msgs: list[dict] = []
    for i in range(3):
        raw = {"role": "assistant",
               "tool_calls": [{"id": f"call_{i}", "type": "function",
                               "function": {"name": "get_events", "arguments": "{}"}}]}
        d = LLMDecision(tool="get_events", args={}, raw_message=raw, tool_call_id=f"call_{i}")
        record_turn(msgs, d, "get_events", f"error: boom {i}")   # the failure path

    issued = [tc["id"] for m in msgs if m.get("role") == "assistant" for tc in m["tool_calls"]]
    answered = [m["tool_call_id"] for m in msgs if m.get("role") == "tool"]
    assert issued == answered == ["call_0", "call_1", "call_2"]


def test_record_turn_keeps_prose_for_offline_backends() -> None:
    """ScriptedLLM/ReplayLLM never round-trip to an API, so the protocol does not
    apply — and their history must stay readable."""
    msgs: list[dict] = []
    record_turn(msgs, LLMDecision(tool="get_events", args={}), "get_events", "2 events")
    assert [m["role"] for m in msgs] == ["assistant", "user"]
    assert not any("tool_call_id" in m for m in msgs)


# =========================================================================
# spending parity with the baseline (the confounded-benchmark bug)
# =========================================================================
def test_investigator_may_spend_exactly_what_the_autopilot_spends() -> None:
    """The benchmark compares the agent against the autopilot. If the agent's ceiling is
    below what the autopilot spends, "the agent spent less" is not a result — it is the
    cap, reported as a discovery. It was 3 against the autopilot's 7 for the whole of
    stage 11's measurement, and `confounded_pair` (0/4) is separated only by the 5-point
    counterfactual sweep the agent could not afford.
    """
    from backend.agents.investigator import autopilot_spend, default_budget

    assert default_budget().max_cost_points == autopilot_spend(), \
        "the agent cannot spend what it is benchmarked against — the comparison is rigged"


def test_autopilot_spend_is_derived_not_restated() -> None:
    """It must track `_CF_TOP_K` and the real tool costs, so parity survives someone
    tuning the autopilot. A hardcoded 7 would drift and the drift would read as a
    finding."""
    from backend.agents.tools import REGISTRY
    from backend.agents.investigator import autopilot_spend
    from backend.rank.autopilot import _CF_TOP_K

    expected = _CF_TOP_K * REGISTRY["run_counterfactual"].cost + REGISTRY["run_twin"].cost
    assert autopilot_spend() == expected == 7

    # and it genuinely follows the constant rather than coincidentally equalling it
    import backend.rank.autopilot as ap
    original = ap._CF_TOP_K
    try:
        ap._CF_TOP_K = 9
        assert autopilot_spend() == 9 * 1 + 2, "autopilot_spend() ignores _CF_TOP_K"
    finally:
        ap._CF_TOP_K = original


def test_the_other_caps_do_not_silently_become_the_binding_one() -> None:
    """Raising cost points achieves nothing if max_calls binds instead: spending 7 points
    needs >= 6 expensive calls, plus orientation calls on top."""
    b = default_budget_or_skip()
    min_expensive_calls = 6                      # 5 counterfactuals + 1 twin
    assert b.max_calls > min_expensive_calls + 4, \
        f"max_calls={b.max_calls} binds before the cost-point budget can be spent"
    assert b.wall_clock_s >= 120, \
        f"wall_clock_s={b.wall_clock_s} binds first: this budget is mostly LLM latency"


def default_budget_or_skip():
    from backend.agents.investigator import default_budget
    return default_budget()
