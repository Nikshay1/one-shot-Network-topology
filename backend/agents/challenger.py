"""The Challenger — adversarial review with the rhetoric surgically removed.

ONE pass, read-only 0-cost tools (get_ledger, get_events, check_path),
Budget(5 calls, 30s). Model: gpt-4o-mini. Runs AFTER the investigator + rescore,
against the top hypothesis.

The model emits attacks as {claim, contradicting_event_id}. CODE validates each:
the cited event must resolve AND must pertain (component or time overlap).
Invalid attacks are silently discarded. An attack is exactly as strong as its
citation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from backend.agents.budget import Budget
from backend.agents.harness import AgentResult, LLM, run_agent
from backend.agents.tools import ToolContext
from backend.localize.blast import reachable_upstream
from backend.models import RankedHypothesis

MODEL = "gpt-4o-mini"
PROMPT_VERSION = "challenger-v1"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
CHALLENGER_TOOLS = ["get_ledger", "get_events", "check_path"]   # read-only, 0 cost
_TIME_SKEW_S = 5.0


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), undefined=StrictUndefined,
                       trim_blocks=True, lstrip_blocks=True)


def render_prompt(ctx: ToolContext, hypothesis: RankedHypothesis, budget: Budget) -> str:
    return _env().get_template("challenger.j2").render(
        case_id=ctx.case_id, hypothesis=hypothesis,
        cited=hypothesis.cited_evidence_ids[:5],
        budget={"max_calls": budget.max_calls, "wall_clock_s": budget.wall_clock_s},
    )


def parse_attacks(final_text: str | None) -> list[dict]:
    if not final_text:
        return []
    m = re.search(r"\[.*\]", final_text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("claim") and item.get("contradicting_event_id"):
                out.append({"claim": str(item["claim"]),
                            "contradicting_event_id": str(item["contradicting_event_id"])})
    return out


def validate_attack(ctx: ToolContext, hypothesis: RankedHypothesis, attack: dict) -> bool:
    """Cited event must EXIST and PERTAIN (component or time overlap)."""
    df = ctx.store.get_by_ids([attack["contradicting_event_id"]])
    if df.height == 0:
        return False                                    # citation does not resolve
    row = df.row(0, named=True)
    comp, ts = row["component_id"], float(row["ts"])

    suspect = hypothesis.suspect_component
    if comp in ({suspect} | reachable_upstream(ctx.topology, suspect)):
        return True                                     # component overlap
    windows = [(a.window.start, a.window.end) for a in ctx.anomalies
               if a.component_id == suspect]
    return any(s - _TIME_SKEW_S <= ts <= e + _TIME_SKEW_S for s, e in windows)   # time overlap


def challenge(
    ctx: ToolContext,
    hypothesis: RankedHypothesis,
    *,
    run_id: str,
    llm: LLM | None = None,
    budget: Budget | None = None,
    emit: Callable[[str, dict], None] | None = None,
    transcripts_dir: str | Path = "data/transcripts",
) -> tuple[list[dict], AgentResult]:
    """One adversarial pass. Returns (validated attacks, agent result)."""
    budget = budget or Budget(max_calls=5, max_cost_points=0, wall_clock_s=30.0)
    result = run_agent(
        agent="challenger", model=MODEL,
        system_prompt=render_prompt(ctx, hypothesis, budget),
        task=f"Attack the leading hypothesis for {ctx.case_id} and return the JSON array.",
        tools=CHALLENGER_TOOLS, ctx=ctx, budget=budget, run_id=run_id,
        prompt_version=PROMPT_VERSION, llm=llm, transcripts_dir=transcripts_dir, emit=emit,
    )

    upheld: list[dict] = []
    for attack in parse_attacks(result.final_text):
        if not validate_attack(ctx, hypothesis, attack):
            continue                                    # silently discarded
        upheld.append({**attack, "upheld": True})
        if emit:
            emit("challenger_attack", {"hypothesis_id": hypothesis.hypothesis_id,
                                       "claim": attack["claim"],
                                       "contradicting_event_id": attack["contradicting_event_id"],
                                       "upheld": True})
    return upheld, result
