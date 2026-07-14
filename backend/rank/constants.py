"""Deterministic-ranking constants (single home for the scorer weights)."""

from __future__ import annotations

# Pre-weight each score component; the weighted contributions are what land in
# `score_breakdown` and MUST sum to `score`. These sum to 1.0.
WEIGHTS: dict[str, float] = {
    "coverage": 0.30,
    "topo_consistency": 0.25,
    "precedence": 0.15,
    "corroboration": 0.15,
    "pagerank": 0.15,
}

# Component criticality for blast-impact weighting (front-end is user-facing).
CRITICALITY_DEFAULT = 0.5
CRITICALITY: dict[str, float] = {"front-end": 0.9}

# Temporal-precedence tolerance (seconds) for the "cause precedes effect" check.
PRECEDENCE_SKEW_S = 5.0

# k for the k-hop blast subgraph.
BLAST_K = 2

# Confidence stamped on agent-filed ledger findings.
AGENT_FINDING_CONFIDENCE = 0.7


def criticality(component: str) -> float:
    return CRITICALITY.get(component, CRITICALITY_DEFAULT)
