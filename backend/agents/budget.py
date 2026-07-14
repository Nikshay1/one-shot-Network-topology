"""Harness-enforced agent budget (rule 10: enforced in code, never by prompt).

Counts every tool call, sums cost points, and enforces a wall-clock deadline.
The clock is injectable so budgets can be unit-tested with no LLM and no real
time passing.
"""

from __future__ import annotations

import time
from typing import Callable


class BudgetExceeded(Exception):
    def __init__(self, reason: str, limit: float | int | None = None,
                 attempted: float | int | None = None) -> None:
        self.reason = reason
        self.limit = limit
        self.attempted = attempted
        super().__init__(f"budget exceeded: {reason} (limit={limit}, attempted={attempted})")


class Budget:
    def __init__(
        self,
        max_calls: int = 10,
        max_cost_points: int = 3,
        wall_clock_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_calls = max_calls
        self.max_cost_points = max_cost_points
        self.wall_clock_s = wall_clock_s
        self._clock = clock
        self._start: float | None = None
        self.calls = 0
        self.cost = 0

    def start(self) -> "Budget":
        self._start = self._clock()
        return self

    def elapsed(self) -> float:
        return 0.0 if self._start is None else self._clock() - self._start

    def charge(self, cost_points: int = 0, label: str = "") -> "Budget":
        """Account for one tool call; raise BudgetExceeded if any limit trips.
        Nothing is incremented when a limit trips."""
        if self._start is None:
            self._start = self._clock()
        elapsed = self.elapsed()
        if elapsed > self.wall_clock_s:
            raise BudgetExceeded("wall_clock", self.wall_clock_s, round(elapsed, 3))
        if self.calls + 1 > self.max_calls:
            raise BudgetExceeded("max_calls", self.max_calls, self.calls + 1)
        if self.cost + cost_points > self.max_cost_points:
            raise BudgetExceeded("max_cost_points", self.max_cost_points, self.cost + cost_points)
        self.calls += 1
        self.cost += cost_points
        return self

    def wrap(self, fn: Callable, cost_points: int, label: str = "") -> Callable:
        def wrapped(*args, **kwargs):
            self.charge(cost_points, label=label or getattr(fn, "__name__", ""))
            return fn(*args, **kwargs)
        return wrapped

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "cost": self.cost,
            "max_cost_points": self.max_cost_points,
            "elapsed_s": round(self.elapsed(), 3),
            "wall_clock_s": self.wall_clock_s,
        }
