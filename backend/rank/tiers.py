"""Evidence tiers — the ONLY place a tier is ever assigned (rule 5).

CONFIRMED       : multi-modal corroboration, a concrete trigger, and temporal
                  precedence hold, with meaningful coverage.
CORRELATED      : the suspect is anomalous and reachability lines up, but the
                  trigger/precedence story is incomplete.
MISSING_EVIDENCE: suspect is uninstrumented, or evidence is too thin to correlate.
"""

from __future__ import annotations

from backend.models import Tier

CONFIRMED: Tier = "CONFIRMED"
CORRELATED: Tier = "CORRELATED"
MISSING_EVIDENCE: Tier = "MISSING_EVIDENCE"


def assign_tier(
    *,
    n_modalities: int,
    has_trigger: bool,
    precedence_ok: bool,
    coverage_raw: float,
    topo_raw: float,
    n_suspect_anomalies: int,
    uninstrumented: bool,
) -> tuple[Tier, str]:
    """Return (tier, tier_reason) from the deterministic evidence signals."""
    if uninstrumented or n_suspect_anomalies == 0:
        return (
            MISSING_EVIDENCE,
            "Suspect emits no direct telemetry (uninstrumented or unobserved); "
            "only indirect/upstream evidence is available.",
        )
    if n_modalities >= 2 and has_trigger and precedence_ok and coverage_raw >= 0.5:
        return (
            CONFIRMED,
            f"{n_modalities} modalities corroborate on the suspect, a trigger "
            f"event precedes downstream symptoms, and it explains "
            f"{coverage_raw:.0%} of subgraph anomalies.",
        )
    if coverage_raw > 0.0 or topo_raw > 0.0:
        return (
            CORRELATED,
            "Suspect is anomalous and reachability aligns with observed "
            "symptoms, but trigger/precedence evidence is incomplete.",
        )
    return (
        MISSING_EVIDENCE,
        "Anomalous but not connected to the observed symptom set; insufficient "
        "evidence to correlate.",
    )
