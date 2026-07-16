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
from backend.agents.investigator import default_budget, investigate_and_rescore
from backend.agents.remediation import recommend as recommend_fix
from backend.agents.tools import ToolContext
from backend.narrate.narrator import narrate
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.ledger.seed import seed_from_anomalies
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
    mode: str                                   # agentic | autopilot | ensemble
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
    # ensemble mode only: how the two engines voted, and what broke the tie.
    ensemble: dict | None = None
    mode_hypotheses: dict[str, list[RankedHypothesis]] = field(default_factory=dict)


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


@dataclass
class _EnsembleRun:
    hypotheses: list[RankedHypothesis]
    ledger: Ledger
    block: dict
    per_mode: dict[str, list[RankedHypothesis]]
    investigator_status: str | None
    note: str
    transcripts: dict[str, str]
    budget: Budget | None


def _run_ensemble(
    case_id: str,
    *,
    run_id: str,
    store_root,
    anomalies_dir,
    ledger_dir,
    transcripts_dir,
    llm,
    twin_enabled: bool,
    counterfactual_enabled: bool,
    weight: float | None,
    emit,
) -> _EnsembleRun:
    """Run both engines at once, then fuse their verdicts.

    The two ledgers are SEPARATE and that is the whole design, not tidiness: each
    mode's ranking is a rescore over its own ledger, so a shared one would let the
    autopilot's five counterfactuals leak into the agent's evidence and vice versa.
    They would stop being two opinions and become one, and there would be nothing
    left to ensemble.

    They also cannot share a Ledger object: fact ids are minted from an in-memory
    per-component counter, so two writers on one file mint the same id twice.
    """
    from concurrent.futures import ThreadPoolExecutor

    from backend.rank.ensemble import fuse, merge_derived_facts

    root = Path(ledger_dir) / "_modes" / run_id
    a_dir, f_dir = root / "agentic", root / "fixed"

    budget = default_budget()

    def _agentic():
        # emit is passed through: agent_step/agent_done drive the Agents tab. It does
        # NOT emit hypothesis_ranked — only the fused verdict may claim that channel,
        # or the UI would flicker between two rankings fighting over one upsert key.
        return investigate_and_rescore(
            case_id, store_root=store_root, anomalies_dir=anomalies_dir, ledger_dir=a_dir,
            transcripts_dir=transcripts_dir, run_id=run_id, llm=llm, budget=budget, emit=emit,
        )

    def _fixed():
        return autopilot_mod.run(
            case_id, store_root=store_root, anomalies_dir=anomalies_dir, ledger_dir=f_dir,
            run_id=run_id, counterfactual_enabled=counterfactual_enabled,
            twin_enabled=twin_enabled,
            twin_fn=None if twin_enabled else (lambda c, f: None),
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="verdict-ensemble") as ex:
        fut_a, fut_f = ex.submit(_agentic), ex.submit(_fixed)
        inv, vf = fut_a.result(), fut_f.result()

    store = EventStore(store_root)
    topology = store.load_topology(case_id)
    anomalies = load_anomalies(case_id, anomalies_dir)

    # The canonical ledger the narrator and chat read. Fresh, seeded with the
    # observations, then given every DERIVED fact both engines bought — re-filed, so
    # each gets a canonical id (both modes number facts per component from 0, so
    # copying ids verbatim would collide `fact-catalogue-0000` with itself).
    canonical = Ledger(run_id, case_id, ledger_dir, fresh=True)
    seed_from_anomalies(canonical, anomalies)
    merge_derived_facts(canonical, [Ledger(run_id, case_id, a_dir), Ledger(run_id, case_id, f_dir)])

    ens = fuse(inv.hypotheses, vf.hypotheses, anomalies=anomalies, topology=topology,
               store=store, ledger=canonical, weight=weight)

    transcripts: dict[str, str] = {}
    if inv.result and inv.result.transcript_path:
        transcripts["investigator"] = inv.result.transcript_path

    note = (f"ensemble: agentic({'autopilot-fallback' if inv.used_autopilot else 'live'}) "
            f"+ fixed -> {ens.reason}")
    return _EnsembleRun(
        hypotheses=ens.hypotheses, ledger=canonical, block=ens.to_dict(),
        per_mode={"agentic": inv.hypotheses, "fixed": vf.hypotheses},
        investigator_status=(inv.result.status if inv.result else None),
        note=note, transcripts=transcripts, budget=budget,
    )


def run(
    case_id: str,
    *,
    fixed_pipeline: bool = False,
    mode: str | None = None,
    ensemble_weight: float | None = None,
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
    ensemble_block: dict | None = None
    mode_hyps: dict[str, list[RankedHypothesis]] = {}

    if mode == "ensemble":
        ens = _run_ensemble(
            case_id, run_id=run_id, store_root=store_root, anomalies_dir=anomalies_dir,
            ledger_dir=ledger_dir, transcripts_dir=transcripts_dir, llm=llm,
            twin_enabled=twin_enabled, counterfactual_enabled=counterfactual_enabled,
            weight=ensemble_weight, emit=emit,
        )
        ranked, ledger = ens.hypotheses, ens.ledger
        ensemble_block, mode_hyps = ens.block, ens.per_mode
        mode_label = "ensemble"
        inv_status, note = ens.investigator_status, ens.note
        transcripts.update(ens.transcripts)
        inv_budget = ens.budget
    elif fixed_pipeline:                        # ablation: no reasoning agents at all
        v = autopilot_mod.run(case_id, store_root=store_root, anomalies_dir=anomalies_dir,
                              ledger_dir=ledger_dir, run_id=run_id,
                              counterfactual_enabled=counterfactual_enabled,
                              twin_enabled=twin_enabled,
                              twin_fn=None if twin_enabled else (lambda c, f: None))
        ranked, ledger, mode_label = v.hypotheses, v.ledger, "autopilot"
        note = "--fixed-pipeline: agents bypassed"
    else:
        # --- Investigator, then ALWAYS a deterministic rescore from the ledger ---
        # One source of truth for this budget: it must stay at spending parity with the
        # autopilot, and a second copy here is exactly how it fell out of parity before.
        inv_budget = default_budget()
        inv = investigate_and_rescore(
            case_id, store_root=store_root, anomalies_dir=anomalies_dir, ledger_dir=ledger_dir,
            transcripts_dir=transcripts_dir, run_id=run_id, llm=llm, budget=inv_budget, emit=emit,
        )
        ranked, ledger = inv.hypotheses, None
        mode_label = "autopilot" if inv.used_autopilot else "agentic"
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
    # Not in ensemble mode. The challenger's penalty is applied by
    # `rescore_from_ledger`, which re-derives every score from the canonical ledger —
    # and that would silently overwrite the fused scores with a plain single-mode
    # rescore, throwing the ensemble away right after computing it. Challenging the
    # fused verdict needs the penalty folded into the fusion instead; that is a
    # follow-up, not a line to sneak in here.
    if not fixed_pipeline and mode != "ensemble" and ranked:
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
    return RunVerdict(case_id, run_id, mode_label, ranked, ledger,
                      investigator_status=inv_status, challenger_status=ch_status,
                      challenger_attacks=attacks, fallback_note=note,
                      remediation=remediation, narration=narration,
                      tool_calls=(inv_budget.calls if inv_budget else 0),
                      cost_points_spent=(inv_budget.cost if inv_budget else 0),
                      expensive_checks=expensive, transcripts=transcripts,
                      ensemble=ensemble_block, mode_hypotheses=mode_hyps)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pipeline")
    ap.add_argument("--case", required=True)
    ap.add_argument("--fixed-pipeline", action="store_true",
                    help="bypass both agents -> deterministic autopilot (eval ablation)")
    ap.add_argument("--ensemble", action="store_true",
                    help="run BOTH engines concurrently and fuse their verdicts")
    ap.add_argument("--ensemble-weight", type=float, default=None,
                    help="override the agentic weight (default: derived from agreement)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--store", default=_STORE)
    ap.add_argument("--narration", action="store_true", help="print the full incident report")
    args = ap.parse_args(argv[1:])

    v = run(args.case, fixed_pipeline=args.fixed_pipeline, store_root=args.store,
            mode="ensemble" if args.ensemble else None,
            ensemble_weight=args.ensemble_weight)
    print(f"case={v.case_id} mode={v.mode} investigator={v.investigator_status} "
          f"challenger={v.challenger_status} attacks={len(v.challenger_attacks)}")
    if v.fallback_note:
        print(f"note: {v.fallback_note}")
    if v.ensemble:
        e = v.ensemble
        print(f"ensemble: agentic top1={e['top1']['agentic']} | fixed top1={e['top1']['fixed']} "
              f"| FUSED top1={e['top1']['ensemble']}")
        print(f"  weights: agentic={e['weight_agentic']} fixed={e['weight_fixed']} "
              f"({'agree' if e['agree'] else 'DISAGREE'})")
        print(f"  {e['reason']}")
        for d in e["disagreements"]:
            print(f"  rank delta: {d}")
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
