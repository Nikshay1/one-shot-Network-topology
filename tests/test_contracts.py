"""Golden contract tests.

Every fixture validates against its Draft-7 JSON Schema, and every fixture
also round-trips through the corresponding pydantic model. The pre-weighted
score_breakdown of each hypothesis is asserted to sum to its score.
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest
from jsonschema import Draft7Validator

from backend.models import (
    AnomalyEvent,
    EventEnvelope,
    LedgerRecord,
    RankedHypothesis,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
FIXTURES = ROOT / "fixtures"

# (fixture file, schema file, pydantic model) — every array fixture with a schema.
CASES = [
    ("sample_events.json", "event_envelope.schema.json", EventEnvelope),
    ("sample_anomalies.json", "anomaly_event.schema.json", AnomalyEvent),
    ("sample_hypotheses.json", "ranked_hypothesis.schema.json", RankedHypothesis),
    ("sample_ledger.json", "ledger_record.schema.json", LedgerRecord),
]


def _load(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("fixture,schema,_model", CASES)
def test_schema_itself_is_valid_draft7(fixture: str, schema: str, _model) -> None:
    Draft7Validator.check_schema(_load(CONTRACTS / schema))


@pytest.mark.parametrize("fixture,schema,_model", CASES)
def test_fixture_validates_against_schema(fixture: str, schema: str, _model) -> None:
    validator = Draft7Validator(_load(CONTRACTS / schema))
    items = _load(FIXTURES / fixture)
    assert isinstance(items, list) and items, f"{fixture} must be a non-empty array"
    for i, item in enumerate(items):
        errors = sorted(validator.iter_errors(item), key=lambda e: e.path)
        assert not errors, f"{fixture}[{i}] invalid: {[e.message for e in errors]}"


@pytest.mark.parametrize("fixture,_schema,model", CASES)
def test_fixture_roundtrips_through_pydantic(fixture: str, _schema: str, model) -> None:
    for i, item in enumerate(_load(FIXTURES / fixture)):
        obj = model.model_validate(item)
        # dumped model, re-validated, must be stable
        model.model_validate(obj.model_dump(mode="json"))
        assert obj is not None, f"{fixture}[{i}] failed to build"


def test_fixture_counts() -> None:
    assert len(_load(FIXTURES / "sample_events.json")) == 20
    assert len(_load(FIXTURES / "sample_anomalies.json")) == 6
    assert len(_load(FIXTURES / "sample_hypotheses.json")) == 3
    assert len(_load(FIXTURES / "sample_ledger.json")) == 12


def test_events_cover_all_five_sources() -> None:
    sources = {e["source"] for e in _load(FIXTURES / "sample_events.json")}
    assert sources == {"metric", "log", "alert", "topology", "config"}


def test_hypotheses_cover_every_tier() -> None:
    tiers = {h["tier"] for h in _load(FIXTURES / "sample_hypotheses.json")}
    assert tiers == {"CONFIRMED", "CORRELATED", "MISSING_EVIDENCE"}


def test_score_breakdown_sums_to_score() -> None:
    for h in _load(FIXTURES / "sample_hypotheses.json"):
        total = sum(h["score_breakdown"].values())
        assert abs(total - h["score"]) < 1e-6, (
            f"{h['hypothesis_id']}: breakdown sums to {total}, score is {h['score']}"
        )


def test_ledger_has_required_kinds() -> None:
    kinds = {r["kind"] for r in _load(FIXTURES / "sample_ledger.json")}
    assert "anomaly_absent" in kinds
    assert "topology_no_path" in kinds


def test_topology_loads_as_networkx_graph() -> None:
    data = _load(FIXTURES / "sample_topology.json")
    try:
        g = nx.node_link_graph(data, edges="links")
    except TypeError:  # older networkx without the `edges` kwarg
        g = nx.node_link_graph(data)
    assert g.is_directed()
    # Full sock-shop topology: the 15 pods present in the RE2-SS pod-node CSVs.
    assert g.number_of_nodes() == 15
    assert g.number_of_edges() == len(data["links"])
    assert "catalogue-db" in g.nodes
    # every faulted service in RE2-SS (carts/catalogue/orders/payment/user) is a node
    for svc in ("carts", "catalogue", "orders", "payment", "user"):
        assert svc in g.nodes
    # every node id is a valid component_id
    import re

    comp_re = re.compile(r"^[a-z0-9][a-z0-9-]*$")
    for node in g.nodes:
        assert comp_re.match(node), f"topology node {node!r} is not a valid component_id"
