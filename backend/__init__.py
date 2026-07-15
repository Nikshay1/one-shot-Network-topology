"""VERDICT backend package.

Loading `.env` here — at the package root — is deliberate. Rule 7 says every
module must be runnable via CLI without the API server, so no single entry point
could own this: `backend.main`, `backend.agents.investigator`,
`backend.narrate.cache` and a dozen test modules are each a first-import depending
on how you enter. Importing `backend` is the one thing they provably share.

This was a real bug. `python-dotenv` was declared in pyproject and README §Bring-up
told you to put `OPENAI_API_KEY` in `.env` — but nothing ever called `load_dotenv`,
so the file was inert. The key was silently absent, every agent resolved to no-LLM,
and rule 11 quietly ran the autopilot: a green run, a valid verdict, and no agent
anywhere in it. The failure mode of a missing key is *silence*, which is exactly
why this went unnoticed for nine steps.

`override=False` keeps a real exported environment variable winning over the file.
"""

from __future__ import annotations


def _load_env() -> None:
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:                          # pragma: no cover - optional at runtime
        return
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)


_load_env()
