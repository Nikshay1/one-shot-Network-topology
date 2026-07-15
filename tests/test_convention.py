"""Edge-direction tripwire (rule 17).

The frozen convention: an edge A->B means "A calls/depends on B". Failures
therefore propagate UPSTREAM, to ancestors:

    symptoms S explains        = nx.ancestors(G, S) | {S}
    possible root causes of X  = nx.descendants(G, X) | {X}

If someone ever flips an edge — in the adapter, the scenario generator, the
scorer, or the twin — these tests scream. That is the whole point of the file:
it is cheap, it is unglamorous, and it is the only thing standing between this
codebase and a silently inverted causal model.

It asserts against networkx AND against the live scorer/blast helpers, because a
docstring cannot be executed.
"""

from __future__ import annotations

import inspect

import networkx as nx

from backend.localize.blast import reachable_upstream
from backend.rank import scorer


def _fixture() -> nx.DiGraph:
    """front-end -> {catalogue -> catalogue-db, payment}."""
    g = nx.DiGraph()
    g.add_edge("front-end", "catalogue")
    g.add_edge("catalogue", "catalogue-db")
    g.add_edge("front-end", "payment")
    return g


def test_ancestors_are_the_symptom_carriers() -> None:
    """catalogue-db breaking shows up at catalogue and front-end, nowhere else."""
    g = _fixture()
    assert nx.ancestors(g, "catalogue-db") == {"catalogue", "front-end"}


def test_descendants_are_the_possible_causes() -> None:
    """front-end looking sick could be caused by anything it (transitively) calls."""
    g = _fixture()
    assert nx.descendants(g, "front-end") == {"catalogue", "catalogue-db", "payment"}


def test_leaf_has_no_causes_and_root_has_no_symptoms() -> None:
    g = _fixture()
    # Nothing calls front-end, so it carries no one else's symptoms.
    assert nx.ancestors(g, "front-end") == set()
    # catalogue-db calls nothing, so nothing downstream can be blamed for it.
    assert nx.descendants(g, "catalogue-db") == set()


def test_payment_is_not_upstream_of_catalogue_db() -> None:
    """The red herring's structural core: a sibling is never a suspect.

    payment and catalogue-db are both children of front-end. Neither can explain
    the other, in either direction.
    """
    g = _fixture()
    assert "payment" not in nx.ancestors(g, "catalogue-db")
    assert "payment" not in nx.descendants(g, "catalogue-db")
    assert not nx.has_path(g, "payment", "catalogue-db")


def test_live_scorer_reach_follows_the_convention() -> None:
    """The scorer's own reach set, not a docstring — this is the load-bearing one."""
    g = _fixture()
    assert scorer._reach(g, "catalogue-db") == {"catalogue-db", "catalogue", "front-end"}
    assert scorer._reach(g, "front-end") == {"front-end"}


def test_live_blast_reachable_upstream_follows_the_convention() -> None:
    g = _fixture()
    assert reachable_upstream(g, "catalogue-db") == {"catalogue-db", "catalogue", "front-end"}


def test_scorer_documents_the_convention() -> None:
    """A reader of scorer.py must be told which way the edges point."""
    src = inspect.getsource(scorer)
    assert "ancestors" in src, "scorer must reach via ancestors, not descendants"
    reach_doc = inspect.getdoc(scorer._reach) or ""
    assert "calls" in reach_doc.lower() or "upstream" in reach_doc.lower(), (
        "scorer._reach must document the edge convention"
    )
