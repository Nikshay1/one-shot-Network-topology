"""Agent tool registry — the typed layer all agents call.

Every tool is a (pydantic input model, pydantic output model, pure function)
triple with a cost in points. `REGISTRY` maps name -> Tool; `call_tool` is the
harness entry point that validates input, charges the budget, and runs the fn.

`file_finding` is the ONLY tool that mutates state (appends to the ledger) and it
VALIDATES first (kind, event resolution, component membership). Read tools never
mutate. `run_counterfactual`/`run_twin` are registered now and return
`{"status": "unavailable"}` until Steps 6/7.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, get_args

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field

from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import BlastSubgraph, reachable_upstream
from backend.rank.counterfactual import remove_and_explain, score_multiplier
from backend.models import (
    AnomalyEvent,
    EventEnvelope,
    LedgerKind,
    LedgerRecord,
    RankedHypothesis,
    Source,
)
from backend.rank.constants import AGENT_FINDING_CONFIDENCE

_LEDGER_KINDS = set(get_args(LedgerKind))


# =========================================================================
# Tool context (handles the tools operate over)
# =========================================================================
@dataclass
class ToolContext:
    case_id: str
    store: EventStore
    topology: nx.DiGraph
    anomalies: list[AnomalyEvent]
    blast: BlastSubgraph
    ledger: Ledger
    _ranked: list[RankedHypothesis] | None = None

    def ranked(self) -> list[RankedHypothesis]:
        if self._ranked is None:
            from backend.rank.scorer import rank
            self._ranked = rank(self.case_id, self.anomalies, self.topology)
        return self._ranked


# =========================================================================
# I/O models
# =========================================================================
class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetAnomaliesIn(_Model):
    component_id: str | None = None
    source: Source | None = None
    limit: int = Field(default=20, ge=1, le=1000)


class GetAnomaliesOut(_Model):
    anomalies: list[AnomalyEvent]


class GetCandidatesIn(_Model):
    pass


class GetCandidatesOut(_Model):
    candidates: list[RankedHypothesis]


class CheckPathIn(_Model):
    src: str
    dst: str


class CheckPathOut(_Model):
    path_exists: bool
    path: list[str]
    direction_note: str


class GetTopologySummaryIn(_Model):
    pass


class GetTopologySummaryOut(_Model):
    nodes: list[dict[str, Any]]
    n_edges: int
    blast: dict[str, Any]


class GetEventsIn(_Model):
    event_ids: list[str] = Field(max_length=10)


class GetEventsOut(_Model):
    events: list[EventEnvelope]
    missing: list[str]


class GetLedgerIn(_Model):
    component_id: str | None = None
    kind: str | None = None
    hypothesis_id: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class GetLedgerOut(_Model):
    records: list[LedgerRecord]


class RunCounterfactualIn(_Model):
    component: str


class RunCounterfactualOut(_Model):
    status: str
    component: str
    still_explained_pct: float
    score_multiplier: float
    interpretation: str
    fact_id: str


class StubOut(_Model):
    model_config = ConfigDict(extra="allow")
    status: str = "unavailable"


class RunTwinIn(_Model):
    component: str
    fault_type: str


class FileFindingIn(_Model):
    kind: str
    statement: str
    component_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)


class FileFindingOut(_Model):
    ok: bool
    fact_id: str | None = None
    error: str | None = None
    detail: str | None = None


# =========================================================================
# Tool functions
# =========================================================================
def _rows_to_envelopes(df) -> list[EventEnvelope]:
    out: list[EventEnvelope] = []
    for r in df.iter_rows(named=True):
        out.append(EventEnvelope(
            event_id=r["event_id"], case_id=r["case_id"], source=r["source"],
            component_id=r["component_id"], ts=r["ts"],
            payload=json.loads(r["payload_json"]),
        ))
    return out


def _get_anomalies(inp: GetAnomaliesIn, ctx: ToolContext) -> GetAnomaliesOut:
    items = ctx.anomalies
    if inp.component_id is not None:
        items = [a for a in items if a.component_id == inp.component_id]
    if inp.source is not None:
        items = [a for a in items if a.source == inp.source]
    return GetAnomaliesOut(anomalies=items[: inp.limit])


def _get_candidates(inp: GetCandidatesIn, ctx: ToolContext) -> GetCandidatesOut:
    return GetCandidatesOut(candidates=ctx.ranked())


def _check_path(inp: CheckPathIn, ctx: ToolContext) -> CheckPathOut:
    g = ctx.topology
    if inp.src not in g or inp.dst not in g:
        return CheckPathOut(path_exists=False, path=[],
                            direction_note="src or dst not present in topology")
    if nx.has_path(g, inp.src, inp.dst):
        p = nx.shortest_path(g, inp.src, inp.dst)
        return CheckPathOut(path_exists=True, path=p,
                            direction_note=f"{inp.src} calls (transitively) {inp.dst}; "
                                           f"latency/errors propagate {inp.dst} -> {inp.src}")
    if nx.has_path(g, inp.dst, inp.src):
        p = nx.shortest_path(g, inp.dst, inp.src)
        return CheckPathOut(path_exists=False, path=[],
                            direction_note=f"no {inp.src}->{inp.dst} path, but reverse exists "
                                           f"({' -> '.join(p)})")
    return CheckPathOut(path_exists=False, path=[],
                        direction_note="no directed path in either direction")


def _get_topology_summary(inp: GetTopologySummaryIn, ctx: ToolContext) -> GetTopologySummaryOut:
    g = ctx.topology
    nodes = [{"id": n, "instrumented": bool(g.nodes[n].get("instrumented", True))}
             for n in sorted(g.nodes)]
    return GetTopologySummaryOut(nodes=nodes, n_edges=g.number_of_edges(),
                                 blast=ctx.blast.to_summary())


def _get_events(inp: GetEventsIn, ctx: ToolContext) -> GetEventsOut:
    df = ctx.store.get_by_ids(inp.event_ids)
    events = _rows_to_envelopes(df)
    found = {e.event_id for e in events}
    missing = [e for e in inp.event_ids if e not in found]
    return GetEventsOut(events=events, missing=missing)


def _get_ledger(inp: GetLedgerIn, ctx: ToolContext) -> GetLedgerOut:
    return GetLedgerOut(records=ctx.ledger.query(
        component_id=inp.component_id, kind=inp.kind,
        hypothesis_id=inp.hypothesis_id, limit=inp.limit,
    ))


def _run_counterfactual(inp: RunCounterfactualIn, ctx: ToolContext) -> RunCounterfactualOut:
    ranked = ctx.ranked()
    reach_by = {h.suspect_component: reachable_upstream(ctx.topology, h.suspect_component)
                for h in ranked}
    reach_by.setdefault(inp.component, reachable_upstream(ctx.topology, inp.component))
    pct = remove_and_explain(ctx.blast, ctx.anomalies, reach_by, inp.component)
    mult = score_multiplier(pct)
    verdict = "redundant (counterfactual-unchanged)" if pct >= 70.0 else "load-bearing"
    interp = (f"Removing {inp.component} leaves {pct:.0f}% of anomalies explained by other "
              f"candidates -> {verdict} (score x{mult}).")
    hyp = next((h.hypothesis_id for h in ranked if h.suspect_component == inp.component), None)
    fact_id = ctx.ledger.counterfactual_result(
        statement=interp, component_ids=[inp.component], ts_range=(0.0, 0.0), hypothesis_id=hyp,
    )
    return RunCounterfactualOut(status="ok", component=inp.component, still_explained_pct=pct,
                                score_multiplier=mult, interpretation=interp, fact_id=fact_id)


def _run_twin(inp: RunTwinIn, ctx: ToolContext) -> StubOut:
    return StubOut(status="unavailable")  # implemented in Step 7


def _file_finding(inp: FileFindingIn, ctx: ToolContext) -> FileFindingOut:
    if inp.kind not in _LEDGER_KINDS:
        return FileFindingOut(ok=False, error="invalid_kind",
                              detail=f"{inp.kind!r} is not a valid LedgerRecord kind")
    bad_components = [c for c in inp.component_ids if c not in ctx.topology]
    if bad_components:
        return FileFindingOut(ok=False, error="unknown_component",
                              detail=f"components not in topology: {bad_components}")
    df = ctx.store.get_by_ids(inp.event_ids) if inp.event_ids else None
    found = set(df["event_id"].to_list()) if df is not None else set()
    unresolved = [e for e in inp.event_ids if e not in found]
    if unresolved:
        return FileFindingOut(ok=False, error="unresolved_event_id",
                              detail=f"event_ids do not resolve: {unresolved}")

    if df is not None and df.height:
        sources = set(df["source"].to_list())
        modality = next(iter(sources)) if len(sources) == 1 else "mixed"
        ts_range = (float(df["ts"].min()), float(df["ts"].max()))
    else:
        modality, ts_range = "derived", (0.0, 0.0)

    fact_id = ctx.ledger.write(
        kind=inp.kind, statement=inp.statement, component_ids=inp.component_ids,
        event_ids=inp.event_ids, modality=modality, ts_range=ts_range,
        confidence=AGENT_FINDING_CONFIDENCE, hypothesis_id=None,
    )
    return FileFindingOut(ok=True, fact_id=fact_id)


# =========================================================================
# Registry
# =========================================================================
@dataclass
class Tool:
    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[[Any, ToolContext], BaseModel]
    cost: int


REGISTRY: dict[str, Tool] = {}


def _register(name: str, inp, out, fn, cost: int) -> None:
    REGISTRY[name] = Tool(name=name, input_model=inp, output_model=out, fn=fn, cost=cost)


_register("get_anomalies", GetAnomaliesIn, GetAnomaliesOut, _get_anomalies, 0)
_register("get_candidates", GetCandidatesIn, GetCandidatesOut, _get_candidates, 0)
_register("check_path", CheckPathIn, CheckPathOut, _check_path, 0)
_register("get_topology_summary", GetTopologySummaryIn, GetTopologySummaryOut, _get_topology_summary, 0)
_register("get_events", GetEventsIn, GetEventsOut, _get_events, 0)
_register("get_ledger", GetLedgerIn, GetLedgerOut, _get_ledger, 0)
_register("run_counterfactual", RunCounterfactualIn, RunCounterfactualOut, _run_counterfactual, 1)
_register("run_twin", RunTwinIn, StubOut, _run_twin, 2)
_register("file_finding", FileFindingIn, FileFindingOut, _file_finding, 0)


def call_tool(name: str, args: dict | None, ctx: ToolContext, budget=None) -> BaseModel:
    """Harness entry point: validate args, charge the budget, run the tool."""
    if name not in REGISTRY:
        raise KeyError(f"unknown tool {name!r}")
    tool = REGISTRY[name]
    inp = tool.input_model.model_validate(args or {})
    if budget is not None:
        budget.charge(tool.cost, label=name)
    return tool.fn(inp, ctx)
