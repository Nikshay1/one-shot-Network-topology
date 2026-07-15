"""Real token accounting and a hard spend cap for the OpenAI path.

Two jobs, both of which used to be missing:

1. **Measure.** `eval/run_benchmark.py` reported `usd_per_case_measured: None`
   whenever a key was present — the prices were listed but nothing ever counted a
   token, so the "LLM cost note per case" was a price list, not a measurement.
   Every `OpenAIClient` call now records real `usage` off the response.

2. **Cap.** Rule 10 says budgets are enforced in code, never by prompt text. The
   per-agent `Budget` bounds *tool calls*, which is not the same thing as bounding
   *dollars*: a single agent can burn its way through a large context 10 times over
   and never trip a call counter. `SpendCap` is the dollar-denominated sibling.

The cap is deliberately enforced BEFORE the request goes out, and raises. The
harness catches it like any other LLM failure, so rule 11 applies unchanged: an
exhausted budget degrades to the deterministic autopilot and the run still
completes with a valid verdict. Running out of money is a demotion, not a crash.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

# USD per 1M tokens, (input, output). The single source of truth for pricing —
# eval/run_benchmark.py imports these rather than keeping its own copy.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}

DEFAULT_CAP_ENV = "VERDICT_SPEND_CAP_USD"


class SpendCap(Exception):
    """Raised before a request that would exceed the configured USD ceiling."""

    def __init__(self, spent: float, cap: float) -> None:
        self.spent = spent
        self.cap = cap
        super().__init__(f"spend cap reached: ${spent:.4f} of ${cap:.2f} — refusing "
                         f"further LLM calls (set {DEFAULT_CAP_ENV} to raise it)")


def price(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD for one call. Unknown models price at gpt-4o (the expensive one) so a
    typo can never make a run look cheaper than it was."""
    pin, pout = PRICES.get(model, PRICES["gpt-4o"])
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000


@dataclass
class ModelUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0


@dataclass
class Meter:
    """Process-wide token/cost meter. Thread-safe: the pipeline runs in
    `asyncio.to_thread` while the API loop serves SSE, so this is touched from
    more than one thread."""

    cap_usd: float | None = None
    by_model: dict[str, ModelUsage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- accounting -------------------------------------------------------
    def record(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        cost = price(model, prompt_tokens, completion_tokens)
        with self._lock:
            m = self.by_model.setdefault(model, ModelUsage())
            m.calls += 1
            m.prompt_tokens += prompt_tokens
            m.completion_tokens += completion_tokens
            m.usd += cost
        return cost

    @property
    def usd(self) -> float:
        with self._lock:
            return sum(m.usd for m in self.by_model.values())

    @property
    def calls(self) -> int:
        with self._lock:
            return sum(m.calls for m in self.by_model.values())

    # -- the cap ----------------------------------------------------------
    def check(self) -> None:
        """Raise if the NEXT call would run past the ceiling. Checked before the
        request, because a request that has already been sent has already been paid
        for — checking afterwards would cap nothing."""
        if self.cap_usd is None:
            return
        spent = self.usd
        if spent >= self.cap_usd:
            raise SpendCap(spent, self.cap_usd)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "usd": round(sum(m.usd for m in self.by_model.values()), 6),
                "cap_usd": self.cap_usd,
                "calls": sum(m.calls for m in self.by_model.values()),
                "prompt_tokens": sum(m.prompt_tokens for m in self.by_model.values()),
                "completion_tokens": sum(m.completion_tokens for m in self.by_model.values()),
                "by_model": {
                    k: {"calls": v.calls, "prompt_tokens": v.prompt_tokens,
                        "completion_tokens": v.completion_tokens, "usd": round(v.usd, 6)}
                    for k, v in sorted(self.by_model.items())
                },
            }

    def reset(self) -> None:
        with self._lock:
            self.by_model.clear()


def _cap_from_env() -> float | None:
    raw = os.getenv(DEFAULT_CAP_ENV)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


METER = Meter(cap_usd=_cap_from_env())


def reload_cap() -> float | None:
    """Re-read the ceiling from the environment (the env is loaded from .env at
    import time, but tests and CLIs set it later)."""
    METER.cap_usd = _cap_from_env()
    return METER.cap_usd
