"""Deterministic candidate generation (the ranking floor).

THE THREE-WAY UNION (rule 18: candidates are generated per incident, inside its
subgraph; the reachability math in scorer.py then runs on the FULL topology and
is never clipped at the k-hop boundary):

  1. broken   — every anomalous component in the incident. This is the obvious
                one and used to be the only one.
  2. config   — every component targeted by a risky config change in the window,
                EVEN IF IT HAS NO ANOMALIES. This is how the red herring becomes
                a scoreable, exonerable hypothesis instead of never being asked
                about: you cannot clear a suspect you never charged.
  3. inferred — every non-anomalous component DOWNSTREAM of an anomalous one
                (nx.descendants). This is the uninstrumented-root-cause case: the
                thing that broke emits nothing, and the only evidence it exists is
                that everything above it is on fire.

Each candidate carries a trigger_ts, which is what the scorer's precedence factor
measures against. It is deliberately different per origin:
  broken   -> its earliest anomaly
  config   -> THE CONFIG EVENT'S TIME, not an anomaly time. A config pushed after
              the symptoms started therefore scores ~0 on precedence, for free.
  inferred -> the earliest anomaly among the components it could explain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import networkx as nx

from backend.localize.blast import BlastSubgraph
from backend.models import AnomalyEvent

Origin = Literal["anomalous", "config", "inferred"]

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
    #: Where this candidate came from — drives the statement and the corroboration floor.
    origin: Origin = "anomalous"
    #: What precedence is measured against. None when nothing dates the candidate.
    trigger_ts: float | None = None

    @property
    def inferred(self) -> bool:
        return self.origin == "inferred"

    @property
    def from_config_target(self) -> bool:
        """Kept for the existing importers: a config target with no anomalies of
        its own — the red-herring shape."""
        return self.origin == "config" and not self.suspect_anomalies


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


def _first_anomaly_ts(anomalies: list[AnomalyEvent]) -> dict[str, float]:
    out: dict[str, float] = {}
    for a in anomalies:
        out[a.component_id] = min(out.get(a.component_id, a.window.start), a.window.start)
    return out


def _statement(suspect: str, origin: Origin, fault: str, n_anoms: int, n_up: int) -> str:
    if origin == "config":
        return (
            f"A risky config change on {suspect} lands in the incident window; "
            f"{n_anoms} anomaly(ies) observed on {suspect} itself, symptoms expected "
            f"across {n_up} upstream dependent(s)."
        )
    if origin == "inferred":
        return (
            f"{suspect} emits no anomalies of its own but sits beneath the observed "
            f"failures; if it is uninstrumented, silence here is not health. "
            f"Symptoms expected across {n_up} upstream dependent(s)."
        )
    return (
        f"{suspect} is a candidate root cause ({fault}); "
        f"{n_anoms} anomaly(ies) observed, symptoms expected across "
        f"{n_up} upstream dependent(s)."
    )


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
    first_ts = _first_anomaly_ts(anomalies)

    # 1. broken — anomalous components inside the incident.
    origins: dict[str, Origin] = {c: "anomalous" for c in blast.nodes if c in by_component}

    # 2. config — targets of a risky config change. The config detector raises a
    #    `config_risky_flag` anomaly on the target, which is what dates it; the
    #    component is charged whether or not anything else about it looks wrong.
    for a in anomalies:
        if a.method == "config_risky_flag":
            origins[a.component_id] = "config"

    # 3. inferred — non-anomalous components DOWNSTREAM of an anomalous one, i.e.
    #    things that could be the cause but said nothing. Scoped to the incident's
    #    subgraph per rule 18 (the reachability math is not).
    for comp in anomalous:
        if comp not in topology:
            continue
        for below in nx.descendants(topology, comp):
            if below in anomalous or below in origins:
                continue
            if below not in blast.nodes:
                continue
            origins[below] = "inferred"

    candidates: list[Candidate] = []
    for suspect in sorted(origins):
        origin = origins[suspect]
        anoms = by_component.get(suspect, [])
        fault = _dominant_fault_type(anoms) if anoms else (
            "config_push" if origin == "config" else "unknown"
        )
        symptoms = _predicted_symptoms(suspect, topology, anomalous)
        cited = sorted({e for a in anoms for e in a.evidence_event_ids})
        n_up = len(nx.ancestors(topology, suspect)) if suspect in topology else 0

        if origin == "inferred":
            # Dated by the earliest symptom it could explain — the only clock it has.
            explains = (nx.ancestors(topology, suspect) | {suspect}) & anomalous
            trigger_ts = min((first_ts[c] for c in explains), default=None)
        else:
            trigger_ts = first_ts.get(suspect)

        candidates.append(Candidate(
            suspect=suspect,
            statement=_statement(suspect, origin, fault, len(anoms), n_up),
            trigger_event_id=_trigger_event_id(suspect, anoms),
            fault_type_guess=fault,
            predicted_symptoms=symptoms,
            cited_evidence_ids=cited,
            suspect_anomalies=anoms,
            origin=origin,
            trigger_ts=trigger_ts,
        ))
    return candidates
