"""Blast radius: a k-hop subgraph around the anomalous components.

The dependency graph is directed caller -> callee. A fault at a component
propagates two ways: the fault itself is "downstream" (callee), while its
latency/error symptoms propagate "upstream" (callee -> caller). We therefore
walk BOTH directions to build the subgraph and record the direction per edge.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import networkx as nx

from backend.rank.constants import BLAST_K, criticality


@dataclass
class BlastEdge:
    src: str
    dst: str
    relation: str
    # dependency call flows src->dst; latency/error symptoms flow dst->src
    latency_direction: str = "callee->caller"


@dataclass
class BlastSubgraph:
    nodes: set[str]
    edges: list[BlastEdge]
    impact: dict[str, float]          # component -> downstream-reach * criticality
    anomalous: set[str]
    k: int = BLAST_K

    def to_summary(self) -> dict:
        return {
            "nodes": sorted(self.nodes),
            "edges": [
                {"src": e.src, "dst": e.dst, "relation": e.relation,
                 "latency_direction": e.latency_direction}
                for e in self.edges
            ],
            "impact": {k: round(v, 3) for k, v in sorted(self.impact.items())},
            "anomalous": sorted(self.anomalous),
            "k": self.k,
        }


def reachable_upstream(topology: nx.DiGraph, node: str) -> set[str]:
    """A suspect plus everything that (transitively) calls it — the components
    that would show its symptoms (symptoms propagate callee -> caller)."""
    if node not in topology:
        return {node}
    return {node} | nx.ancestors(topology, node)


def _khop(graph: nx.DiGraph, source: str, k: int, forward: bool) -> set[str]:
    """Nodes within k hops of `source`, following out-edges (forward) or
    in-edges (backward)."""
    seen = {source}
    frontier = deque([(source, 0)])
    neighbors = graph.successors if forward else graph.predecessors
    while frontier:
        node, depth = frontier.popleft()
        if depth >= k:
            continue
        for nb in neighbors(node):
            if nb not in seen:
                seen.add(nb)
                frontier.append((nb, depth + 1))
    return seen


def blast_radius(
    topology: nx.DiGraph,
    anomalous_components: set[str],
    k: int = BLAST_K,
) -> BlastSubgraph:
    """Build the k-hop blast subgraph around the anomalous components."""
    nodes: set[str] = set()
    present = [c for c in anomalous_components if c in topology]
    for comp in present:
        nodes |= _khop(topology, comp, k, forward=True)   # callees
        nodes |= _khop(topology, comp, k, forward=False)  # callers
    nodes |= set(present)

    sub = topology.subgraph(nodes)
    edges = [
        BlastEdge(u, v, sub[u][v].get("relation", "calls"))
        for u, v in sub.edges()
    ]

    impact: dict[str, float] = {}
    for comp in nodes:
        downstream = nx.descendants(topology, comp)  # reachable callees
        impact[comp] = len(downstream) * criticality(comp)

    return BlastSubgraph(
        nodes=set(nodes),
        edges=edges,
        impact=impact,
        anomalous=set(present) & set(nodes),
        k=k,
    )
