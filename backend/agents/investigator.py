"""The Investigator — decides WHAT GETS INVESTIGATED, never the verdict.

Nine tools, Budget(10 calls, 3 cost points, 60s). Model: gpt-4o.

After the loop — REGARDLESS of agent status — the deterministic scorer rescores
over the (now richer) ledger and tiers.py assigns the tiers. If the agent did not
complete AND contributed no counterfactual/twin facts, the autopilot runs instead,
so a verdict always exists (rule 11).

    py -m backend.agents.investigator --case catalogue_cpu-1 --live
    OFFLINE=1 py -m backend.agents.investigator --case catalogue_cpu-1
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from backend.agents.budget import Budget
from backend.agents.harness import STATUS_COMPLETED, AgentResult, LLM, run_agent
from backend.agents.tools import ToolContext
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.models import RankedHypothesis
from backend.rank import autopilot as autopilot_mod
from backend.rank.rescore import counterfactual_components, rescore_from_ledger, twin_facts
from backend.rank.scorer import load_anomalies, rank

MODEL = "gpt-4o"
PROMPT_VERSION = "investigator-v1"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

INVESTIGATOR_TOOLS = [
    "get_anomalies", "get_candidates", "check_path", "get_topology_summary",
    "get_events", "get_ledger", "run_counterfactual", "run_twin", "file_finding",
]


@dataclass
class Investigation:
    result: AgentResult | None
    hypotheses: list[RankedHypothesis] = field(default_factory=list)
    used_autopilot: bool = False
    note: str = ""


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), undefined=StrictUndefined,
                       trim_blocks=True, lstrip_blocks=True)


def render_prompt(ctx: ToolContext, budget: Budget, candidates: list[RankedHypothesis]) -> str:
    return _env().get_template("investigator.j2").render(
        case_id=ctx.case_id,
        anomaly_count=len(ctx.anomalies),
        blast_size=len(ctx.blast.nodes),
        budget={"max_calls": budget.max_calls, "max_cost_points": budget.max_cost_points,
                "wall_clock_s": budget.wall_clock_s},
        candidates=[{"rank": h.rank, "suspect": h.suspect_component, "score": h.score,
                     "tier": h.tier, "fault": h.fault_type_guess,
                     "twin": h.twin.verdict if h.twin else None}
                    for h in candidates[:5]],
    )


def investigate(
    ctx: ToolContext,
    *,
    run_id: str,
    llm: LLM | None = None,
    budget: Budget | None = None,
    emit: Callable[[str, dict], None] | None = None,
    transcripts_dir: str | Path = "data/transcripts",
) -> AgentResult:
    budget = budget or Budget(max_calls=10, max_cost_points=3, wall_clock_s=60.0)
    candidates = ctx.ranked()
    system_prompt = render_prompt(ctx, budget, candidates)
    return run_agent(
        agent="investigator", model=MODEL, system_prompt=system_prompt,
        task=f"Investigate case {ctx.case_id} and file what you conclude.",
        tools=INVESTIGATOR_TOOLS, ctx=ctx, budget=budget, run_id=run_id,
        prompt_version=PROMPT_VERSION, llm=llm, transcripts_dir=transcripts_dir, emit=emit,
    )


def investigate_and_rescore(
    case_id: str,
    *,
    store_root: str | Path = "data/parquet",
    anomalies_dir: str | Path = "data/anomalies",
    ledger_dir: str | Path = "data/ledger",
    transcripts_dir: str | Path = "data/transcripts",
    run_id: str | None = None,
    llm: LLM | None = None,
    budget: Budget | None = None,
    emit: Callable[[str, dict], None] | None = None,
) -> Investigation:
    """Run the Investigator, then ALWAYS rescore deterministically from the ledger."""
    run_id = run_id or case_id
    store = EventStore(store_root)
    topology = store.load_topology(case_id)
    if topology is None:
        raise FileNotFoundError(f"no topology for case {case_id!r}")
    anomalies = load_anomalies(case_id, anomalies_dir)
    # a run starts with a CLEAN ledger: inheriting a previous run's facts would both
    # fake the "did this agent contribute?" check and drift the transcript cache key.
    ledger = Ledger(run_id, case_id, ledger_dir, fresh=True)
    ctx = ToolContext(case_id=case_id, store=store, topology=topology, anomalies=anomalies,
                      blast=blast_radius(topology, {a.component_id for a in anomalies}),
                      ledger=ledger)

    before_cf, before_twin = counterfactual_components(ledger), set(twin_facts(ledger))
    result = investigate(ctx, run_id=run_id, llm=llm, budget=budget, emit=emit,
                         transcripts_dir=transcripts_dir)

    # what the ledger GAINED from this agent (not what was already in it)
    contributed = bool((counterfactual_components(ledger) - before_cf)
                       or (set(twin_facts(ledger)) - before_twin))
    if result.status != STATUS_COMPLETED and not contributed:
        v = autopilot_mod.run(case_id, store_root=store_root, anomalies_dir=anomalies_dir,
                              ledger_dir=ledger_dir, run_id=run_id)
        return Investigation(result, v.hypotheses, used_autopilot=True,
                             note=f"agent status={result.status} with no expensive-check facts "
                                  f"-> deterministic autopilot")

    # regardless of agent status: the arithmetic decides
    hyps = rescore_from_ledger(case_id, anomalies, topology, store, ledger)
    return Investigation(result, hyps, used_autopilot=False,
                         note=f"agent status={result.status}; rescored from ledger")


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="agents.investigator")
    ap.add_argument("--case", required=True)
    ap.add_argument("--live", action="store_true", help="allow real API calls (needs OPENAI_API_KEY)")
    ap.add_argument("--store", default="data/parquet")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args(argv[1:])

    if args.live:
        import os
        os.environ["OFFLINE"] = "0"

    inv = investigate_and_rescore(args.case, store_root=args.store)
    r = inv.result
    print(f"case={args.case} agent_status={r.status} replayed={r.replayed} "
          f"autopilot={inv.used_autopilot}")
    print(f"transcript: {r.transcript_path}")
    print(f"tools used : {r.tools_used}")
    if r.error:
        print(f"error      : {r.error}")
    if r.final_text:
        print(f"final      : {r.final_text[:300]}")
    print(f"{'rank':>4}  {'suspect':<14} {'score':>6}  {'tier':<16} fault")
    for h in inv.hypotheses[: args.top]:
        print(f"{h.rank:>4}  {h.suspect_component:<14} {h.score:>6.3f}  {h.tier:<16} {h.fault_type_guess}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
