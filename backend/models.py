"""Pydantic v2 models mirroring the four frozen JSON schemas in /contracts.

These are the runtime shapes for every piece of data the pipeline moves. They
are kept in exact lock-step with:
    contracts/event_envelope.schema.json
    contracts/anomaly_event.schema.json
    contracts/ranked_hypothesis.schema.json
    contracts/ledger_record.schema.json

``extra="forbid"`` mirrors ``additionalProperties: false`` from the schemas.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

# --- Shared constrained id types (patterns mirror /contracts exactly) ---
ComponentId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
EventId = Annotated[
    str,
    StringConstraints(pattern=r"^(metric|log|alert|topology|config)-[A-Za-z0-9_.]+-[0-9]{6}$"),
]
AnomalyId = Annotated[str, StringConstraints(pattern=r"^anom-[A-Za-z0-9_.]+-[0-9]{4}$")]
HypothesisId = Annotated[str, StringConstraints(pattern=r"^hyp-[A-Za-z0-9_.]+-[0-9]{2}$")]
FactId = Annotated[str, StringConstraints(pattern=r"^fact-[A-Za-z0-9_.]+-[0-9]{4}$")]

Source = Literal["metric", "log", "alert", "topology", "config"]

# Sum-tolerance for score_breakdown vs score (float-safe).
_SCORE_TOL = 1e-6


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# =========================================================================
# Event envelope + payloads
# =========================================================================
class MetricPayload(_Strict):
    kind: Literal["metric"]
    name: Annotated[str, StringConstraints(min_length=1)]
    value: float
    unit: str | None = None


class LogPayload(_Strict):
    kind: Literal["log"]
    # Optional/nullable: RE2-SS logs.csv `level` column is empty for the vast
    # majority of container logs (Go/Node services emit no structured level).
    level: Literal["DEBUG", "INFO", "WARN", "ERROR", "FATAL", "TRACE"] | None = None
    message: str
    template: str | None = None
    template_id: str | None = None
    req_path: str | None = None
    error: str | None = None


class AlertPayload(_Strict):
    kind: Literal["alert"]
    name: Annotated[str, StringConstraints(min_length=1)]
    severity: Annotated[float, Field(ge=0, le=1)]
    state: Literal["firing", "resolved"]


class TopologyPayload(_Strict):
    kind: Literal["topology"]
    target_component_id: ComponentId
    relation: Literal["calls", "depends_on", "routes_to", "reads_from", "writes_to"]


class ConfigPayload(_Strict):
    kind: Literal["config"]
    key: Annotated[str, StringConstraints(min_length=1)]
    old_value: str | float | bool | None = None
    new_value: str | float | bool | None = None
    risky: bool | None = None


Payload = Annotated[
    Union[MetricPayload, LogPayload, AlertPayload, TopologyPayload, ConfigPayload],
    Field(discriminator="kind"),
]


class EventEnvelope(_Strict):
    event_id: EventId
    case_id: Annotated[str, StringConstraints(min_length=1)]
    source: Source
    component_id: ComponentId
    ts: float
    payload: Payload

    @model_validator(mode="after")
    def _source_matches_payload(self) -> "EventEnvelope":
        if self.source != self.payload.kind:
            raise ValueError(
                f"source {self.source!r} does not match payload.kind {self.payload.kind!r}"
            )
        if not self.event_id.startswith(f"{self.source}-"):
            raise ValueError(
                f"event_id {self.event_id!r} prefix does not match source {self.source!r}"
            )
        return self


# =========================================================================
# Anomaly event
# =========================================================================
Method = Literal[
    "mad_zscore",
    "isolation_forest",
    "log_freq_spike",
    "log_rare_template",
    "alert_dedup",
    "config_risky_flag",
]


class Window(_Strict):
    start: float
    end: float


class AnomalyEvent(_Strict):
    anomaly_id: AnomalyId
    case_id: Annotated[str, StringConstraints(min_length=1)]
    source: Source
    component_id: ComponentId
    window: Window
    score: Annotated[float, Field(ge=0, le=1)]
    method: Method
    evidence_event_ids: list[EventId] = Field(default_factory=list)
    summary: Annotated[str, StringConstraints(max_length=200)]


# =========================================================================
# Ranked hypothesis
# =========================================================================
Tier = Literal["CONFIRMED", "CORRELATED", "MISSING_EVIDENCE"]
FaultType = Literal["cpu", "mem", "disk", "delay", "loss", "socket", "config_push", "unknown"]


class ScoreBreakdown(_Strict):
    coverage: Annotated[float, Field(ge=0, le=1)]
    topo_consistency: Annotated[float, Field(ge=0, le=1)]
    precedence: Annotated[float, Field(ge=0, le=1)]
    corroboration: Annotated[float, Field(ge=0, le=1)]
    pagerank: Annotated[float, Field(ge=0, le=1)]

    def total(self) -> float:
        return (
            self.coverage
            + self.topo_consistency
            + self.precedence
            + self.corroboration
            + self.pagerank
        )


class PredictedSymptom(_Strict):
    component_id: ComponentId
    expectation: Annotated[str, StringConstraints(min_length=1)]
    observed: bool | None


class Counterfactual(_Strict):
    removed: bool
    anomalies_still_explained_pct: Annotated[float, Field(ge=0, le=100)]


class Twin(_Strict):
    run: Annotated[str, StringConstraints(min_length=1)]
    similarity: Annotated[float, Field(ge=0, le=1)]
    verdict: Literal["match", "partial", "mismatch"]
    missing_evidence: list[str] = Field(default_factory=list)


class ChallengerAttack(_Strict):
    claim: Annotated[str, StringConstraints(min_length=1)]
    contradicting_event_id: EventId
    upheld: bool


class Challenger(_Strict):
    attacks: list[ChallengerAttack] = Field(default_factory=list)


class RankedHypothesis(_Strict):
    hypothesis_id: HypothesisId
    case_id: Annotated[str, StringConstraints(min_length=1)]
    rank: Annotated[int, Field(ge=1)]
    suspect_component: ComponentId
    statement: Annotated[str, StringConstraints(min_length=1)]
    score: Annotated[float, Field(ge=0, le=1)]
    score_breakdown: ScoreBreakdown
    tier: Tier
    tier_reason: Annotated[str, StringConstraints(min_length=1)]
    cited_evidence_ids: list[EventId] = Field(default_factory=list)
    predicted_symptoms: list[PredictedSymptom] = Field(default_factory=list)
    counterfactual: Counterfactual
    twin: Twin | None
    challenger: Challenger | None
    trigger_event_id: EventId | None
    fault_type_guess: FaultType | None

    @model_validator(mode="after")
    def _breakdown_sums_to_score(self) -> "RankedHypothesis":
        total = self.score_breakdown.total()
        if abs(total - self.score) > _SCORE_TOL:
            raise ValueError(
                f"score_breakdown sums to {total!r} but score is {self.score!r} "
                f"(tolerance {_SCORE_TOL})"
            )
        return self


# =========================================================================
# Ledger record
# =========================================================================
LedgerKind = Literal[
    "anomaly_observed",
    "anomaly_absent",
    "topology_path",
    "topology_no_path",
    "temporal_order",
    "config_change_observed",
    "counterfactual_result",
    "twin_result",
    "coverage_gap",
    "hypothesis_scored",
    "investigation_note",   # v1.1
    "remediation_result",   # v1.1
]
Modality = Literal["metric", "log", "alert", "topology", "config", "mixed", "derived"]


class LedgerRecord(_Strict):
    fact_id: FactId
    case_id: Annotated[str, StringConstraints(min_length=1)]
    kind: LedgerKind
    statement: Annotated[str, StringConstraints(max_length=300)]
    component_ids: list[ComponentId] = Field(default_factory=list)
    event_ids: list[EventId] = Field(default_factory=list)
    modality: Modality
    ts_range: Window
    confidence: Annotated[float, Field(ge=0, le=1)]
    hypothesis_id: HypothesisId | None


__all__ = [
    "ComponentId",
    "EventId",
    "AnomalyId",
    "HypothesisId",
    "FactId",
    "Source",
    "MetricPayload",
    "LogPayload",
    "AlertPayload",
    "TopologyPayload",
    "ConfigPayload",
    "Payload",
    "EventEnvelope",
    "Window",
    "AnomalyEvent",
    "ScoreBreakdown",
    "PredictedSymptom",
    "Counterfactual",
    "Twin",
    "ChallengerAttack",
    "Challenger",
    "RankedHypothesis",
    "LedgerRecord",
]
