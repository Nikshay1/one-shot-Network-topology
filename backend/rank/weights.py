"""The scorer's weight vector, and the rules about who may change it.

PROVENANCE (non-negotiable): production weights are tuned on the DEV split only,
by `py -m eval.split --tune`, which writes eval/tuning_log.json. That module
documents itself as "the ONLY place tuning is allowed to look", and it stays that
way — a second tuning entry point is a second answer to "where did 0.30 come
from?", and the whole point of the log is that there is exactly one.

So this file holds the vector and the invariants. It does not tune. If you want
different numbers, run the tuner and paste its winner here with the log entry
that produced it.

KEY NAMING, deliberately mismatched:
  internal raw factor   ->  serialized schema key
  coverage                  coverage
  topo_precision            topo_consistency     <- the schema key is FROZEN
  precedence                precedence
  corroboration             corroboration
  pagerank                  pagerank

The schema key `topo_consistency` is what contracts/ranked_hypothesis.schema.json
declares and what the frontend renders, so it cannot be renamed. The internal name
is `topo_precision` because that is what the factor now measures — see scorer.py.
This dict is keyed by the SCHEMA names, because that is what lands in
score_breakdown and what every existing importer already expects.
"""

from __future__ import annotations

from typing import Final

# Tuned on dev; see eval/tuning_log.json for the grid that chose them.
# MUST sum to 1.0 — score_breakdown terms are these times the raw factors, and
# the golden assertion checks they sum to `score`.
WEIGHTS: Final[dict[str, float]] = {
    "coverage": 0.30,
    "topo_consistency": 0.25,
    "precedence": 0.15,
    "corroboration": 0.15,
    "pagerank": 0.15,
}

#: The order the factors are reported in — stable, so tables and logs line up.
FACTOR_ORDER: Final[tuple[str, ...]] = (
    "coverage",
    "topo_consistency",
    "precedence",
    "corroboration",
    "pagerank",
)

#: Raw-factor name -> serialized schema key.
RAW_TO_SCHEMA: Final[dict[str, str]] = {
    "coverage": "coverage",
    "topo_precision": "topo_consistency",
    "precedence": "precedence",
    "corroboration": "corroboration",
    "pagerank": "pagerank",
}

#: Sum tolerance for the pre-weighting invariant.
SUM_TOLERANCE: Final[float] = 1e-6

#: Corroboration floor for inferred (silent) candidates — silence is not
#: exoneration: a component with no telemetry cannot corroborate itself, and
#: scoring it 0 would bury exactly the uninstrumented root cause we inferred it
#: for.
INFERRED_CORROBORATION_FLOOR: Final[float] = 0.33

#: Modalities that can corroborate a suspect.
MODALITIES: Final[frozenset[str]] = frozenset({"metric", "log", "alert", "config"})


def validate(weights: dict[str, float] = WEIGHTS) -> None:
    """Raise unless the vector is usable. Cheap enough to call at import."""
    missing = set(FACTOR_ORDER) - set(weights)
    extra = set(weights) - set(FACTOR_ORDER)
    if missing or extra:
        raise ValueError(f"weight keys wrong: missing={sorted(missing)} extra={sorted(extra)}")
    if any(w < 0 for w in weights.values()):
        raise ValueError(f"negative weight: {weights}")
    total = sum(weights.values())
    if abs(total - 1.0) > SUM_TOLERANCE:
        raise ValueError(f"weights must sum to 1.0, got {total!r}")


validate()
