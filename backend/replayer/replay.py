"""Replay a stored case as a live event stream.

Events are ordered by ``(ts, event_id)`` — `ts` alone is not a total order (a
case emits many events per second), and the tie-break keeps two replays of the
same case byte-identical.

`speed` compresses the original inter-arrival gaps:

    speed=1   real time          speed=10  10x faster      speed=0  instant batch

`speed=0` is the eval path: no sleeping at all, so a benchmark over the held-out
split is not gated on wall-clock.

DETECTION IS BATCH, BY DESIGN
-----------------------------
The UI receives `event_ingested` DURING the replay, but detection only runs once
the stream has ended. The detectors are batch estimators — MAD needs the whole
baseline window, IsolationForest fits over all windows, Drain3 needs the full log
corpus — so a partial stream would produce different (and worse) anomalies than
the offline pipeline. The stream is a visualization of ingest; the verdict is
computed on the complete case.

    py -m backend.replayer.replay --case clean_cascade-01 --speed 0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from backend.ingest.store import EventStore

# The stream is a demo surface, not a data channel: a real RE2-SS case carries
# ~186k events and pushing every one down SSE both drowns the agent_step events
# the operator is actually watching and pins the browser. Streams are capped —
# and the cap is REPORTED (ReplayStats.dropped), never silent.
DEFAULT_MAX_STREAM_EVENTS = 2000

# Never sleep longer than this between two events, whatever the gap says.
MAX_GAP_S = 2.0


@dataclass
class ReplayStats:
    case_id: str
    total: int
    streamed: int
    dropped: int
    speed: float
    wall_clock_s: float
    # ids actually put on the wire — the caller needs this to honour the contract's
    # ordering guarantee #1 for anomalies citing events the cap dropped.
    streamed_ids: set[str] = field(default_factory=set)

    @property
    def truncated(self) -> bool:
        return self.dropped > 0

    def note(self) -> str:
        if not self.truncated:
            return f"replayed {self.streamed}/{self.total} events at speed={self.speed}"
        return (f"replayed {self.streamed}/{self.total} events at speed={self.speed} "
                f"— {self.dropped} events beyond the stream cap were NOT streamed "
                f"(detection still runs on all {self.total})")


class Replayer:
    """Ordered, speed-compressed, deterministic replay of one case's events."""

    def __init__(
        self,
        store: EventStore,
        case_id: str,
        *,
        speed: float = 1.0,
        seed: int = 0,
        max_stream_events: int | None = DEFAULT_MAX_STREAM_EVENTS,
    ) -> None:
        self.store = store
        self.case_id = case_id
        self.speed = max(0.0, float(speed))
        self.seed = int(seed)            # replay is deterministic; seed is carried for the run record
        self.max_stream_events = max_stream_events

    def rows(self) -> list[dict]:
        """Every event of the case in (ts, event_id) order."""
        df = self.store.events(self.case_id)
        if not df.height:
            return []
        return df.sort(["ts", "event_id"]).to_dicts()

    @staticmethod
    def envelope(row: dict) -> dict:
        """Rebuild the EventEnvelope shape the contract puts on `event_ingested`."""
        return {
            "event_id": row["event_id"],
            "case_id": row["case_id"],
            "source": row["source"],
            "component_id": row["component_id"],
            "ts": row["ts"],
            "payload": json.loads(row["payload_json"]),
        }

    async def stream(self, emit: Callable[[str, dict], None] | None = None) -> ReplayStats:
        """Emit `event_ingested` per event, pacing by `speed`. Returns what was sent."""
        rows = self.rows()
        total = len(rows)
        cap = self.max_stream_events if self.max_stream_events is not None else total
        sent = rows[:cap]
        t0 = time.monotonic()

        prev_ts: float | None = None
        streamed_ids: set[str] = set()
        for row in sent:
            if self.speed > 0 and prev_ts is not None:
                gap = (row["ts"] - prev_ts) / self.speed
                if gap > 0:
                    await asyncio.sleep(min(gap, MAX_GAP_S))
            prev_ts = row["ts"]
            streamed_ids.add(row["event_id"])
            if emit is not None:
                emit("event_ingested", self.envelope(row))
            if self.speed == 0 and emit is not None:
                # instant batch: still yield so a subscriber's queue drains
                await asyncio.sleep(0)

        return ReplayStats(case_id=self.case_id, total=total, streamed=len(sent),
                           dropped=total - len(sent), speed=self.speed,
                           wall_clock_s=round(time.monotonic() - t0, 3),
                           streamed_ids=streamed_ids)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="replayer.replay")
    ap.add_argument("--case", required=True)
    ap.add_argument("--speed", type=float, default=0.0, help="0 = instant batch")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--store", default="data/parquet")
    ap.add_argument("--cap", type=int, default=DEFAULT_MAX_STREAM_EVENTS)
    ap.add_argument("--print-first", type=int, default=3)
    args = ap.parse_args(argv[1:])

    seen: list[dict] = []
    r = Replayer(EventStore(args.store), args.case, speed=args.speed, seed=args.seed,
                 max_stream_events=args.cap)
    stats = asyncio.run(r.stream(lambda name, data: seen.append(data)))
    print(stats.note())
    print(f"wall_clock={stats.wall_clock_s}s ordered_by=(ts,event_id)")
    for e in seen[: args.print_first]:
        print(f"  {e['ts']:.3f}  {e['event_id']:<28} {e['component_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
