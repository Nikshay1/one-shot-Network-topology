"""LLM plumbing for narration: exactly ONE tool, bounded calls, one retry.

The narrator is a function-calling agent whose only tool is
`query_evidence_ledger` (<= 6 calls). This module owns the model interaction; the
citation contract and the report structure live in `narrator.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.agents.budget import Budget
from backend.agents.harness import AgentResult, LLM, run_agent
from backend.agents.tools import ToolContext

MODEL = "gpt-4o"
PROMPT_VERSION = "narrator-v1"
NARRATOR_TOOLS = ["query_evidence_ledger"]      # EXACTLY ONE
MAX_LEDGER_CALLS = 6


@dataclass
class Generation:
    text: str
    result: AgentResult | None


def generate(
    ctx: ToolContext,
    system_prompt: str,
    *,
    run_id: str,
    llm: LLM | None = None,
    emit: Callable[[str, dict], None] | None = None,
    transcripts_dir: str | Path = "data/transcripts",
    attempt: int = 1,
) -> Generation:
    """One narration attempt. Returns the raw model text (may be empty)."""
    budget = Budget(max_calls=MAX_LEDGER_CALLS, max_cost_points=0, wall_clock_s=60.0)
    result = run_agent(
        agent=f"narrator" if attempt == 1 else f"narrator-retry{attempt}",
        model=MODEL, system_prompt=system_prompt,
        task=(f"Write the incident report for case {ctx.case_id}. Query the ledger for "
              f"the facts you cite, then produce the markdown report."),
        tools=NARRATOR_TOOLS, ctx=ctx, budget=budget, run_id=run_id,
        prompt_version=f"{PROMPT_VERSION}-a{attempt}", llm=llm,
        transcripts_dir=transcripts_dir, emit=emit,
    )
    return Generation(text=(result.final_text or "").strip(), result=result)
