"""Deterministic-ranking constants.

The scorer weights moved to backend/rank/weights.py, which owns the vector and
its invariants (and documents where the numbers came from). They are re-exported
here because half the codebase already imports them from this module, and a
weight vector with two homes is a weight vector with two values.
"""

from __future__ import annotations

from backend.rank.weights import WEIGHTS  # noqa: F401  (re-export; see module docstring)

# Component criticality for blast-impact weighting (front-end is user-facing).
CRITICALITY_DEFAULT = 0.5
CRITICALITY: dict[str, float] = {"front-end": 0.9}

# Temporal-precedence tolerance (seconds) for the "cause precedes effect" check.
PRECEDENCE_SKEW_S = 5.0

# k for the k-hop blast subgraph.
BLAST_K = 2

# Confidence stamped on agent-filed ledger findings.
AGENT_FINDING_CONFIDENCE = 0.7

# Twin verdict thresholds (cosine similarity of sim-vs-real delta signatures).
TWIN_MATCH_THETA = 0.80      # >= match
TWIN_PARTIAL_THETA = 0.50    # >= partial, else mismatch


def criticality(component: str) -> float:
    return CRITICALITY.get(component, CRITICALITY_DEFAULT)
