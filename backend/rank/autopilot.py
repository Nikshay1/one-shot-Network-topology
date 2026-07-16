"""Deterministic autopilot — the fixed pipeline, a first-class code path forever.

This is what Step 8 falls back to on any agent error/timeout/budget exhaustion,
and what `--fixed-pipeline` eval mode runs. Pipeline: deterministic floor rank ->
counterfactual (top-5 candidates + every risky-config target) -> twin for top-1
(Step 7; stubbed to "pending" here) -> rescore -> tiers. Every step writes to the
evidence ledger.

    py -m backend.rank.autopilot --case catalogue_cpu-1 --top 5
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import networkx as nx

from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius, reachable_upstream
from backend.ledger.seed import seed_from_anomalies
from backend.models import AnomalyEvent, RankedHypothesis
from backend.rank import tiers
from backend.rank.counterfactual import remove_and_explain, score_multiplier
from backend.rank.scorer import load_anomalies, rank

_DEFAULT_STORE = "data/parquet"
_DEFAULT_ANOMALIES = Path("data/anomalies")
_DEFAULT_LEDGER = Path("data/ledger")
_CF_TOP_K = 5
_REDUNDANT_PCT = 70.0


@dataclass
class Verdict:
    case_id: str
    run_id: str
    hypotheses: list[RankedHypothesis]
    ledger: Ledger


def _config_targets(anomalies: list[AnomalyEvent]) -> set[str]:
    return {a.component_id for a in anomalies if a.method == "config_risky_flag"}


def run(
    case_id: str,
    *,
    store_root: str | Path = _DEFAULT_STORE,
    anomalies_dir: str | Path = _DEFAULT_ANOMALIES,
    ledger_dir: str | Path = _DEFAULT_LEDGER,
    run_id: str | None = None,
    twin_fn: Callable[[str, str], dict | None] | None = None,
    cf_top_k: int = _CF_TOP_K,
    counterfactual_enabled: bool = True,
    twin_enabled: bool = True,
) -> Verdict:
    store = EventStore(store_root)
    topology = store.load_topology(case_id)
    if topology is None:
        raise FileNotFoundError(f"no topology for case {case_id!r}")
    anomalies = load_anomalies(case_id, anomalies_dir)
    run_id = run_id or case_id
    ledger = Ledger(run_id, case_id, ledger_dir)
    # The observations, before any conclusion drawn from them. Idempotent: the
    # investigator seeds its fresh ledger and then hands this same ledger to the
    # autopilot on the rule-11 fallback, which would otherwise file it all twice.
    seed_from_anomalies(ledger, anomalies)

    if twin_fn is None and twin_enabled:  # default: the SimPy twin (Step 7), for the top-1 suspect
        from backend.twin.runner import twin as _twin
        twin_fn = lambda comp, ft: _twin(case_id, comp, ft, store_root=store_root, ledger=None)

    ranked = rank(case_id, anomalies, topology, store=store)
    blast = blast_radius(topology, {a.component_id for a in anomalies})
    reach_by = {h.suspect_component: reachable_upstream(topology, h.suspect_component) for h in ranked}
    anomalous = {a.component_id for a in anomalies}

    # counterfactual set: original top-k plus every risky-config target (so a
    # red-herring config always gets a counterfactual-unchanged fact).
    cf_set = ({h.suspect_component for h in ranked[:cf_top_k]} | _config_targets(anomalies)
              if counterfactual_enabled else set())

    dumped: list[dict] = []
    for h in ranked:
        d = h.model_dump()
        if h.suspect_component in cf_set:
            pct = remove_and_explain(blast, anomalies, reach_by, h.suspect_component)
            mult = score_multiplier(pct)

            connected = any(
                c != h.suspect_component
                and (nx.has_path(topology, h.suspect_component, c) or nx.has_path(topology, c, h.suspect_component))
                for c in anomalous
            )
            if not connected:
                ledger.topology_no_path(
                    statement=f"No topology path connects {h.suspect_component} to any other "
                              f"anomalous component; not causally linked to the symptom set.",
                    component_ids=[h.suspect_component],
                )
            verdict_word = "redundant (counterfactual-unchanged)" if pct >= _REDUNDANT_PCT else "load-bearing"
            ledger.counterfactual_result(
                statement=f"Removing {h.suspect_component} leaves {pct:.0f}% of anomalies explained "
                          f"by other candidates -> {verdict_word} (score x{mult}).",
                component_ids=[h.suspect_component], ts_range=(0.0, 0.0),
                hypothesis_id=h.hypothesis_id,
            )

            new_bd = {k: round(v * mult, 6) for k, v in d["score_breakdown"].items()}
            d["score_breakdown"] = new_bd
            d["score"] = round(sum(new_bd.values()), 6)
            d["counterfactual"] = {"removed": True, "anomalies_still_explained_pct": pct}
        dumped.append(d)

    dumped.sort(key=lambda d: d["score"], reverse=True)

    # twin for the new top-1 (Step 7 provides twin_fn; else pending)
    top1_twin = "pending"
    if twin_fn is not None and dumped:
        top = dumped[0]
        tw = twin_fn(top["suspect_component"], top["fault_type_guess"])
        if tw:                                      # a twin_fn may decline (--no-twin ablation)
            top["twin"] = tw
            top1_twin = tw.get("verdict", "pending")
            ledger.twin_result(
                statement=f"Twin for {top['suspect_component']}: verdict={top1_twin}, "
                          f"similarity={tw.get('similarity')}.",
                component_ids=[top["suspect_component"]], ts_range=(0.0, 0.0),
                hypothesis_id=top["hypothesis_id"],
            )

    final: list[RankedHypothesis] = []
    for rank_i, d in enumerate(dumped, start=1):
        d["rank"] = rank_i
        tctx = tiers.build_context(
            d["suspect_component"], anomalies, topology,
            d["predicted_symptoms"], d["cited_evidence_ids"],
            twin_verdict=(top1_twin if rank_i == 1 else "pending"),
            resolve=store.resolve,
        )
        tres = tiers.assign_tier(tctx, ledger=ledger, hypothesis_id=d["hypothesis_id"])
        d["tier"] = tres.tier
        d["tier_reason"] = tres.reason
        rh = RankedHypothesis.model_validate(d)
        ledger.hypothesis_scored(
            statement=f"{rh.suspect_component} scored {rh.score:.3f}, tier={rh.tier}.",
            component_ids=[rh.suspect_component], ts_range=(0.0, 0.0),
            hypothesis_id=rh.hypothesis_id,
        )
        final.append(rh)

    return Verdict(case_id=case_id, run_id=run_id, hypotheses=final, ledger=ledger)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="rank.autopilot")
    ap.add_argument("--case", required=True)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--store", default=_DEFAULT_STORE)
    ap.add_argument("--anomalies", default=str(_DEFAULT_ANOMALIES))
    ap.add_argument("--ledger", default=str(_DEFAULT_LEDGER))
    args = ap.parse_args(argv[1:])

    verdict = run(args.case, store_root=args.store, anomalies_dir=args.anomalies,
                  ledger_dir=args.ledger)
    print(f"case={verdict.case_id}  hypotheses={len(verdict.hypotheses)}  "
          f"ledger={verdict.ledger.path}")
    print(f"{'rank':>4}  {'suspect':<14} {'score':>6}  {'tier':<16} fault")
    for h in verdict.hypotheses[: args.top]:
        print(f"{h.rank:>4}  {h.suspect_component:<14} {h.score:>6.3f}  {h.tier:<16} {h.fault_type_guess}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
