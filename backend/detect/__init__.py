"""Detection layer: deterministic detectors -> schema-valid AnomalyEvents.

Detectors are RUNTIME PIPELINE code: they read only events (never ground truth
such as inject_time / fault_service). Baselines come from the case window
itself (first 30%), not from any label.
"""

from __future__ import annotations

from collections import defaultdict

from backend.ingest.normalize import normalize_component
from backend.models import AnomalyEvent, Method, Source, Window


def normalize_score(raw: float, cap: float = 10.0) -> float:
    """Map a raw magnitude (per the STEP 4 spec, capped at `cap`) into the
    AnomalyEvent contract's [0, 1] score by dividing by `cap`."""
    if raw < 0:
        raw = 0.0
    if raw > cap:
        raw = cap
    return raw / cap


class AnomalyBuilder:
    """Allocates unique per-component anomaly ids across all detectors so ids
    never collide (anomaly_id middle segment is the component)."""

    def __init__(self, case_id: str, min_score: float = 0.0) -> None:
        self.case_id = case_id
        self.min_score = min_score
        self._counters: dict[str, int] = defaultdict(int)
        self.anomalies: list[AnomalyEvent] = []

    def make(
        self,
        component: str,
        source: Source,
        method: Method,
        start: float,
        end: float,
        score: float,
        evidence_event_ids: list[str],
        summary: str,
    ) -> AnomalyEvent | None:
        clamped = max(0.0, min(1.0, float(score)))
        if clamped < self.min_score:
            return None  # drop weak anomalies below the reporting floor
        comp = normalize_component(component)
        seq = self._counters[comp]
        self._counters[comp] = seq + 1
        anomaly = AnomalyEvent(
            anomaly_id=f"anom-{comp.replace('-', '_')}-{seq:04d}",
            case_id=self.case_id,
            source=source,
            component_id=comp,
            window=Window(start=float(start), end=float(end)),
            score=clamped,
            method=method,
            evidence_event_ids=list(evidence_event_ids),
            summary=summary[:200],
        )
        self.anomalies.append(anomaly)
        return anomaly


__all__ = ["AnomalyBuilder", "normalize_score"]
