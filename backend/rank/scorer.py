"""Deterministic scorer: candidates -> ranked, tiered RankedHypotheses.

THE EDGE CONVENTION (rule 17, frozen): an edge A->B means "A calls/depends on B".
Failures propagate UPSTREAM. So for a suspect S:

    R(S) = nx.ancestors(topology, S) | {S}    — everything S can break
    E(S) = R(S) & anomalous                   — what S actually explains

tests/test_convention.py guards this. If you are about to swap ancestors for
descendants somewhere, you are wrong; read that file.

FIVE RAW FACTORS, each in [0,1] (rule 19: they are NOT weighted here — the
multiplication happens once, in serialize.py):

    coverage       = |E(S)| / |A|                     recall: of all that broke, how much does S explain?
    topo_precision = |E(S)| / |R(S) ∩ incident|       precision: of all S could break, how much did?
    precedence     = 1 - violations / max(|E(S)|,1)   an effect cannot precede its cause
    corroboration  = min(modalities(S) / 4, 1)        floored for inferred candidates
    pagerank       = PPR(S) / max(PPR)                personalized on the anomalous set

topo_precision is the one that earns its keep: the factor it replaced was recall
wearing a different name, so a god-node that reached everything scored well for
reaching everything.

    py -m backend.rank.scorer --case catalogue_cpu-1 --top 5
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from backend.localize.blast import blast_radius
from backend.models import AnomalyEvent, RankedHypothesis
from backend.rank import tiers
from backend.rank.candidates import Candidate, generate_candidates
from backend.rank.constants import PRECEDENCE_SKEW_S
from backend.rank.serialize import to_ranked_hypothesis
from backend.rank.weights import (
    INFERRED_CORROBORATION_FLOOR,
    MODALITIES,
    RAW_TO_SCHEMA,
    WEIGHTS,
)

_DEFAULT_STORE = "data/parquet"
_DEFAULT_ANOMALIES = Path("data/anomalies")


def load_anomalies(case_id: str, anomalies_dir: str | Path = _DEFAULT_ANOMALIES) -> list[AnomalyEvent]:
    path = Path(anomalies_dir) / f"{case_id}.json"
    if not path.exists():
        return []
    return [AnomalyEvent.model_validate(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def _reach(topology: nx.DiGraph, suspect: str) -> set[str]:
    """Suspect + everything that (transitively) calls it — i.e. who shows symptoms.

    Rule 17, the frozen edge convention: an edge A->B means "A calls B", so a
    failure at B surfaces at B's ANCESTORS. R(S) = ancestors(S) | {S} is therefore
    "everything S can break". tests/test_convention.py guards this; if you are
    tempted to use descendants here, read that file first.

    Rule 18: this runs on the FULL topology and is never clipped at the incident's
    k-hop boundary. Candidate GENERATION is scoped to the incident; the
    reachability math is not.
    """
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


@dataclass
class RawBreakdown:
    """The five factors, each in [0,1], BEFORE weighting.

    Rule 19: weighting happens exactly once, in serialize.py. Nothing here is
    multiplied by a weight — `s_math` is the weighted sum only so the scorer can
    rank; the number that ships is recomputed from the breakdown at serialize
    time and the two must agree.
    """

    coverage: float
    topo_precision: float
    precedence: float
    corroboration: float
    pagerank: float
    s_math: float


def _raw_factors(
    cand: Candidate,
    reach: set[str],
    anomalous: set[str],
    incident_nodes: set[str],
    first_ts: dict[str, float],
    pr: dict[str, float],
    pr_max: float,
) -> RawBreakdown:
    """The frozen formulas. Every division is guarded — an empty anomaly set or an
    empty reach yields 0.0, never a ZeroDivisionError and never a free 1.0."""
    explained = reach & anomalous              # E(S): what S explains
    n_explained = len(explained)

    # coverage (RECALL): of everything broken, how much does S explain?
    coverage = n_explained / len(anomalous) if anomalous else 0.0

    # topo_precision (PRECISION): of everything S could break within this
    # incident's scope, how much actually broke?
    #
    # This replaces the old `topo_consistency = |A ∩ R(S)| / |A|`, which was
    # recall with a different name — a near-duplicate of coverage that could not
    # penalise a god-node, because reaching more things only ever helped it. The
    # denominator is restricted to incident nodes so a suspect is not punished for
    # healthy services in unrelated parts of the estate.
    scope = reach & incident_nodes
    topo_precision = n_explained / len(scope) if scope else 0.0

    # precedence: a gradient, not a binary. A violation is an explained symptom
    # that started BEFORE the suspect's trigger (outside the clock-skew band) —
    # an effect cannot precede its cause.
    t_s = cand.trigger_ts
    violations = 0
    if t_s is not None:
        for sym in explained:
            if first_ts.get(sym, t_s) < t_s - PRECEDENCE_SKEW_S:
                violations += 1
    precedence = 1.0 - violations / max(n_explained, 1)

    # corroboration: distinct MODALITIES on the suspect itself.
    #
    # Not to be confused with the aggregation GATE in detect/aggregate (which
    # decides what counts as anomalous at all). Gate on signals, score on
    # modalities — two different questions, deliberately not merged.
    modalities = {a.source for a in cand.suspect_anomalies} & MODALITIES
    corroboration = min(len(modalities) / 4.0, 1.0)
    if cand.inferred:
        # Silence is not exoneration: an uninstrumented suspect cannot corroborate
        # itself, and a hard 0 would bury the very case we inferred it for.
        corroboration = max(corroboration, INFERRED_CORROBORATION_FLOOR)

    pagerank = (pr.get(cand.suspect, 0.0) / pr_max) if pr_max > 0 else 0.0

    raw = {
        "coverage": coverage,
        "topo_precision": topo_precision,
        "precedence": precedence,
        "corroboration": corroboration,
        "pagerank": pagerank,
    }
    s_math = sum(WEIGHTS[RAW_TO_SCHEMA[k]] * v for k, v in raw.items())
    return RawBreakdown(**raw, s_math=round(s_math, 6))


def score_candidates(
    case_id: str,
    candidates: list[Candidate],
    anomalies: list[AnomalyEvent],
    topology: nx.DiGraph,
    blast,
    store=None,
) -> list[RankedHypothesis]:
    anomalous = {a.component_id for a in anomalies}
    incident_nodes = set(blast.nodes)
    subgraph_anoms = [a for a in anomalies if a.component_id in incident_nodes]
    n_sub = len(subgraph_anoms) or 1

    first_ts: dict[str, float] = {}
    for a in anomalies:
        first_ts[a.component_id] = min(first_ts.get(a.component_id, a.window.start), a.window.start)

    # Once per incident, not per candidate.
    pr = _personalized_pagerank(topology, anomalous)
    pr_max = max(pr.values()) if pr else 0.0
    reach_by_suspect = {c.suspect: _reach(topology, c.suspect) for c in candidates}

    scored: list[tuple] = []
    for cand in candidates:
        s = cand.suspect
        reach = reach_by_suspect[s]
        raw = _raw_factors(cand, reach, anomalous, incident_nodes, first_ts, pr, pr_max)
        score = raw.s_math

        tctx = tiers.build_context(
            s, anomalies, topology, cand.predicted_symptoms, cand.cited_evidence_ids,
            twin_verdict="pending",  # no twin at the floor (Step 7 lifts to match)
            resolve=(store.resolve if store is not None else None),
        )
        tres = tiers.assign_tier(tctx)  # no ledger at the floor -> no facts written
        scored.append((cand, raw, score, tres.tier, tres.reason, reach))

    scored.sort(key=lambda t: (t[2], blast.impact.get(t[0].suspect, 0.0)), reverse=True)

    result: list[RankedHypothesis] = []
    for rank_i, (cand, raw, score, tier, reason, reach) in enumerate(scored, start=1):
        # deterministic counterfactual proxy: anomalies still explained by OTHER
        # suspects if this one is removed (Step 6 computes the real thing).
        others_reach: set[str] = set()
        for c in candidates:
            if c.suspect != cand.suspect:
                others_reach |= reach_by_suspect[c.suspect]
        still = [a for a in subgraph_anoms if a.component_id in others_reach]
        cf_pct = round(100.0 * len(still) / n_sub, 2)

        # Rule 19: the weighting happens there, once.
        result.append(to_ranked_hypothesis(
            candidate=cand,
            raw=raw,
            rank=rank_i,
            case_id=case_id,
            tier=tier,
            tier_reason=reason,
            counterfactual_pct=cf_pct,
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
