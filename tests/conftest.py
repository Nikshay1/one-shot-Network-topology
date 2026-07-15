"""Test-suite-wide guarantees.

The important one: **the tests never spend money.**

This became load-bearing the moment `backend/__init__.py` started honouring `.env`.
Before that, `OPENAI_API_KEY` was never loaded into any process, so the suite was
hermetic by accident — `recommend()`, `narrate()` and friends all gate on
`os.getenv("OPENAI_API_KEY")`, and the variable was simply never there. Loading
`.env` silently flipped that: a plain `pytest` on a machine with a populated `.env`
issues real, billed gpt-4o calls, and the only symptom is a slower test run and a
smaller balance. (Observed, not theorised: `tests/test_api.py` drove a full live
investigator run this way before this file existed.)

The key is therefore removed in `pytest_configure` — before collection, before any
fixture of any scope. That timing is the whole point. The obvious implementation is
an autouse fixture, and it is WRONG: autouse fixtures are function-scoped, and
`tests/test_api.py` builds its `env`/`run` fixtures at **session** scope. Session
fixtures are instantiated before function-scoped ones, so the fixture strips the key
only after the expensive run it was supposed to prevent has already been billed.

Tests that want the live path opt in with `@pytest.mark.live`, which is deselected
unless `--live` is passed. Opt-in, never opt-out: a test that forgets the marker
fails closed (no key) rather than failing expensive.
"""

from __future__ import annotations

import os

import pytest

_KEY = "OPENAI_API_KEY"
_STASHED: str | None = None


def pytest_addoption(parser) -> None:
    parser.addoption("--live", action="store_true", default=False,
                     help="run @pytest.mark.live tests against the real OpenAI API (COSTS MONEY)")


def pytest_configure(config) -> None:
    """Runs before collection and before every fixture — the only safe moment."""
    global _STASHED
    config.addinivalue_line(
        "markers", "live: hits the real OpenAI API; costs money; needs --live")

    # Import `backend` FIRST, then strip. `backend/__init__` calls load_dotenv() at
    # import time, and collection imports it a moment later — so popping the key
    # before that import just gets it put straight back by the .env load, and the
    # session fixtures see a live key anyway. Forcing the load here makes this pop
    # the last word: `backend` is already in sys.modules, so its import (and its
    # load_dotenv) will not run a second time.
    import backend  # noqa: F401

    _STASHED = os.environ.pop(_KEY, None)


def pytest_collection_modifyitems(config, items) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (costs money)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def live_key(request) -> str:
    """Hand the real key back, for the duration of ONE @pytest.mark.live test."""
    if _STASHED is None:
        pytest.skip(f"no {_KEY} available")
    os.environ[_KEY] = _STASHED
    request.addfinalizer(lambda: os.environ.pop(_KEY, None))
    return _STASHED
