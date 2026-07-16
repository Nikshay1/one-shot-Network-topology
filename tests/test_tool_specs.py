"""What the MODEL is told about the tools.

The function spec is the whole contract between the harness and the model, and it
is generated — `tool_specs` turns the input model's JSON schema into parameters and
the fn's docstring into the description. Both halves have silently failed:

- `kind` was a free `str` with no enum, so the narrator guessed `kind="fact"`, got
  `{"records": []}`, decided the run had no evidence and filed a report of six
  empty headings. Nothing errored. The filter did exactly what it was told.
- Several fns had no docstring, so their description degraded to the bare tool
  name — "query_evidence_ledger", which tells a model nothing at all.

Neither is visible on the deterministic path, which is why the whole suite stayed
green while a live report came out blank. These tests assert on the spec itself.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents.harness import tool_specs
from backend.agents.tools import REGISTRY, GetLedgerIn
from backend.models import LedgerKind

try:                                    # py3.8+ / 3.11
    from typing import get_args
except ImportError:                     # pragma: no cover
    from typing_extensions import get_args


def _spec(name: str) -> dict:
    return tool_specs([name])[0]["function"]


def test_every_tool_describes_itself_to_the_model():
    """A description that is just the tool's own name is not a description. The model
    only knows what this string tells it."""
    naked = []
    for name in REGISTRY:
        d = _spec(name)["description"].strip()
        if not d or d == name:
            naked.append(name)
    assert not naked, f"tools whose description is just their name: {naked}"


def test_the_ledger_kind_filter_is_an_enum_not_a_free_string():
    """REGRESSION: the narrator cannot invent a kind if the schema enumerates them.
    This is the difference between a model guessing and a model choosing."""
    kind = _spec("query_evidence_ledger")["parameters"]["properties"]["kind"]
    enums = [e for branch in kind.get("anyOf", [kind]) for e in branch.get("enum", [])]
    assert set(enums) == set(get_args(LedgerKind)), (
        "the model is not shown the real ledger kinds, so it will guess one"
    )


@pytest.mark.parametrize("guess", ["fact", "hypothesis", "anomaly", "evidence", "all"])
def test_the_exact_guesses_that_emptied_a_report_are_now_rejected(guess):
    """`kind="fact"` and `kind="hypothesis"` are what the live narrator actually sent.
    They matched nothing, silently. Now they cannot be sent, and if they somehow are,
    the model gets an error it can act on instead of a false emptiness."""
    with pytest.raises(ValidationError):
        GetLedgerIn.model_validate({"kind": guess})


def test_a_real_kind_still_passes():
    """Guard the guard: a validator that rejects everything would pass the test above."""
    assert GetLedgerIn.model_validate({"kind": "anomaly_observed"}).kind == "anomaly_observed"
    assert GetLedgerIn.model_validate({}).kind is None      # no filter = all the evidence


def test_the_ledger_tool_says_that_empty_means_no_match_not_no_evidence():
    """The failure was a misreading: [] meant 'nothing matched THESE filters', and the
    model read it as 'this run has no evidence'. The description now says which."""
    d = _spec("query_evidence_ledger")["description"].lower()
    assert "empty" in d and "filter" in d
