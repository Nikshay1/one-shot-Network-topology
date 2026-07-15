"""Raw factors -> RankedHypothesis. The frontend-compat boundary.

Two rules live here and nowhere else:

RULE 19 — the multiplication happens EXACTLY ONCE, here. The scorer keeps raw
factors in [0,1]; `score_breakdown` on the wire is PRE-WEIGHTED (raw × weight) and
its terms MUST sum to `score` within 1e-6. The schema says so, the golden test
asserts it, and the frontend renders the terms as contributions to the score. If
you ever find yourself weighting in the scorer too, you have double-weighted.

RULE: the schema is FROZEN (contracts/ranked_hypothesis.schema.json,
additionalProperties: false; models.py is extra="forbid"). The internal factor
`topo_precision` is serialized under the schema's `topo_consistency` key. Do not
rename the schema to match the code — the frontend mirrors the schema, and a
rename is a breaking change for a cosmetic gain.
"""

from __future__ import annotations

from backend.models import (
    Counterfactual,
    PredictedSymptom,
    RankedHypothesis,
    ScoreBreakdown,
)
from backend.rank.candidates import Candidate
from backend.rank.weights import RAW_TO_SCHEMA, SUM_TOLERANCE, WEIGHTS


def weighted_breakdown(raw, weights: dict[str, float] = WEIGHTS) -> dict[str, float]:
    """raw factor × its weight, keyed by the SCHEMA's names.

    This is the one place the weights are applied.
    """
    out: dict[str, float] = {}
    for raw_name, schema_key in RAW_TO_SCHEMA.items():
        out[schema_key] = round(weights[schema_key] * getattr(raw, raw_name), 6)
    return out


def to_ranked_hypothesis(
    candidate: Candidate,
    raw,
    rank: int,
    case_id: str,
    tier: str,
    tier_reason: str,
    counterfactual_pct: float,
    weights: dict[str, float] = WEIGHTS,
) -> RankedHypothesis:
    breakdown = weighted_breakdown(raw, weights)
    score = round(sum(breakdown.values()), 6)

    # The invariant the schema cannot express and the golden test checks.
    assert abs(sum(breakdown.values()) - score) <= SUM_TOLERANCE, (
        f"score_breakdown must sum to score: {breakdown} != {score}"
    )

    cited = list(candidate.cited_evidence_ids)
    if candidate.trigger_event_id and candidate.trigger_event_id not in cited:
        cited.append(candidate.trigger_event_id)

    return RankedHypothesis(
        hypothesis_id=f"hyp-{candidate.suspect.replace('-', '_')}-{rank:02d}",
        case_id=case_id,
        rank=rank,
        suspect_component=candidate.suspect,
        statement=candidate.statement,
        score=score,
        score_breakdown=ScoreBreakdown(**breakdown),
        tier=tier,
        tier_reason=tier_reason,
        cited_evidence_ids=cited,
        predicted_symptoms=[PredictedSymptom(**s) for s in candidate.predicted_symptoms],
        # NOT null: the schema requires `counterfactual` and it is not nullable.
        # At the floor this is the scorer's PROXY — removed=False says "nobody
        # actually bought a counterfactual for this yet", and rank/counterfactual.py
        # (rule 20, still mandatory) overwrites it with the measured thing. The
        # frontend keys off `removed` for exactly this reason.
        counterfactual=Counterfactual(removed=False, anomalies_still_explained_pct=counterfactual_pct),
        # These two ARE nullable, and downstream stages fill them.
        twin=None,
        challenger=None,
        trigger_event_id=candidate.trigger_event_id,
        fault_type_guess=candidate.fault_type_guess,
    )
