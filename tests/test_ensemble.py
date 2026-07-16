"""The ensemble: fuse the agentic verdict with the deterministic one.

The two things worth testing here are the two things that could quietly make the
ensemble a lie:

- the blend must keep `score_breakdown.total() == score`, or the contract's own
  validator rejects the fused hypothesis (rule 3);
- the fused tier must come from `tiers.py` (rule 5) and the fused score must be
  arithmetic over the ledger (rule 12) — an ensemble is exactly the sort of place
  where "just take the agent's answer when it's confident" sneaks in.
"""

from __future__ import annotations

import networkx as nx
import pytest

from backend.ledger.ledger import Ledger
from backend.models import RankedHypothesis
from backend.rank import tiers
from backend.rank.ensemble import (
    agreement_weight,
    fuse,
    merge_derived_facts,
)


# =========================================================================
# builders
# =========================================================================
def hyp(component: str, score: float, *, rank: int = 1, tier: str = tiers.CORRELATED,
        twin: dict | None = None, cited: list[str] | None = None,
        case_id: str = "case-001") -> RankedHypothesis:
    """A RankedHypothesis whose breakdown sums to `score` — as the contract demands."""
    part = round(score / 5.0, 6)
    bd = {"coverage": part, "topo_consistency": part, "precedence": part,
          "corroboration": part, "pagerank": part}
    return RankedHypothesis.model_validate({
        "hypothesis_id": f"hyp-{component.replace('-', '_')}-01",
        "case_id": case_id,
        "rank": rank,
        "suspect_component": component,
        "statement": f"{component} is a candidate root cause",
        "score": round(sum(bd.values()), 6),
        "score_breakdown": bd,
        "tier": tier,
        "tier_reason": "test",
        "cited_evidence_ids": cited or [],
        "predicted_symptoms": [],
        "counterfactual": {"removed": False, "anomalies_still_explained_pct": 0.0},
        "twin": twin,
        "challenger": None,
        "trigger_event_id": None,
        "fault_type_guess": "cpu",
    })


class _Store:
    """Only `resolve` is reached by tiers.build_context."""

    def resolve(self, ids):
        return True


@pytest.fixture
def topology():
    g = nx.DiGraph()
    g.add_edge("front-end", "catalogue")
    g.add_edge("front-end", "payment")
    return g


@pytest.fixture
def ledger(tmp_path):
    return Ledger("run-1", "case-001", tmp_path / "ledger", fresh=True)


def _fuse(agentic, fixed, topology, ledger, **kw):
    return fuse(agentic, fixed, anomalies=[], topology=topology, store=_Store(),
                ledger=ledger, **kw)


# =========================================================================
# the weight: derived, never fitted
# =========================================================================
def test_agreeing_modes_are_averaged_5050():
    a = [hyp("catalogue", 0.60)]
    f = [hyp("catalogue", 0.40)]
    w, reason = agreement_weight(a, f)
    assert w == 0.5
    assert "catalogue" in reason and "50/50" in reason


def test_disagreement_leans_toward_the_better_evidenced_mode():
    """CONFIRMED means the twin matched, the paths hold and precedence is complete.
    CORRELATED means at least one of those is missing. Leaning toward the first is a
    statement about evidence, not a preference for the agent."""
    a = [hyp("catalogue", 0.5, tier=tiers.CONFIRMED)]
    f = [hyp("payment", 0.5, tier=tiers.CORRELATED)]
    w, _ = agreement_weight(a, f)
    assert w == pytest.approx(3 / 5)          # 3 (CONFIRMED) / (3 + 2)

    w2, _ = agreement_weight(f, a)            # symmetric
    assert w2 == pytest.approx(2 / 5)


def test_equally_evidenced_disagreement_does_not_invent_a_casting_vote():
    a = [hyp("catalogue", 0.5, tier=tiers.CORRELATED)]
    f = [hyp("payment", 0.9, tier=tiers.CORRELATED)]
    w, _ = agreement_weight(a, f)
    assert w == 0.5


def test_a_missing_mode_hands_the_verdict_to_the_other():
    assert agreement_weight([], [hyp("catalogue", 0.5)])[0] == 0.0
    assert agreement_weight([hyp("catalogue", 0.5)], [])[0] == 1.0


def test_the_weight_has_no_tuned_constant():
    """Guard against the obvious future mistake: fitting a magic number to the 23
    synthetic cases and reporting accuracy on those same 23. The dev split is empty
    (n=1 real case), so a fitted weight cannot be honestly validated."""
    import inspect

    from backend.rank import ensemble

    src = inspect.getsource(ensemble.agreement_weight)
    # 0.5 is a tie, not a tuning: any OTHER float literal here is a fitted parameter.
    floats = {t for t in src.replace("(", " ").replace(")", " ").split()
              if t.replace(".", "", 1).isdigit() and "." in t}
    assert floats <= {"0.5", "1.0", "0.0"}, f"a fitted-looking constant crept in: {floats}"


# =========================================================================
# the blend: still a valid hypothesis
# =========================================================================
def test_the_blend_keeps_the_breakdown_summing_to_the_score(topology, ledger):
    """RankedHypothesis enforces breakdown.total() == score to 1e-6. A fused score
    computed separately from its parts would fail validation — so the score IS the
    sum of the blended parts, never a second calculation."""
    a = [hyp("catalogue", 0.80)]
    f = [hyp("catalogue", 0.30)]
    out = _fuse(a, f, topology, ledger)
    top = out.hypotheses[0]
    assert top.score == pytest.approx(top.score_breakdown.total(), abs=1e-6)
    assert top.score == pytest.approx(0.55, abs=1e-3)      # 0.5*0.80 + 0.5*0.30


def test_a_pinned_weight_moves_the_score_the_way_it_says(topology, ledger):
    a = [hyp("catalogue", 1.0)]
    f = [hyp("catalogue", 0.0)]
    assert _fuse(a, f, topology, ledger, weight=1.0).hypotheses[0].score == pytest.approx(1.0)
    assert _fuse(a, f, topology, ledger, weight=0.0).hypotheses[0].score == pytest.approx(0.0)
    assert _fuse(a, f, topology, ledger, weight=0.25).hypotheses[0].score == pytest.approx(0.25, abs=1e-3)


def test_fusion_can_overturn_a_single_mode_top1(topology, ledger):
    """The point of the whole feature. The agent is confident about payment; the
    deterministic floor is confident about catalogue and better evidenced. Fused,
    catalogue wins — neither mode's answer copied, the arithmetic decided."""
    agentic = [hyp("payment", 0.70, rank=1, tier=tiers.CORRELATED),
               hyp("catalogue", 0.40, rank=2, tier=tiers.CORRELATED)]
    fixed = [hyp("catalogue", 0.90, rank=1, tier=tiers.CONFIRMED,
                 twin={"run": "twin-catalogue", "similarity": 0.9, "verdict": "match"}),
             hyp("payment", 0.20, rank=2, tier=tiers.CORRELATED)]

    out = _fuse(agentic, fixed, topology, ledger)
    assert out.agree is False
    assert out.top1_agentic == "payment" and out.top1_fixed == "catalogue"
    assert out.top1_ensemble == "catalogue"
    assert out.hypotheses[0].rank == 1


def test_ranks_are_recomputed_not_inherited(topology, ledger):
    agentic = [hyp("payment", 0.9, rank=1), hyp("catalogue", 0.1, rank=2)]
    fixed = [hyp("catalogue", 0.9, rank=1), hyp("payment", 0.1, rank=2)]
    out = _fuse(agentic, fixed, topology, ledger)
    assert [h.rank for h in out.hypotheses] == [1, 2]
    scores = [h.score for h in out.hypotheses]
    assert scores == sorted(scores, reverse=True)


def test_a_hypothesis_only_one_mode_ranked_keeps_its_own_numbers(topology, ledger):
    """Scoring the silent mode as 0 would invent a vote it never cast — it did not
    rank the component low, it never considered it."""
    agentic = [hyp("catalogue", 0.6), hyp("carts", 0.5)]
    fixed = [hyp("catalogue", 0.6)]
    out = _fuse(agentic, fixed, topology, ledger)
    carts = next(h for h in out.hypotheses if h.suspect_component == "carts")
    assert carts.score == pytest.approx(0.5, abs=1e-6)


def test_disagreements_are_reported_not_hidden(topology, ledger):
    agentic = [hyp("payment", 0.9, rank=1), hyp("catalogue", 0.1, rank=2)]
    fixed = [hyp("catalogue", 0.9, rank=1), hyp("payment", 0.1, rank=2)]
    out = _fuse(agentic, fixed, topology, ledger)
    assert out.disagreements
    assert any("payment" in d for d in out.disagreements)


# =========================================================================
# the rules
# =========================================================================
def test_evidence_is_merged_not_averaged(topology, ledger):
    """A twin the agent ran and the autopilot skipped is still a twin that ran.
    Averaging a real verdict against 'pending' would invent a measurement."""
    agentic = [hyp("catalogue", 0.5,
                   twin={"run": "twin-catalogue", "similarity": 0.88, "verdict": "match"})]
    fixed = [hyp("catalogue", 0.5)]           # never ran one
    out = _fuse(agentic, fixed, topology, ledger)
    assert out.hypotheses[0].twin is not None
    assert out.hypotheses[0].twin.verdict == "match"
    assert out.hypotheses[0].twin.similarity == pytest.approx(0.88)


def test_the_fused_tier_comes_from_tiers_py_not_from_either_mode(topology, ledger, monkeypatch):
    """Rule 5: tiers are assigned in exactly one place. If the ensemble ever copies a
    tier across instead of re-deriving it, this fails."""
    called: list[str] = []
    real = tiers.assign_tier

    def spy(ctx, **kw):
        called.append(ctx.suspect)
        res = real(ctx, **kw)
        res.tier, res.reason = tiers.MISSING_EVIDENCE, "stamped by tiers.py"
        return res

    monkeypatch.setattr(tiers, "assign_tier", spy)
    a = [hyp("catalogue", 0.5, tier=tiers.CONFIRMED)]
    f = [hyp("catalogue", 0.5, tier=tiers.CONFIRMED)]
    out = _fuse(a, f, topology, ledger)

    assert called == ["catalogue"]
    assert out.hypotheses[0].tier == tiers.MISSING_EVIDENCE, "a tier was copied, not assigned"
    assert out.hypotheses[0].tier_reason == "stamped by tiers.py"


def test_the_fused_verdict_is_filed_to_the_ledger(topology, ledger):
    """Rule 12's paper trail: the verdict is arithmetic over the ledger, and the
    arithmetic records itself."""
    out = _fuse([hyp("catalogue", 0.8)], [hyp("catalogue", 0.4)], topology, ledger)
    facts = ledger.query(kind="hypothesis_scored", limit=50)
    assert facts and any("ENSEMBLE" in f.statement for f in facts)
    assert any(f"{out.hypotheses[0].score:.3f}" in f.statement for f in facts)


# =========================================================================
# merging the two ledgers
# =========================================================================
def test_merge_refiles_with_canonical_ids_and_dedupes(tmp_path):
    """Both modes number facts per component from 0, so `fact-catalogue-0000` exists in
    both and means different things. Copying ids verbatim would make a citation
    ambiguous — the one thing the narrator's validator cannot survive."""
    a = Ledger("run-1", "case-001", tmp_path / "a", fresh=True)
    f = Ledger("run-1", "case-001", tmp_path / "f", fresh=True)
    canonical = Ledger("run-1", "case-001", tmp_path / "c", fresh=True)

    # the SAME deterministic counterfactual, run by both engines
    same = "Removing catalogue leaves 58% of anomalies explained by other candidates"
    a.counterfactual_result(same, ["catalogue"], (0.0, 1.0), "hyp-catalogue-01")
    f.counterfactual_result(same, ["catalogue"], (0.0, 1.0), "hyp-catalogue-01")
    # ...and one only the agent bought
    a.twin_result("Twin for catalogue: verdict=match, similarity=0.9.", ["catalogue"],
                  (0.0, 1.0), "hyp-catalogue-01")
    assert a.query(kind="counterfactual_result")[0].fact_id == \
        f.query(kind="counterfactual_result")[0].fact_id, "ids collide across modes (the premise)"

    filed = merge_derived_facts(canonical, [a, f])
    assert filed == 2, "the duplicate counterfactual was filed twice"

    ids = [x.fact_id for x in canonical.query(limit=50)]
    assert len(ids) == len(set(ids)), "canonical ledger minted a duplicate id"
    kinds = {x.kind for x in canonical.query(limit=50)}
    assert kinds == {"counterfactual_result", "twin_result"}


def test_merge_does_not_carry_over_each_modes_scores(tmp_path):
    """hypothesis_scored is superseded by the fused score. Merging both modes' would
    leave three contradictory scores per component for the narrator to explain."""
    a = Ledger("run-1", "case-001", tmp_path / "a", fresh=True)
    canonical = Ledger("run-1", "case-001", tmp_path / "c", fresh=True)
    a.hypothesis_scored("catalogue scored 0.9, tier=CORRELATED.", ["catalogue"],
                        (0.0, 0.0), "hyp-catalogue-01")
    a.counterfactual_result("Removing catalogue leaves 58%.", ["catalogue"], (0.0, 1.0),
                            "hyp-catalogue-01")

    merge_derived_facts(canonical, [a])
    assert not canonical.query(kind="hypothesis_scored", limit=10)
    assert canonical.query(kind="counterfactual_result", limit=10)
