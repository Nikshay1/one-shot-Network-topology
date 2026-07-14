"""The SSE bus: one ordered event log per run, fanned out to N subscribers.

Ordering (contract §"Ordering guarantees") is structural, not best-effort: every
event goes through ONE bus in publish order, and each subscriber replays that
same buffer from index 0. So `event_ingested` always precedes the
`anomaly_detected` that cites it, and `pipeline_done` / `pipeline_error` is
always last — a subscriber that connects late sees the identical sequence.

The pipeline is synchronous and runs in a worker thread, so `publish` is
thread-safe (`call_soon_threadsafe` back onto the API's loop).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator

HEARTBEAT_S = 15.0
TERMINAL = ("pipeline_done", "pipeline_error")


@dataclass
class BusEvent:
    name: str
    data: dict


@dataclass
class RunBus:
    """An append-only, replayable event log for one run."""

    run_id: str
    loop: asyncio.AbstractEventLoop | None = None
    log: list[BusEvent] = field(default_factory=list)
    done: bool = False
    _subscribers: set[asyncio.Queue] = field(default_factory=set)

    # -- producer side ----------------------------------------------------
    def publish(self, name: str, data: dict) -> None:
        """Thread-safe. Called from the pipeline worker thread via `emit`."""
        if self.loop is not None and self.loop.is_running():
            try:
                self.loop.call_soon_threadsafe(self._publish_now, name, data)
                return
            except RuntimeError:                            # loop closed mid-run
                pass
        self._publish_now(name, data)

    def _publish_now(self, name: str, data: dict) -> None:
        if self.done:                                       # nothing follows a terminal event
            return
        ev = BusEvent(name, data)
        self.log.append(ev)
        if name in TERMINAL:
            self.done = True
        for q in list(self._subscribers):
            q.put_nowait(ev)

    def emitter(self):
        """The `emit(name, data)` callable the pipeline stages expect."""
        return lambda name, data: self.publish(name, dict(data))

    # -- consumer side ----------------------------------------------------
    async def subscribe(self) -> AsyncIterator[BusEvent | None]:
        """Replay the buffer, then follow live. Yields None as a heartbeat tick."""
        q: asyncio.Queue = asyncio.Queue()
        # Snapshot and subscribe with NO await between them: a publish from the
        # worker thread lands via call_soon_threadsafe, which cannot run until this
        # coroutine yields. So the backlog and the queue cannot overlap or gap.
        backlog = list(self.log)
        already_done = self.done
        self._subscribers.add(q)
        try:
            for ev in backlog:
                yield ev
            if already_done:
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield None                              # keep the connection warm
                    continue
                yield ev
                if ev.name in TERMINAL:
                    return
        finally:
            self._subscribers.discard(q)


class BusRegistry:
    """run_id -> RunBus."""

    def __init__(self) -> None:
        self._buses: dict[str, RunBus] = {}

    def create(self, run_id: str, loop: asyncio.AbstractEventLoop | None = None) -> RunBus:
        bus = RunBus(run_id=run_id, loop=loop)
        self._buses[run_id] = bus
        return bus

    def get(self, run_id: str) -> RunBus | None:
        return self._buses.get(run_id)

    def clear(self) -> None:
        self._buses.clear()
