"""Counterfactual causal test.

Remove a suspect and ask how many anomalies the *remaining* candidates can still
explain (via their reachable sets). A HIGH still-explained percentage means the
suspect is unnecessary — its score is discounted; a LOW percentage means the
suspect is load-bearing — its score is kept.

    score_multiplier = 1 - 0.5 * (still_explained_pct / 100)
      pct = 100  (fully redundant)   -> x0.5
      pct = 0    (indispensable)     -> x1.0
"""

from __future__ import annotations

from backend.localize.blast import BlastSubgraph, reachable_upstream
from backend.models import AnomalyEvent


def remove_and_explain(
    subgraph: BlastSubgraph,
    anomalies: list[AnomalyEvent],
    reach_by_suspect: dict[str, set[str]],
    removed: str,
) -> float:
    """Percentage [0,100] of subgraph anomalies still explained by the reachable
    sets of the candidates OTHER than `removed`.

    `reach_by_suspect` maps each candidate suspect -> its reachable set (the
    "candidates" of the pipeline, pre-resolved to reachable components)."""
    sub_anoms = [a for a in anomalies if a.component_id in subgraph.nodes]
    if not sub_anoms:
        return 0.0
    covered: set[str] = set()
    for suspect, reach in reach_by_suspect.items():
        if suspect != removed:
            covered |= reach
    explained = sum(1 for a in sub_anoms if a.component_id in covered)
    return round(100.0 * explained / len(sub_anoms), 2)


def score_multiplier(still_explained_pct: float) -> float:
    """1 - 0.5 * pct_fraction. High still-explained => suspect unnecessary => lower."""
    return round(1.0 - 0.5 * (max(0.0, min(100.0, still_explained_pct)) / 100.0), 6)


def reach_map(topology, suspects) -> dict[str, set[str]]:
    """Build the {suspect -> reachable set} map used by remove_and_explain."""
    return {s: reachable_upstream(topology, s) for s in suspects}
