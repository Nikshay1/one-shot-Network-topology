"""The ensemble: fuse the agentic verdict with the deterministic one.

Why fuse VERDICTS rather than pool EVIDENCE
-------------------------------------------
The two modes are not two models. They are the SAME deterministic scorer over
DIFFERENT evidence:

    fixed:   ledger_F (autopilot: top-5 counterfactuals + 1 twin) -> scorer -> ranking_F
    agentic: ledger_A (whatever the agent chose to buy)           -> scorer -> ranking_A

So the obvious move — merge the ledgers, score once — is the wrong one, and the
project's own benchmark says why: at spending parity the agent LOSES
`red_herring_config` 2/5 against fixed's 4/5, because "it can afford to look at
everything, and the extra evidence drags innocent-but-correlated components up
the ranking" (README §The budget experiment). More evidence is not monotonically
better here. Pooling would inherit that harm by construction; fusing keeps the
autopilot's discipline at weight (1-w) even when the agent's evidence misleads.

The complementarity is real and measured, which is the whole case for an
ensemble: `confounded_pair` agentic 4/4 vs fixed 3/4; `red_herring_config`
agentic 2/5 vs fixed 4/5. Neither mode dominates.

No tuned constants
------------------
`agreement_weight` derives its weight from the tier ladder that `tiers.py`
already defines, not from a number fitted to the suite. That matters: the dev
split is empty (n=1 real case, 20% rounds to zero), so a weight tuned on the 23
synthetic cases and then reported on those same 23 cases would be overfitting
wearing a lab coat. When the modes agree, 0.5. When they disagree, lean by how
well-evidenced each one's top suspect is.

Rules honoured
--------------
- rule 12: the verdict is still arithmetic over the ledger. Fusion is arithmetic;
  no agent's opinion is copied into a score.
- rule 5:  tiers are NOT chosen here. The fused hypothesis's evidence is merged
  and handed back to `tiers.assign_tier`, which is the only place a tier is ever
  assigned.
- rule 3:  `score_breakdown` is blended component-wise, so it still sums to
  `score` — the RankedHypothesis validator enforces that to 1e-6.

    py -m backend.rank.ensemble --case red_herring_config-01
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Iterable

import networkx as nx

from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.models import AnomalyEvent, RankedHypothesis
from backend.rank import tiers

# The tier ladder, as a strength. This is not a tuning knob — it is the ordering
# tiers.py already publishes (CONFIRMED needs everything; MISSING_EVIDENCE means a
# symptom sits somewhere nothing instruments).
_TIER_STRENGTH: dict[str, int] = {
    tiers.CONFIRMED: 3,
    tiers.CORRELATED: 2,
    tiers.MISSING_EVIDENCE: 1,
}

_AGREE_WEIGHT = 0.5             # a tie is a tie: neither mode earns the casting vote


@dataclass
class EnsembleVerdict:
    hypotheses: list[RankedHypothesis]
    weight_agentic: float
    reason: str
    agree: bool
    top1_agentic: str | None = None
    top1_fixed: str | None = None
    top1_ensemble: str | None = None
    disagreements: list[str] = field(default_factory=list)   # components the modes rank differently

    def to_dict(self) -> dict:
        return {
            "weight_agentic": round(self.weight_agentic, 6),
            "weight_fixed": round(1.0 - self.weight_agentic, 6),
            "reason": self.reason,
            "agree": self.agree,
            "top1": {"agentic": self.top1_agentic, "fixed": self.top1_fixed,
                     "ensemble": self.top1_ensemble},
            "disagreements": self.disagreements,
        }


def agreement_weight(agentic: list[RankedHypothesis],
                     fixed: list[RankedHypothesis]) -> tuple[float, str]:
    """Weight on the AGENTIC ranking, in [0, 1]. Derived, never fitted.

    Agreement is the signal. When both modes independently land on the same top-1,
    there is nothing to arbitrate and they average. When they disagree, the tie is
    broken by how well-evidenced each one's own top suspect is — CONFIRMED outranks
    CORRELATED outranks MISSING_EVIDENCE — because that ladder is exactly a statement
    about how much evidence stands behind the claim.
    """
    if not agentic and not fixed:
        return _AGREE_WEIGHT, "neither mode produced a ranking"
    if not agentic:
        return 0.0, "agentic produced no ranking; the deterministic verdict stands alone"
    if not fixed:
        return 1.0, "fixed produced no ranking; the agentic verdict stands alone"

    a, f = agentic[0], fixed[0]
    if a.suspect_component == f.suspect_component:
        return _AGREE_WEIGHT, (f"both modes independently rank {a.suspect_component} first "
                               f"— averaged 50/50")

    sa = _TIER_STRENGTH.get(a.tier, 1)
    sf = _TIER_STRENGTH.get(f.tier, 1)
    w = sa / (sa + sf)
    return w, (f"modes disagree: agentic says {a.suspect_component} ({a.tier}), fixed says "
               f"{f.suspect_component} ({f.tier}) — leaning {w:.0%} agentic by evidence tier")


def _blend_breakdown(a: dict[str, float], f: dict[str, float], w: float) -> tuple[dict, float]:
    """Component-wise blend. The score is the SUM of the blended parts, never a
    separately-computed number — that is what keeps the model validator satisfied
    (score_breakdown.total() == score) instead of merely close."""
    keys = sorted(set(a) | set(f))
    bd = {k: round(w * a.get(k, 0.0) + (1.0 - w) * f.get(k, 0.0), 6) for k in keys}
    return bd, round(sum(bd.values()), 6)


def _merge_evidence(da: dict | None, df: dict | None) -> dict:
    """Merge the two modes' evidence for one hypothesis.

    Evidence is not blended — it either exists or it does not. A twin the agent ran
    and the autopilot skipped is still a twin that ran; averaging a verdict with
    'pending' would invent a result nobody measured.
    """
    base = dict(da or df or {})
    other = df if da else None
    if da and df:
        for key in ("twin", "counterfactual", "challenger"):
            # prefer whichever mode actually has the evidence; if both, keep the
            # agentic one (same deterministic check, same inputs -> same answer)
            if not base.get(key) and df.get(key):
                base[key] = df[key]
        cited = list(dict.fromkeys([*(da.get("cited_evidence_ids") or []),
                                    *(df.get("cited_evidence_ids") or [])]))
        base["cited_evidence_ids"] = cited
    _ = other
    return base


def fuse(
    agentic: list[RankedHypothesis],
    fixed: list[RankedHypothesis],
    *,
    anomalies: list[AnomalyEvent],
    topology: nx.DiGraph,
    store: EventStore,
    ledger: Ledger,
    weight: float | None = None,
) -> EnsembleVerdict:
    """Blend two rankings into one. Tiers are re-assigned by tiers.py, never chosen here."""
    w, reason = agreement_weight(agentic, fixed)
    if weight is not None:                      # explicit override (eval/ablation only)
        w, reason = weight, f"weight pinned to {weight:.2f} by the caller"

    by_a = {h.hypothesis_id: h.model_dump() for h in agentic}
    by_f = {h.hypothesis_id: h.model_dump() for h in fixed}

    dumped: list[dict] = []
    for hid in list(by_a) + [k for k in by_f if k not in by_a]:
        da, df = by_a.get(hid), by_f.get(hid)
        d = _merge_evidence(da, df)
        if da and df:
            d["score_breakdown"], d["score"] = _blend_breakdown(
                da["score_breakdown"], df["score_breakdown"], w)
        # a hypothesis only one mode ranked keeps its own numbers: there is nothing
        # to average against, and scoring the other side as 0 would invent a vote
        # it never cast.
        dumped.append(d)

    dumped.sort(key=lambda x: (-x["score"], x["suspect_component"]))

    final: list[RankedHypothesis] = []
    for i, d in enumerate(dumped, start=1):
        d["rank"] = i
        tv = d["twin"]["verdict"] if d.get("twin") else "pending"
        upheld = bool(d.get("challenger")
                      and any(a.get("upheld") for a in d["challenger"]["attacks"]))
        tctx = tiers.build_context(
            d["suspect_component"], anomalies, topology, d["predicted_symptoms"],
            d["cited_evidence_ids"], twin_verdict=tv, challenger_upheld=upheld,
            resolve=store.resolve,
        )
        tres = tiers.assign_tier(tctx, ledger=ledger, hypothesis_id=d["hypothesis_id"])
        d["tier"], d["tier_reason"] = tres.tier, tres.reason
        rh = RankedHypothesis.model_validate(d)
        ledger.hypothesis_scored(
            statement=f"ENSEMBLE {rh.suspect_component} scored {rh.score:.3f} "
                      f"(agentic w={w:.2f}), tier={rh.tier}.",
            component_ids=[rh.suspect_component], ts_range=(0.0, 0.0),
            hypothesis_id=rh.hypothesis_id,
        )
        final.append(rh)

    top_a = agentic[0].suspect_component if agentic else None
    top_f = fixed[0].suspect_component if fixed else None
    return EnsembleVerdict(
        hypotheses=final,
        weight_agentic=w,
        reason=reason,
        agree=bool(top_a and top_a == top_f),
        top1_agentic=top_a,
        top1_fixed=top_f,
        top1_ensemble=final[0].suspect_component if final else None,
        disagreements=_rank_disagreements(agentic, fixed),
    )


# hypothesis_scored is deliberately NOT merged: each mode's scores are superseded by
# the fused ones, and copying them in would leave three contradictory scores per
# component in the ledger the narrator has to explain.
_DERIVED_KINDS = ("counterfactual_result", "twin_result", "remediation_result",
                  "topology_no_path", "anomaly_absent", "coverage_gap",
                  "investigation_note", "topology_path", "temporal_order")


def merge_derived_facts(canonical: Ledger, sources: list[Ledger]) -> int:
    """Re-file both engines' derived evidence into one canonical ledger.

    Re-filed rather than copied: fact ids are minted per component from 0 in each
    mode's ledger, so `fact-catalogue-0000` exists in both and means different things.
    Re-filing mints canonical ids and keeps citations unambiguous — which is the
    entire contract the narrator's validator rests on.

    Deduped on (kind, components, statement): both engines run the same deterministic
    counterfactual on the same inputs, so the overlap is exact and filing it twice
    would let the narrator cite the same finding as if it were two.
    """
    seen: set[tuple] = set()
    for f in canonical.query(limit=100_000):
        seen.add((f.kind, tuple(f.component_ids), f.statement))

    filed = 0
    for src in sources:
        for f in src.query(limit=100_000):
            if f.kind not in _DERIVED_KINDS:
                continue
            key = (f.kind, tuple(f.component_ids), f.statement)
            if key in seen:
                continue
            seen.add(key)
            canonical.write(
                kind=f.kind, statement=f.statement, component_ids=list(f.component_ids),
                event_ids=list(f.event_ids), modality=f.modality,
                ts_range=(f.ts_range.start, f.ts_range.end), confidence=f.confidence,
                hypothesis_id=f.hypothesis_id,
            )
            filed += 1
    return filed


def _rank_disagreements(agentic: Iterable[RankedHypothesis],
                        fixed: Iterable[RankedHypothesis]) -> list[str]:
    """Components the two modes place at different ranks — the interesting part of a
    run, and the thing a single-mode pipeline can never show you."""
    ra = {h.suspect_component: h.rank for h in agentic}
    rf = {h.suspect_component: h.rank for h in fixed}
    out = []
    for comp in sorted(set(ra) | set(rf)):
        a, f = ra.get(comp), rf.get(comp)
        if a != f:
            out.append(f"{comp}: agentic #{a if a else '-'} vs fixed #{f if f else '-'}")
    return out


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="rank.ensemble",
                                 description="Fuse the agentic and fixed verdicts for a case.")
    ap.add_argument("--case", required=True)
    ap.add_argument("--run", default=None)
    ap.add_argument("--weight", type=float, default=None, help="override the agentic weight")
    args = ap.parse_args(argv[1:])

    from backend.pipeline import run as pipeline_run

    v = pipeline_run(args.case, run_id=args.run or args.case, mode="ensemble",
                     ensemble_weight=args.weight)
    e = v.ensemble
    print(f"case={v.case_id} mode={v.mode}")
    print(f"  agentic top1 = {e['top1']['agentic']}")
    print(f"  fixed   top1 = {e['top1']['fixed']}")
    print(f"  ENSEMBLE top1= {e['top1']['ensemble']}   (agentic weight {e['weight_agentic']})")
    print(f"  {e['reason']}")
    if e["disagreements"]:
        print("  disagreements:")
        for d in e["disagreements"]:
            print("   -", d)
    print()
    for h in v.hypotheses[:5]:
        print(f"  {h.rank}  {h.suspect_component:<16} {h.score:.3f}  {h.tier}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
