"""Evidence tiers — the ONLY place a tier is ever assigned or written (rule 5).

Verbatim rules:

  CONFIRMED        cited ids resolve AND a topology path connects the suspect to
                   every OBSERVED symptom AND full temporal precedence holds AND
                   the twin verdict == "match".  (Until Step 7 the twin is
                   "pending", which caps a hypothesis at CORRELATED.)
  CORRELATED       co-occurring, but confirmation is blocked by any of: no path,
                   partial precedence, twin partial/pending, or an upheld
                   challenger attack.
  MISSING_EVIDENCE any predicted symptom sits at an UNINSTRUMENTED component
                   (blocks confirmation) -> also file coverage_gap facts and
                   emit instrumentation recommendations; or the suspect does not
                   co-occur with the observed symptom set at all.

`tier_reason` names the exact rule that passed or failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import networkx as nx

from backend.localize.blast import reachable_upstream
from backend.models import AnomalyEvent, Tier
from backend.rank.constants import PRECEDENCE_SKEW_S

CONFIRMED: Tier = "CONFIRMED"
CORRELATED: Tier = "CORRELATED"
MISSING_EVIDENCE: Tier = "MISSING_EVIDENCE"


@dataclass
class TierContext:
    suspect: str
    cited_ids_resolve: bool
    all_symptoms_have_path: bool
    full_precedence: bool
    twin_verdict: str          # pending | match | partial | mismatch | none
    challenger_upheld: bool
    uninstrumented_symptoms: list[str]
    co_occurring: bool


@dataclass
class TierResult:
    tier: Tier
    reason: str
    coverage_gaps: list[str] = field(default_factory=list)       # uninstrumented components
    recommendations: list[str] = field(default_factory=list)


def _sym_component(s) -> str:
    return s["component_id"] if isinstance(s, dict) else s.component_id


def _sym_observed(s):
    return s["observed"] if isinstance(s, dict) else s.observed


def build_context(
    suspect: str,
    anomalies: list[AnomalyEvent],
    topology: nx.DiGraph,
    predicted_symptoms: list,
    cited_evidence_ids: list[str],
    *,
    twin_verdict: str = "pending",
    challenger_upheld: bool = False,
    resolve: Callable[[list[str]], bool] | None = None,
) -> TierContext:
    anomalous = {a.component_id for a in anomalies}
    suspect_anoms = [a for a in anomalies if a.component_id == suspect]

    first_ts: dict[str, float] = {}
    for a in anomalies:
        first_ts[a.component_id] = min(first_ts.get(a.component_id, a.window.start), a.window.start)

    reach = reachable_upstream(topology, suspect)
    t_s = min((a.window.start for a in suspect_anoms), default=None)
    violations = 0
    for c in reach - {suspect}:
        if c in anomalous and t_s is not None and t_s > first_ts[c] + PRECEDENCE_SKEW_S:
            violations += 1
    full_precedence = violations == 0

    observed = [s for s in predicted_symptoms if _sym_observed(s) is True]
    all_path = True
    for s in observed:
        comp = _sym_component(s)
        if not (topology.has_node(comp) and topology.has_node(suspect)
                and nx.has_path(topology, comp, suspect)):
            all_path = False
            break

    uninstrumented = [_sym_component(s) for s in predicted_symptoms if _sym_observed(s) is None]
    cited_resolve = True if resolve is None else (resolve(cited_evidence_ids) if cited_evidence_ids else True)

    return TierContext(
        suspect=suspect,
        cited_ids_resolve=bool(cited_resolve),
        all_symptoms_have_path=all_path,
        full_precedence=full_precedence,
        twin_verdict=twin_verdict,
        challenger_upheld=challenger_upheld,
        uninstrumented_symptoms=uninstrumented,
        co_occurring=len(suspect_anoms) > 0,
    )


def assign_tier(ctx: TierContext, *, ledger=None, hypothesis_id: str | None = None) -> TierResult:
    """Assign a tier from the evidence. If MISSING_EVIDENCE is due to an
    uninstrumented symptom and a ledger is provided, file coverage_gap facts."""
    # MISSING_EVIDENCE: an uninstrumented symptom blocks confirmation.
    if ctx.uninstrumented_symptoms:
        gaps = sorted(set(ctx.uninstrumented_symptoms))
        recs = [f"instrument {c} (emit metrics/logs) to confirm or refute {ctx.suspect}" for c in gaps]
        if ledger is not None:
            for c in gaps:
                ledger.coverage_gap(
                    statement=f"{c} is uninstrumented; predicted symptom of {ctx.suspect} "
                              f"cannot be observed. Recommend instrumenting {c}.",
                    component_ids=[c],
                )
        return TierResult(
            MISSING_EVIDENCE,
            f"MISSING_EVIDENCE: predicted symptom at uninstrumented component(s) "
            f"{gaps} blocks confirmation.",
            coverage_gaps=gaps, recommendations=recs,
        )

    # CONFIRMED: every confirmation condition holds.
    if (ctx.cited_ids_resolve and ctx.all_symptoms_have_path
            and ctx.full_precedence and ctx.twin_verdict == "match"):
        return TierResult(
            CONFIRMED,
            "CONFIRMED: cited evidence resolves, topology path connects the suspect "
            "to every observed symptom, temporal precedence holds, and the twin "
            "reproduced the fault (verdict=match).",
        )

    # CORRELATED: co-occurring but at least one confirmation condition fails.
    if ctx.co_occurring:
        blockers: list[str] = []
        if not ctx.cited_ids_resolve:
            blockers.append("cited evidence does not resolve")
        if not ctx.all_symptoms_have_path:
            blockers.append("no topology path to an observed symptom")
        if not ctx.full_precedence:
            blockers.append("partial temporal precedence")
        if ctx.twin_verdict != "match":
            blockers.append(f"twin {ctx.twin_verdict}")
        if ctx.challenger_upheld:
            blockers.append("an upheld challenger attack")
        return TierResult(
            CORRELATED,
            "CORRELATED: co-occurring, but confirmation blocked by "
            + "; ".join(blockers or ["incomplete evidence"]) + ".",
        )

    return TierResult(
        MISSING_EVIDENCE,
        "MISSING_EVIDENCE: suspect does not co-occur with the observed symptom "
        "set; insufficient evidence to correlate.",
    )
