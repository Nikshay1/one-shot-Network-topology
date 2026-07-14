"""SimPy digital twin built FROM topology.json.

Each service is a pool of capacity slots (a ``simpy.Container`` so faults and
remedies can resize it mid-sim). Requests enter at the front door at the
pre-incident throughput and traverse the call graph SYNCHRONOUSLY — a service
holds a slot while it waits on its callees, so a downstream bottleneck cascades
latency upward. Per-component windowed vectors
``[latency_mean, latency_p95, error_rate, throughput, utilization]`` are recorded.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field

import networkx as nx
import simpy

FEATURES = ["latency_mean", "latency_p95", "error_rate", "throughput", "utilization"]
_WINDOW_S = 5.0


@dataclass
class NodeState:
    service_mult: float = 1.0
    inbound_delay: float = 0.0
    drop_p: float = 0.0
    down: bool = False


@dataclass
class Calibration:
    base_service: dict[str, float] = field(default_factory=dict)
    capacity: dict[str, int] = field(default_factory=dict)
    arrival_rate: float = 30.0
    call_weights: dict[tuple[str, str], float] = field(default_factory=dict)


class TwinModel:
    def __init__(self, topology: nx.DiGraph, calibration: Calibration | None = None,
                 seed: int = 0, entry: str | None = None) -> None:
        self.topology = topology
        self.cal = calibration or Calibration()
        self.seed = seed
        self.entry = entry or self._pick_entry()
        self.window_s = _WINDOW_S
        self._records: list[tuple[str, float, float, bool]] = []  # node, t_done, latency, err
        self._busy: dict[str, float] = {}

    # -- calibration defaults --------------------------------------------
    def _pick_entry(self) -> str:
        for cand in ("loadgenerator", "front-end"):
            if cand in self.topology:
                return cand
        # otherwise a source node (no in-edges) or any node
        srcs = [n for n in self.topology if self.topology.in_degree(n) == 0]
        return srcs[0] if srcs else next(iter(self.topology.nodes))

    def _cap(self, node: str) -> int:
        if node in self.cal.capacity:
            return max(1, int(self.cal.capacity[node]))
        if node == self.entry:
            return 500
        return 4

    def _svc(self, node: str) -> float:
        return float(self.cal.base_service.get(node, 0.015))

    def _weight(self, u: str, v: str) -> float:
        return float(self.cal.call_weights.get((u, v), self.topology[u][v].get("weight", 1.0)))

    def _callees(self, node: str) -> list[str]:
        return [v for v in self.topology.successors(node)
                if self.topology.nodes[v].get("instrumented", True) or True]

    # -- simulation ------------------------------------------------------
    def _serve(self, node: str):
        t0 = self.env.now
        st = self.state[node]
        slot = self.slots[node]
        yield slot.get(1)                       # queue for a capacity slot
        down = st.down
        if not down:
            if st.inbound_delay:
                yield self.env.timeout(st.inbound_delay)
            service = self._svc(node) * st.service_mult * self.rng.uniform(0.85, 1.15)
            yield self.env.timeout(service)
            self._busy[node] = self._busy.get(node, 0.0) + service
        slot.put(1)                             # release before downstream calls
        errored = down or (self.rng.random() < st.drop_p)
        if not errored:
            for callee in self._callees(node):  # synchronous downstream calls
                if self.rng.random() <= min(1.0, self._weight(node, callee)):
                    sub = yield self.env.process(self._serve(callee))
                    errored = errored or sub
        self._records.append((node, self.env.now, self.env.now - t0, errored))
        return errored

    def _generator(self):
        rate = max(1e-6, self.cal.arrival_rate * self.rate_mult)
        while True:
            yield self.env.timeout(self.rng.expovariate(rate))
            self.env.process(self._serve(self.entry))

    def run(self, duration: float, hooks: list[tuple[float, callable]] | None = None) -> None:
        """Run the sim for `duration` sim-seconds. `hooks` = (at_time, fn(model))
        callbacks scheduled during the run (fault/remedy application)."""
        self.env = simpy.Environment()
        self.rng = random.Random(self.seed)
        self.rate_mult = 1.0
        self.state = {n: NodeState() for n in self.topology.nodes}
        self.capacity = {n: self._cap(n) for n in self.topology.nodes}
        self.slots = {n: simpy.Container(self.env, init=self.capacity[n], capacity=100_000)
                      for n in self.topology.nodes}
        self._records = []
        self._busy = {}
        self.held: dict[str, int] = {}
        self.env.process(self._generator())
        for at, fn in sorted(hooks or [], key=lambda h: h[0]):
            self.env.process(self._schedule(at, fn))
        self.env.run(until=duration)
        self.duration = duration

    def _schedule(self, at: float, fn):
        yield self.env.timeout(at)
        fn(self)

    # -- metrics ---------------------------------------------------------
    def aggregate(self, node: str, t0: float, t1: float) -> list[float]:
        """Feature vector for `node` over sim window [t0, t1)."""
        lat = [lt for (n, td, lt, e) in self._records if n == node and t0 <= td < t1]
        errs = [e for (n, td, lt, e) in self._records if n == node and t0 <= td < t1]
        span = max(1e-6, t1 - t0)
        if not lat:
            return [0.0, 0.0, 0.0, 0.0, 0.0]
        latency_mean = statistics.fmean(lat)
        p95 = sorted(lat)[max(0, int(0.95 * len(lat)) - 1)]
        error_rate = sum(1 for e in errs if e) / len(errs)
        throughput = len(lat) / span
        util = min(1.0, throughput * latency_mean / max(1, self.capacity[node]))
        return [latency_mean, p95, error_rate, throughput, util]

    def components(self) -> list[str]:
        return sorted({n for (n, _, _, _) in self._records})
