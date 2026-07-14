"""Deterministic scorer: candidates -> ranked, tiered RankedHypotheses.

Five sub-scores (each in [0,1]) are pre-weighted by `constants.WEIGHTS`; the
weighted contributions form `score_breakdown` and sum to `score`. Reachability
is computed on the REVERSED dependency graph (a suspect "reaches" the callers
that would show its symptoms).

    py -m backend.rank.scorer --case catalogue_cpu-1 --top 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import networkx as nx

from backend.localize.blast import blast_radius
from backend.models import (
    AnomalyEvent,
    Counterfactual,
    PredictedSymptom,
    RankedHypothesis,
    ScoreBreakdown,
)
from backend.rank import tiers
from backend.rank.candidates import Candidate, generate_candidates
from backend.rank.constants import PRECEDENCE_SKEW_S, WEIGHTS

_DEFAULT_STORE = "data/parquet"
_DEFAULT_ANOMALIES = Path("data/anomalies")


def load_anomalies(case_id: str, anomalies_dir: str | Path = _DEFAULT_ANOMALIES) -> list[AnomalyEvent]:
    path = Path(anomalies_dir) / f"{case_id}.json"
    if not path.exists():
        return []
    return [AnomalyEvent.model_validate(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def _reach(topology: nx.DiGraph, suspect: str) -> set[str]:
    """Suspect + everything that (transitively) calls it — i.e. who shows symptoms."""
    if suspect not in topology:
        return {suspect}
    return {suspect} | nx.ancestors(topology, suspect)


def _personalized_pagerank(topology: nx.DiGraph, anomalous: set[str]) -> dict[str, float]:
    if topology.number_of_nodes() == 0:
        return {}
    g_rev = topology.reverse(copy=True)
    pers = {n: 1.0 for n in anomalous if n in g_rev}
    if not pers:
        return {}
    try:
        return nx.pagerank(g_rev, personalization=pers)
    except nx.PowerIterationFailedConvergence:  # pragma: no cover
        return {n: pers.get(n, 0.0) for n in g_rev}


def score_candidates(
    case_id: str,
    candidates: list[Candidate],
    anomalies: list[AnomalyEvent],
    topology: nx.DiGraph,
    blast,
    store=None,
) -> list[RankedHypothesis]:
    anomalous = {a.component_id for a in anomalies}
    subgraph_anoms = [a for a in anomalies if a.component_id in blast.nodes]
    n_sub = len(subgraph_anoms) or 1

    first_ts: dict[str, float] = {}
    for a in anomalies:
        first_ts[a.component_id] = min(first_ts.get(a.component_id, a.window.start), a.window.start)

    pr = _personalized_pagerank(topology, anomalous)
    pr_max = max(pr.values()) if pr else 0.0
    reach_by_suspect = {c.suspect: _reach(topology, c.suspect) for c in candidates}

    scored: list[tuple] = []
    for cand in candidates:
        s = cand.suspect
        reach = reach_by_suspect[s]
        explained = [a for a in subgraph_anoms if a.component_id in reach]
        coverage = len(explained) / n_sub
        topo = (len(anomalous & reach) / len(anomalous)) if anomalous else 0.0

        t_s = min((a.window.start for a in cand.suspect_anomalies), default=None)
        checks = viol = 0
        for c in reach - {s}:
            if c in anomalous:
                checks += 1
                if t_s is not None and t_s > first_ts[c] + PRECEDENCE_SKEW_S:
                    viol += 1
        precedence = 1.0 if checks == 0 else 1.0 - viol / checks

        modalities = {a.source for a in cand.suspect_anomalies}
        corroboration = len(modalities) / 4.0
        pagerank = (pr.get(s, 0.0) / pr_max) if pr_max > 0 else 0.0

        raw = {
            "coverage": coverage, "topo_consistency": topo, "precedence": precedence,
            "corroboration": corroboration, "pagerank": pagerank,
        }
        breakdown = {k: round(WEIGHTS[k] * raw[k], 6) for k in WEIGHTS}
        score = round(sum(breakdown.values()), 6)

        tctx = tiers.build_context(
            s, anomalies, topology, cand.predicted_symptoms, cand.cited_evidence_ids,
            twin_verdict="pending",  # no twin at the floor (Step 7 lifts to match)
            resolve=(store.resolve if store is not None else None),
        )
        tres = tiers.assign_tier(tctx)  # no ledger at the floor -> no facts written
        scored.append((cand, breakdown, score, tres.tier, tres.reason, reach))

    scored.sort(key=lambda t: (t[2], blast.impact.get(t[0].suspect, 0.0)), reverse=True)

    result: list[RankedHypothesis] = []
    for rank_i, (cand, breakdown, score, tier, reason, reach) in enumerate(scored, start=1):
        # deterministic counterfactual proxy: anomalies still explained by OTHER
        # suspects if this one is removed (Step 6 computes the real thing).
        others_reach: set[str] = set()
        for c in candidates:
            if c.suspect != cand.suspect:
                others_reach |= reach_by_suspect[c.suspect]
        still = [a for a in subgraph_anoms if a.component_id in others_reach]
        cf_pct = round(100.0 * len(still) / n_sub, 2)

        result.append(RankedHypothesis(
            hypothesis_id=f"hyp-{cand.suspect.replace('-', '_')}-{rank_i:02d}",
            case_id=case_id,
            rank=rank_i,
            suspect_component=cand.suspect,
            statement=cand.statement,
            score=score,
            score_breakdown=ScoreBreakdown(**breakdown),
            tier=tier,
            tier_reason=reason,
            cited_evidence_ids=cand.cited_evidence_ids,
            predicted_symptoms=[PredictedSymptom(**s) for s in cand.predicted_symptoms],
            counterfactual=Counterfactual(removed=False, anomalies_still_explained_pct=cf_pct),
            twin=None,
            challenger=None,
            trigger_event_id=cand.trigger_event_id,
            fault_type_guess=cand.fault_type_guess,
        ))
    return result


def rank(case_id: str, anomalies: list[AnomalyEvent], topology: nx.DiGraph,
         store=None) -> list[RankedHypothesis]:
    anomalous = {a.component_id for a in anomalies}
    blast = blast_radius(topology, anomalous)
    candidates = generate_candidates(case_id, anomalies, topology, blast)
    return score_candidates(case_id, candidates, anomalies, topology, blast, store=store)


def _main(argv: list[str]) -> int:
    from backend.ingest.store import EventStore

    ap = argparse.ArgumentParser(prog="rank.scorer")
    ap.add_argument("--case", required=True)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--store", default=_DEFAULT_STORE)
    ap.add_argument("--anomalies", default=str(_DEFAULT_ANOMALIES))
    args = ap.parse_args(argv[1:])

    topology = EventStore(args.store).load_topology(args.case)
    if topology is None:
        print(f"no topology for case {args.case!r}", file=sys.stderr)
        return 1
    anomalies = load_anomalies(args.case, args.anomalies)
    ranked = rank(args.case, anomalies, topology)

    print(f"case={args.case}  anomalies={len(anomalies)}  candidates={len(ranked)}")
    print(f"{'rank':>4}  {'suspect':<14} {'score':>6}  {'tier':<16} fault")
    for h in ranked[: args.top]:
        print(f"{h.rank:>4}  {h.suspect_component:<14} {h.score:>6.3f}  {h.tier:<16} {h.fault_type_guess}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
