"""Deterministic rescore over the (agent-enriched) evidence ledger.

The ledger records WHICH candidates were investigated — that was the agent's
choice. The NUMBERS are re-derived here deterministically, so no agent output is
ever the verdict (rule 12). Tiers come from tiers.py only (rule 5).
"""

from __future__ import annotations

import re

import networkx as nx

from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius, reachable_upstream
from backend.models import AnomalyEvent, RankedHypothesis
from backend.rank import tiers
from backend.rank.counterfactual import remove_and_explain, score_multiplier
from backend.rank.scorer import rank

CHALLENGER_PENALTY = 0.1          # per upheld attack, subtracted from the score


def counterfactual_components(ledger: Ledger) -> set[str]:
    return {f.component_ids[0] for f in ledger.query(kind="counterfactual_result", limit=500)
            if f.component_ids}


def twin_facts(ledger: Ledger) -> dict[str, tuple[str, float]]:
    out: dict[str, tuple[str, float]] = {}
    for f in ledger.query(kind="twin_result", limit=500):
        if not f.component_ids:
            continue
        v = re.search(r"verdict=(\w+)", f.statement)
        # anchor the number so a trailing sentence period isn't captured
        s = re.search(r"similarity=([0-9]+(?:\.[0-9]+)?)", f.statement)
        out[f.component_ids[0]] = (v.group(1) if v else "pending",
                                   float(s.group(1)) if s else 0.0)
    return out


def _scale(breakdown: dict[str, float], factor: float) -> tuple[dict[str, float], float]:
    bd = {k: round(v * factor, 6) for k, v in breakdown.items()}
    return bd, round(sum(bd.values()), 6)


def rescore_from_ledger(
    case_id: str,
    anomalies: list[AnomalyEvent],
    topology: nx.DiGraph,
    store: EventStore,
    ledger: Ledger,
    attacks_by_hypothesis: dict[str, list[dict]] | None = None,
) -> list[RankedHypothesis]:
    attacks_by_hypothesis = attacks_by_hypothesis or {}
    ranked = rank(case_id, anomalies, topology, store=store)
    blast = blast_radius(topology, {a.component_id for a in anomalies})
    reach_by = {h.suspect_component: reachable_upstream(topology, h.suspect_component)
                for h in ranked}
    cf_comps = counterfactual_components(ledger)
    twins = twin_facts(ledger)

    dumped: list[dict] = []
    for h in ranked:
        d = h.model_dump()
        comp = h.suspect_component

        if comp in cf_comps:                       # the agent bought a counterfactual here
            pct = remove_and_explain(blast, anomalies, reach_by, comp)
            d["score_breakdown"], d["score"] = _scale(d["score_breakdown"], score_multiplier(pct))
            d["counterfactual"] = {"removed": True, "anomalies_still_explained_pct": pct}

        if comp in twins:                          # the agent bought a twin here
            verdict, sim = twins[comp]
            if verdict in ("match", "partial", "mismatch"):
                d["twin"] = {"run": f"twin-{comp}", "similarity": max(0.0, min(1.0, sim)),
                             "verdict": verdict, "missing_evidence": []}

        atks = attacks_by_hypothesis.get(d["hypothesis_id"])
        if atks:                                   # upheld attacks: score -0.1 each
            old = d["score"]
            new = max(0.0, round(old - CHALLENGER_PENALTY * len(atks), 6))
            d["score_breakdown"], d["score"] = _scale(d["score_breakdown"],
                                                      (new / old) if old > 0 else 0.0)
            d["challenger"] = {"attacks": atks}
        dumped.append(d)

    dumped.sort(key=lambda x: x["score"], reverse=True)

    final: list[RankedHypothesis] = []
    for i, d in enumerate(dumped, start=1):
        d["rank"] = i
        tv = d["twin"]["verdict"] if d.get("twin") else "pending"
        upheld = bool(d.get("challenger") and any(a.get("upheld") for a in d["challenger"]["attacks"]))
        tctx = tiers.build_context(
            d["suspect_component"], anomalies, topology, d["predicted_symptoms"],
            d["cited_evidence_ids"], twin_verdict=tv, challenger_upheld=upheld,
            resolve=store.resolve,
        )
        tres = tiers.assign_tier(tctx, ledger=ledger, hypothesis_id=d["hypothesis_id"])
        d["tier"], d["tier_reason"] = tres.tier, tres.reason
        rh = RankedHypothesis.model_validate(d)
        ledger.hypothesis_scored(
            statement=f"{rh.suspect_component} scored {rh.score:.3f}, tier={rh.tier}.",
            component_ids=[rh.suspect_component], ts_range=(0.0, 0.0),
            hypothesis_id=rh.hypothesis_id,
        )
        final.append(rh)
    return final
