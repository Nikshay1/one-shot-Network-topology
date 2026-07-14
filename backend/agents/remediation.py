"""The Fix-Rehearsal agent — it tests the cure on a simulation before production.

Runs ONLY when the verdict is strong enough: a hypothesis reached CONFIRMED, or the
top-1 is CORRELATED with a twin verdict of partial or better.

Tools: get_verdict_summary (0), list_remedies (0), rehearse_fix (1, wired to
twin.remedies.rehearse), file_finding (0, kind=remediation_result).
Budget(max_calls=6, max_cost_points=3, wall=45s). Model: gpt-4o-mini.

The agent chooses WHICH remedies deserve a rehearsal; the RECOMMENDATION is
arithmetic — best symptoms_cleared_pct, then time_to_recover. If nothing clears
>50%, the fix is declared uncertain and human review is recommended.

    py -m backend.agents.remediation --case red_herring_config-01 --live
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from backend.agents.budget import Budget, BudgetExceeded
from backend.agents.harness import AgentResult, LLM, run_agent
from backend.agents.tools import ToolContext, call_tool
from backend.models import RankedHypothesis
from backend.twin.remedies import REMEDIES

MODEL = "gpt-4o-mini"
PROMPT_VERSION = "remediation-v1"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
REMEDIATION_TOOLS = ["get_verdict_summary", "list_remedies", "rehearse_fix", "file_finding"]

CLEARED_THRESHOLD = 50.0
UNCERTAIN_CAVEAT = ("fix uncertain — no rehearsed remedy cleared more than 50% of the "
                    "simulated symptoms; recommend human review")


@dataclass
class RecoveryReport:
    remedy: str
    symptoms_cleared_pct: float
    sim_time_to_recover_s: float
    residual_symptoms: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    fact_id: str | None = None


@dataclass
class RemediationReport:
    status: str                                   # ok | uncertain | skipped | error
    case_id: str
    hypothesis_id: str | None = None
    component: str | None = None
    fault_type: str | None = None
    recommended: RecoveryReport | None = None
    alternatives: list[RecoveryReport] = field(default_factory=list)
    rehearsals: list[RecoveryReport] = field(default_factory=list)
    caveat: str = ""
    agent_status: str | None = None
    transcript_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), undefined=StrictUndefined,
                       trim_blocks=True, lstrip_blocks=True)


def eligible(hypotheses: list[RankedHypothesis]) -> RankedHypothesis | None:
    """CONFIRMED anywhere, or a top-1 CORRELATED with twin partial+."""
    if not hypotheses:
        return None
    for h in hypotheses:
        if h.tier == "CONFIRMED":
            return h
    top = hypotheses[0]
    if top.tier == "CORRELATED" and top.twin is not None and top.twin.verdict in ("match", "partial"):
        return top
    return None


def render_prompt(case_id: str, hypothesis: RankedHypothesis, budget: Budget) -> str:
    return _env().get_template("remediation.j2").render(
        case_id=case_id, hypothesis=hypothesis,
        budget={"max_calls": budget.max_calls, "max_cost_points": budget.max_cost_points,
                "wall_clock_s": budget.wall_clock_s},
    )


def _to_report(payload: dict) -> RecoveryReport:
    return RecoveryReport(
        remedy=payload["remedy"], symptoms_cleared_pct=payload["symptoms_cleared_pct"],
        sim_time_to_recover_s=payload["sim_time_to_recover_s"],
        residual_symptoms=payload.get("residual_symptoms", []),
        side_effects=payload.get("side_effects", []), fact_id=payload.get("fact_id"),
    )


def _collect_rehearsals(result: AgentResult) -> list[RecoveryReport]:
    out: list[RecoveryReport] = []
    for s in result.steps:
        if s.tool != "rehearse_fix" or not s.ok:
            continue
        try:
            out.append(_to_report(json.loads(s.result_summary)))
        except (json.JSONDecodeError, KeyError):    # summary truncated at 200 chars
            continue
    return out


def _deterministic_rehearsals(ctx: ToolContext, hypothesis: RankedHypothesis,
                              budget: Budget, emit) -> list[RecoveryReport]:
    """No LLM: rehearse the catalogued remedies for the fault type, in order."""
    out: list[RecoveryReport] = []
    for remedy in [r.name for r in REMEDIES.get(hypothesis.fault_type_guess or "", [])][:3]:
        try:
            res = call_tool("rehearse_fix", {"component": hypothesis.suspect_component,
                                             "fault_type": hypothesis.fault_type_guess,
                                             "remedy": remedy}, ctx, budget=budget)
        except BudgetExceeded:
            break
        except Exception:
            continue
        rep = _to_report(res.model_dump(mode="json"))
        out.append(rep)
        if emit:
            emit("remediation_result", {"hypothesis_id": hypothesis.hypothesis_id,
                                        "remedy": rep.remedy,
                                        "symptoms_cleared_pct": rep.symptoms_cleared_pct,
                                        "sim_time_to_recover_s": rep.sim_time_to_recover_s})
    return out


def recommend(
    ctx: ToolContext,
    hypotheses: list[RankedHypothesis],
    *,
    run_id: str,
    llm: LLM | None = None,
    budget: Budget | None = None,
    emit: Callable[[str, dict], None] | None = None,
    transcripts_dir: str | Path = "data/transcripts",
) -> RemediationReport:
    target = eligible(hypotheses)
    if target is None:
        return RemediationReport(
            status="skipped", case_id=ctx.case_id,
            hypothesis_id=hypotheses[0].hypothesis_id if hypotheses else None,
            caveat="verdict not strong enough to rehearse a fix (needs CONFIRMED, or "
                   "top-1 CORRELATED with a twin match/partial)",
        )
    if not REMEDIES.get(target.fault_type_guess or ""):
        return RemediationReport(status="skipped", case_id=ctx.case_id,
                                 hypothesis_id=target.hypothesis_id,
                                 component=target.suspect_component,
                                 fault_type=target.fault_type_guess,
                                 caveat=f"no remedies catalogued for fault type "
                                        f"{target.fault_type_guess!r}")

    budget = budget or Budget(max_calls=6, max_cost_points=3, wall_clock_s=45.0)
    agent_status = None
    tpath: str | None = None
    if llm is not None or __import__("os").getenv("OPENAI_API_KEY") or _offline_cached(
            run_id, transcripts_dir):
        result = run_agent(
            agent="remediation", model=MODEL,
            system_prompt=render_prompt(ctx.case_id, target, budget),
            task=f"Rehearse fixes for {target.suspect_component} "
                 f"({target.fault_type_guess}) and recommend the best.",
            tools=REMEDIATION_TOOLS, ctx=ctx, budget=budget, run_id=run_id,
            prompt_version=PROMPT_VERSION, llm=llm, transcripts_dir=transcripts_dir, emit=emit,
        )
        agent_status = result.status
        tpath = result.transcript_path
        rehearsals = _collect_rehearsals(result)
        if emit:
            for r in rehearsals:
                emit("remediation_result", {"hypothesis_id": target.hypothesis_id,
                                            "remedy": r.remedy,
                                            "symptoms_cleared_pct": r.symptoms_cleared_pct,
                                            "sim_time_to_recover_s": r.sim_time_to_recover_s})
    else:
        rehearsals = []

    if not rehearsals:                              # no agent, or it bought nothing
        rehearsals = _deterministic_rehearsals(ctx, target, budget, emit)

    if not rehearsals:
        return RemediationReport(status="error", case_id=ctx.case_id,
                                 hypothesis_id=target.hypothesis_id,
                                 component=target.suspect_component,
                                 fault_type=target.fault_type_guess,
                                 caveat="no remedy could be rehearsed", agent_status=agent_status,
                                 transcript_path=tpath)

    # the recommendation is arithmetic: clearance first, then recovery time
    ordered = sorted(rehearsals, key=lambda r: (-r.symptoms_cleared_pct, r.sim_time_to_recover_s))
    best = ordered[0]
    if best.symptoms_cleared_pct <= CLEARED_THRESHOLD:
        return RemediationReport(status="uncertain", case_id=ctx.case_id,
                                 hypothesis_id=target.hypothesis_id,
                                 component=target.suspect_component,
                                 fault_type=target.fault_type_guess,
                                 recommended=None, alternatives=ordered, rehearsals=rehearsals,
                                 caveat=f"{UNCERTAIN_CAVEAT} (best: {best.remedy} at "
                                        f"{best.symptoms_cleared_pct:.0f}%)",
                                 agent_status=agent_status, transcript_path=tpath)

    caveat = ""
    if best.side_effects:
        caveat = f"side effects observed in the twin: {', '.join(best.side_effects)}"
    return RemediationReport(status="ok", case_id=ctx.case_id, hypothesis_id=target.hypothesis_id,
                             component=target.suspect_component,
                             fault_type=target.fault_type_guess, recommended=best,
                             alternatives=ordered[1:], rehearsals=rehearsals, caveat=caveat,
                             agent_status=agent_status, transcript_path=tpath)


def _offline_cached(run_id: str, transcripts_dir) -> bool:
    from backend.agents import transcript as tr
    return tr.offline()


def _main(argv: list[str]) -> int:
    from backend.ingest.store import EventStore
    from backend.ledger.ledger import Ledger
    from backend.localize.blast import blast_radius
    from backend.rank.scorer import load_anomalies
    from backend.pipeline import run as pipeline_run

    ap = argparse.ArgumentParser(prog="agents.remediation")
    ap.add_argument("--case", required=True)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--store", default="data/parquet")
    ap.add_argument("--anomalies", default="data/anomalies")
    args = ap.parse_args(argv[1:])
    if args.live:
        import os
        os.environ["OFFLINE"] = "0"

    v = pipeline_run(args.case, store_root=args.store, anomalies_dir=args.anomalies)
    store = EventStore(args.store)
    topology = store.load_topology(args.case)
    anomalies = load_anomalies(args.case, args.anomalies)
    ctx = ToolContext(case_id=args.case, store=store, topology=topology, anomalies=anomalies,
                      blast=blast_radius(topology, {a.component_id for a in anomalies}),
                      ledger=v.ledger)
    rep = recommend(ctx, v.hypotheses, run_id=args.case)
    print(f"case={args.case} status={rep.status} agent={rep.agent_status} "
          f"component={rep.component} fault={rep.fault_type}")
    print(f"rehearsals ({len(rep.rehearsals)}):")
    for r in rep.rehearsals:
        print(f"  {r.remedy:<20} cleared={r.symptoms_cleared_pct:5.0f}%  "
              f"recover={r.sim_time_to_recover_s:5.0f}s  side_effects={r.side_effects or 'none'}")
    print(f"recommended: {rep.recommended.remedy if rep.recommended else 'NONE'}")
    if rep.caveat:
        print(f"caveat: {rep.caveat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
