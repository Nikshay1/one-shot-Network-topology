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
from backend.agents.tools import ToolContext
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.models import RankedHypothesis
from backend.rank import autopilot as autopilot_mod
from backend.rank.rescore import rescore_from_ledger
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


def run(
    case_id: str,
    *,
    fixed_pipeline: bool = False,
    store_root: str | Path = _STORE,
    anomalies_dir: str | Path = _ANOM,
    ledger_dir: str | Path = _LEDGER,
    transcripts_dir: str | Path = _TRANSCRIPTS,
    run_id: str | None = None,
    llm=None,
    challenger_llm=None,
    emit: Callable[[str, dict], None] | None = None,
) -> RunVerdict:
    run_id = run_id or case_id

    if fixed_pipeline:                          # ablation: no agents at all
        v = autopilot_mod.run(case_id, store_root=store_root, anomalies_dir=anomalies_dir,
                              ledger_dir=ledger_dir, run_id=run_id)
        return RunVerdict(case_id, run_id, "autopilot", v.hypotheses, v.ledger,
                          fallback_note="--fixed-pipeline: agents bypassed")

    # --- Investigator, then ALWAYS a deterministic rescore from the ledger ---
    inv = investigate_and_rescore(
        case_id, store_root=store_root, anomalies_dir=anomalies_dir, ledger_dir=ledger_dir,
        transcripts_dir=transcripts_dir, run_id=run_id, llm=llm, emit=emit,
    )
    ranked = inv.hypotheses
    mode = "autopilot" if inv.used_autopilot else "agentic"

    store = EventStore(store_root)
    topology = store.load_topology(case_id)
    anomalies = load_anomalies(case_id, anomalies_dir)
    ledger = Ledger(run_id, case_id, ledger_dir)
    ctx = ToolContext(case_id=case_id, store=store, topology=topology, anomalies=anomalies,
                      blast=blast_radius(topology, {a.component_id for a in anomalies}),
                      ledger=ledger)

    # --- Challenger: one pass against the top hypothesis ---
    attacks: list[dict] = []
    ch_status: str | None = None
    if ranked:
        attacks, cres = challenge(ctx, ranked[0], run_id=run_id, llm=challenger_llm,
                                  emit=emit, transcripts_dir=transcripts_dir,
                                  budget=Budget(max_calls=5, max_cost_points=0, wall_clock_s=30.0))
        ch_status = cres.status
        if attacks:                             # upheld attacks -> -0.1 each + tier re-eval
            ranked = rescore_from_ledger(case_id, anomalies, topology, store, ledger,
                                         {ranked[0].hypothesis_id: attacks})

    return RunVerdict(case_id, run_id, mode, ranked, ledger,
                      investigator_status=inv.result.status if inv.result else None,
                      challenger_status=ch_status, challenger_attacks=attacks,
                      fallback_note=inv.note)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pipeline")
    ap.add_argument("--case", required=True)
    ap.add_argument("--fixed-pipeline", action="store_true",
                    help="bypass both agents -> deterministic autopilot (eval ablation)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--store", default=_STORE)
    args = ap.parse_args(argv[1:])

    v = run(args.case, fixed_pipeline=args.fixed_pipeline, store_root=args.store)
    print(f"case={v.case_id} mode={v.mode} investigator={v.investigator_status} "
          f"challenger={v.challenger_status} attacks={len(v.challenger_attacks)}")
    if v.fallback_note:
        print(f"note: {v.fallback_note}")
    print(f"{'rank':>4}  {'suspect':<14} {'score':>6}  {'tier':<16} fault")
    for h in v.hypotheses[: args.top]:
        print(f"{h.rank:>4}  {h.suspect_component:<14} {h.score:>6.3f}  {h.tier:<16} {h.fault_type_guess}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
