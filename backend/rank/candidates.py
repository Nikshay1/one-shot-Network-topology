"""Deterministic candidate generation (the ranking floor).

Suspects = every blast-subgraph component with >=1 anomaly, plus every target of
a risky-config anomaly. Each suspect gets a drafted hypothesis: statement,
trigger event, fault-type guess, and predicted symptoms derived from reversed
reachability (who depends on the suspect and would therefore show symptoms).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from backend.localize.blast import BlastSubgraph
from backend.models import AnomalyEvent

# Keyword -> fault_type_guess, checked against the anomaly summary (our detectors
# embed the metric/name in the summary).
_FAULT_KEYWORDS: list[tuple[str, str]] = [
    ("cpu", "cpu"),
    ("mem", "mem"),
    ("disk", "disk"),
    ("socket", "socket"),
    ("latency", "delay"),
    ("delay", "delay"),
    ("error", "loss"),
    ("loss", "loss"),
    ("refused", "socket"),
    ("connection", "socket"),
]


def fault_type_from_anomaly(a: AnomalyEvent) -> str:
    if a.method == "config_risky_flag":
        return "config_push"
    s = a.summary.lower()
    for kw, ft in _FAULT_KEYWORDS:
        if kw in s:
            return ft
    return "unknown"


@dataclass
class Candidate:
    suspect: str
    statement: str
    trigger_event_id: str | None
    fault_type_guess: str
    predicted_symptoms: list[dict]
    cited_evidence_ids: list[str]
    suspect_anomalies: list[AnomalyEvent] = field(default_factory=list)
    from_config_target: bool = False


def _dominant_fault_type(anomalies: list[AnomalyEvent]) -> str:
    scores: dict[str, float] = {}
    for a in anomalies:
        ft = fault_type_from_anomaly(a)
        scores[ft] = scores.get(ft, 0.0) + a.score
    if not scores:
        return "unknown"
    # config_push wins ties (a change is the strongest trigger signal)
    return max(scores.items(), key=lambda kv: (kv[1], kv[0] == "config_push"))[0]


def _trigger_event_id(suspect: str, anomalies: list[AnomalyEvent]) -> str | None:
    """Earliest config anomaly's event on the suspect, else the earliest
    anomaly's first evidence event."""
    configs = [a for a in anomalies if a.method == "config_risky_flag"]
    pool = configs or anomalies
    if not pool:
        return None
    earliest = min(pool, key=lambda a: a.window.start)
    return earliest.evidence_event_ids[0] if earliest.evidence_event_ids else None


def _predicted_symptoms(
    suspect: str, topology: nx.DiGraph, anomalous: set[str], max_symptoms: int = 8
) -> list[dict]:
    """Reversed reachability: components that (transitively) call the suspect and
    would therefore show downstream symptoms. observed = True if that component
    has an anomaly, False if instrumented without one, None if uninstrumented."""
    if suspect not in topology:
        return []
    callers = nx.ancestors(topology, suspect)  # who depends on the suspect
    out: list[dict] = []
    for comp in sorted(callers)[:max_symptoms]:
        instrumented = topology.nodes[comp].get("instrumented", True)
        if not instrumented:
            observed: bool | None = None
        else:
            observed = comp in anomalous
        out.append({
            "component_id": comp,
            "expectation": f"elevated latency/errors propagating up from {suspect}",
            "observed": observed,
        })
    return out


def generate_candidates(
    case_id: str,
    anomalies: list[AnomalyEvent],
    topology: nx.DiGraph,
    blast: BlastSubgraph,
) -> list[Candidate]:
    by_component: dict[str, list[AnomalyEvent]] = {}
    for a in anomalies:
        by_component.setdefault(a.component_id, []).append(a)

    anomalous = set(by_component)

    suspects: set[str] = {c for c in blast.nodes if c in by_component}
    config_targets = {
        a.component_id for a in anomalies if a.method == "config_risky_flag"
    }
    suspects |= config_targets

    candidates: list[Candidate] = []
    for suspect in sorted(suspects):
        anoms = by_component.get(suspect, [])
        fault = _dominant_fault_type(anoms) if anoms else "config_push"
        trigger = _trigger_event_id(suspect, anoms)
        symptoms = _predicted_symptoms(suspect, topology, anomalous)
        cited = sorted({e for a in anoms for e in a.evidence_event_ids})
        n_down = len(nx.ancestors(topology, suspect)) if suspect in topology else 0
        statement = (
            f"{suspect} is a candidate root cause ({fault}); "
            f"{len(anoms)} anomaly(ies) observed, symptoms expected across "
            f"{n_down} upstream dependent(s)."
        )
        candidates.append(Candidate(
            suspect=suspect,
            statement=statement,
            trigger_event_id=trigger,
            fault_type_guess=fault,
            predicted_symptoms=symptoms,
            cited_evidence_ids=cited,
            suspect_anomalies=anoms,
            from_config_target=(suspect in config_targets and not anoms),
        ))
    return candidates
