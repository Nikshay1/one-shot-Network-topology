"""Run orchestration.

    detect -> localize -> deterministic candidates+score -> INVESTIGATOR
           -> rescore+tiers -> CHALLENGER -> final verdict

`--fixed-pipeline` bypasses BOTH agents and runs the deterministic autopilot
(the eval ablation and the emergency demo mode).

Rule 11: the run always finishes. Rule 12: the agents shape the investigation;
the scorer and tiers.py deliver the verdict.

    py -m backend.pipeline --case catalogue_cpu-1
    py -m backend.pipeline --case catalogue_cpu-1 --fixed-pipeline
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from backend.agents.budget import Budget
from backend.agents.challenger import challenge
from backend.agents.investigator import investigate_and_rescore
from backend.agents.remediation import recommend as recommend_fix
from backend.agents.tools import ToolContext
from backend.narrate.narrator import narrate
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.models import RankedHypothesis
from backend.rank import autopilot as autopilot_mod
from backend.rank.rescore import (
    counterfactual_components as _cf_components,
    rescore_from_ledger,
    twin_facts as _twin_facts,
)
from backend.rank.scorer import load_anomalies

_STORE = "data/parquet"
_ANOM = Path("data/anomalies")
_LEDGER = Path("data/ledger")
_TRANSCRIPTS = Path("data/transcripts")


@dataclass
class RunVerdict:
    case_id: str
    run_id: str
    mode: str                                   # agentic | autopilot
    hypotheses: list[RankedHypothesis]
    ledger: Ledger
    investigator_status: str | None = None
    challenger_status: str | None = None
    challenger_attacks: list[dict] = field(default_factory=list)
    fallback_note: str = ""
    remediation: object | None = None            # RemediationReport
    narration: object | None = None              # Narration
    tool_calls: int = 0
    cost_points_spent: int = 0
    expensive_checks: list[str] = field(default_factory=list)
    transcripts: dict[str, str] = field(default_factory=dict)   # agent -> transcript path


def emit_ranking(emit: Callable[[str, dict], None] | None,
                 ranked: list[RankedHypothesis],
                 seen_tiers: dict[str, str]) -> None:
    """Emit the ranking stage's SSE events for the current hypothesis list.

    Contract: `hypothesis_ranked` is a FULL-OBJECT upsert keyed by hypothesis_id
    (a re-emit after the challenger rescore replaces the earlier one), and
    `tier_changed` is emitted ONLY from here — the ranking stage — and only when
    the tier actually moved (rule 5: tiers come from tiers.py alone).
    """
    if emit is None:
        return
    for h in ranked:
        if h.counterfactual.removed:
            emit("counterfactual_result", {
                "hypothesis_id": h.hypothesis_id, "removed": h.suspect_component,
                "anomalies_still_explained_pct": h.counterfactual.anomalies_still_explained_pct})
        if h.twin is not None:
            emit("twin_started", {"hypothesis_id": h.hypothesis_id, "run": h.twin.run})
            emit("twin_result", {"hypothesis_id": h.hypothesis_id, "run": h.twin.run,
                                 "similarity": h.twin.similarity, "verdict": h.twin.verdict,
                                 "missing_evidence": h.twin.missing_evidence})
        emit("hypothesis_ranked", h.model_dump(mode="json"))
        if seen_tiers.get(h.hypothesis_id) != h.tier:
            seen_tiers[h.hypothesis_id] = h.tier
            emit("tier_changed", {"hypothesis_id": h.hypothesis_id, "tier": h.tier,
                                  "tier_reason": h.tier_reason})


def run(
    case_id: str,
    *,
    fixed_pipeline: bool = False,
    store_root: str | Path = _STORE,
    anomalies_dir: str | Path = _ANOM,
    ledger_dir: str | Path = _LEDGER,
    transcripts_dir: str | Path = _TRANSCRIPTS,
    run_id: str | None = None,
    twin_enabled: bool = True,
    counterfactual_enabled: bool = True,
    llm=None,
    challenger_llm=None,
    remediation_llm=None,
    narrator_llm=None,
    emit: Callable[[str, dict], None] | None = None,
) -> RunVerdict:
    run_id = run_id or case_id
    inv_status = ch_status = None
    inv_budget: Budget | None = None
    attacks: list[dict] = []
    note = ""
    seen_tiers: dict[str, str] = {}
    transcripts: dict[str, str] = {}

    if fixed_pipeline:                          # ablation: no reasoning agents at all
        v = autopilot_mod.run(case_id, store_root=store_root, anomalies_dir=anomalies_dir,
                              ledger_dir=ledger_dir, run_id=run_id,
                              counterfactual_enabled=counterfactual_enabled,
                              twin_enabled=twin_enabled,
                              twin_fn=None if twin_enabled else (lambda c, f: None))
        ranked, ledger, mode = v.hypotheses, v.ledger, "autopilot"
        note = "--fixed-pipeline: agents bypassed"
    else:
        # --- Investigator, then ALWAYS a deterministic rescore from the ledger ---
        inv_budget = Budget(max_calls=10, max_cost_points=3, wall_clock_s=60.0)
        inv = investigate_and_rescore(
            case_id, store_root=store_root, anomalies_dir=anomalies_dir, ledger_dir=ledger_dir,
            transcripts_dir=transcripts_dir, run_id=run_id, llm=llm, budget=inv_budget, emit=emit,
        )
        ranked, ledger = inv.hypotheses, None
        mode = "autopilot" if inv.used_autopilot else "agentic"
        inv_status, note = (inv.result.status if inv.result else None), inv.note
        if inv.result and inv.result.transcript_path:
            transcripts["investigator"] = inv.result.transcript_path

    store = EventStore(store_root)
    topology = store.load_topology(case_id)
    anomalies = load_anomalies(case_id, anomalies_dir)
    ledger = Ledger(run_id, case_id, ledger_dir)      # the run's ledger, as it now stands
    ctx = ToolContext(case_id=case_id, store=store, topology=topology, anomalies=anomalies,
                      blast=blast_radius(topology, {a.component_id for a in anomalies}),
                      ledger=ledger)

    emit_ranking(emit, ranked, seen_tiers)

    # --- Challenger: one pass against the top hypothesis ---
    if not fixed_pipeline and ranked:
        attacks, cres = challenge(ctx, ranked[0], run_id=run_id, llm=challenger_llm,
                                  emit=emit, transcripts_dir=transcripts_dir,
                                  budget=Budget(max_calls=5, max_cost_points=0, wall_clock_s=30.0))
        ch_status = cres.status
        if cres.transcript_path:
            transcripts["challenger"] = cres.transcript_path
        if attacks:                             # upheld attacks -> -0.1 each + tier re-eval
            ranked = rescore_from_ledger(case_id, anomalies, topology, store, ledger,
                                         {ranked[0].hypothesis_id: attacks})
            emit_ranking(emit, ranked, seen_tiers)      # full-object upsert, tiers re-checked

    # --- Fix-Rehearsal, then the Narrator LAST (so it can cite remediation facts) ---
    remediation = recommend_fix(ctx, ranked, run_id=run_id, llm=remediation_llm, emit=emit,
                                transcripts_dir=transcripts_dir)
    if getattr(remediation, "transcript_path", None):
        transcripts["remediation"] = remediation.transcript_path
    narration = narrate(ctx, ranked, remediation, run_id=run_id, llm=narrator_llm, emit=emit,
                        transcripts_dir=transcripts_dir)

    expensive = ([f"counterfactual:{c}" for c in sorted(_cf_components(ledger))]
                 + [f"twin:{c}" for c in sorted(_twin_facts(ledger))])
    return RunVerdict(case_id, run_id, mode, ranked, ledger,
                      investigator_status=inv_status, challenger_status=ch_status,
                      challenger_attacks=attacks, fallback_note=note,
                      remediation=remediation, narration=narration,
                      tool_calls=(inv_budget.calls if inv_budget else 0),
                      cost_points_spent=(inv_budget.cost if inv_budget else 0),
                      expensive_checks=expensive, transcripts=transcripts)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pipeline")
    ap.add_argument("--case", required=True)
    ap.add_argument("--fixed-pipeline", action="store_true",
                    help="bypass both agents -> deterministic autopilot (eval ablation)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--store", default=_STORE)
    ap.add_argument("--narration", action="store_true", help="print the full incident report")
    args = ap.parse_args(argv[1:])

    v = run(args.case, fixed_pipeline=args.fixed_pipeline, store_root=args.store)
    print(f"case={v.case_id} mode={v.mode} investigator={v.investigator_status} "
          f"challenger={v.challenger_status} attacks={len(v.challenger_attacks)}")
    if v.fallback_note:
        print(f"note: {v.fallback_note}")
    print(f"{'rank':>4}  {'suspect':<14} {'score':>6}  {'tier':<16} fault")
    for h in v.hypotheses[: args.top]:
        print(f"{h.rank:>4}  {h.suspect_component:<14} {h.score:>6.3f}  {h.tier:<16} {h.fault_type_guess}")
    if v.remediation is not None:
        rec = v.remediation.recommended
        print(f"remediation: status={v.remediation.status} "
              f"recommended={rec.remedy if rec else 'none'} "
              f"rehearsals={len(v.remediation.rehearsals)}"
              + (f" caveat={v.remediation.caveat}" if v.remediation.caveat else ""))
    if v.narration is not None:
        print(f"narration: mode={v.narration.mode} citations={len(v.narration.citations)} "
              f"valid={v.narration.citations_valid} attempts={v.narration.attempts}")
        if args.narration:
            print("\n" + v.narration.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
