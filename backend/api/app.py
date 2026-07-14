"""The VERDICT API — every endpoint in contracts/api_contract.md v1.1.

Shape of a run
--------------
`POST /case/{id}/run` returns 202 immediately; a background task then:

    replay (async, speed-compressed)  ->  detect (batch)  ->  pipeline  ->  pipeline_done

The replay streams `event_ingested` live; detection and the pipeline are
synchronous and run in a worker thread (`asyncio.to_thread`), publishing onto the
run's bus as they go. See `backend/replayer/replay.py` for why detection is batch.

run_id == case_id, deliberately
-------------------------------
The agent transcript cache is keyed on `run_id`, so an OFFLINE demo can only
replay a warmed transcript if the API run uses the same run_id the warm-up did —
which is the case_id. That also gives `409 on duplicate run` its natural meaning:
a run for this case is already in flight.

GROUND TRUTH NEVER LEAVES /eval
-------------------------------
No handler here opens data/labels. `fault_service`, `inject_time` and
`ground_truth_innocent` live only in label files read by /eval and /scenarios
(rule 4); tests/test_api.py greps every response and SSE frame to prove it.

    py -m backend.main            # or: make run
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from backend.api.pdf_report import InvestigationSummary, build_pdf
from backend.api.sse import HEARTBEAT_S, BusRegistry, RunBus
from backend.ingest.store import EventStore
from backend.ledger.ledger import Ledger
from backend.localize.blast import blast_radius
from backend.models import AnomalyEvent
from backend.rank.counterfactual import remove_and_explain
from backend.rank.scorer import load_anomalies
from backend.replayer.replay import DEFAULT_MAX_STREAM_EVENTS, Replayer

try:                                                    # networkx>=3
    from networkx import node_link_data
except ImportError:                                     # pragma: no cover
    node_link_data = None

log = logging.getLogger("verdict.api")
VERSION = "1.1"


# =========================================================================
# request/response models
# =========================================================================
class RunRequest(BaseModel):
    speed: float = Field(default=1.0, ge=0.0)
    seed: int = 42
    twin_enabled: bool = True


class CounterfactualRequest(BaseModel):
    remove_component: str


@dataclass
class RunRecord:
    run_id: str
    case_id: str
    speed: float
    seed: int
    twin_enabled: bool
    bus: RunBus
    started: float = field(default_factory=time.time)
    done: bool = False
    error: str | None = None
    verdict: Any = None                                 # RunVerdict
    replay: Any = None                                  # ReplayStats
    task: Any = None


@dataclass
class Paths:
    store: Path = Path("data/parquet")
    anomalies: Path = Path("data/anomalies")
    ledger: Path = Path("data/ledger")
    transcripts: Path = Path("data/transcripts")
    reports: Path = Path("data/reports")
    eval: Path = Path("eval")


# =========================================================================
# app
# =========================================================================
def create_app(paths: Paths | None = None,
               max_stream_events: int | None = DEFAULT_MAX_STREAM_EVENTS) -> FastAPI:
    app = FastAPI(title="VERDICT", version=VERSION)
    P = paths or Paths()
    buses = BusRegistry()
    runs: dict[str, RunRecord] = {}
    app.state.paths, app.state.runs, app.state.buses = P, runs, buses

    def store() -> EventStore:
        return EventStore(P.store)

    def _404(what: str, ident: str):
        return JSONResponse({"error": f"unknown {what} {ident!r}"}, status_code=404)

    def _run_or_404(run_id: str):
        rec = runs.get(run_id)
        return rec if rec is not None else _404("run_id", run_id)

    # -- the run driver ---------------------------------------------------
    def _compute(rec: RunRecord) -> Any:
        """Detection + the whole pipeline. Synchronous — runs in a worker thread."""
        from backend.detect.runner import detect
        from backend.pipeline import run as pipeline_run

        emit = rec.bus.emitter()
        st = store()
        anomalies = detect(rec.case_id, store_root=P.store, out_dir=P.anomalies,
                           drain_dir=Path("data/drain3"))

        # Ordering guarantee #1 says event_ingested precedes any anomaly_detected that
        # CITES it — and the stream cap can drop a cited event (the real RE2-SS case
        # cites ~8.4k evidence ids out of ~186k events). Flush the cited stragglers
        # here, still ahead of every anomaly, so the guarantee holds under the cap.
        cited = {e for a in anomalies for e in a.evidence_event_ids}
        missing = sorted(cited - (rec.replay.streamed_ids if rec.replay else set()))
        if missing:
            rows = st.get_by_ids(missing).sort(["ts", "event_id"]).to_dicts()
            log.info("run %s: flushing %d cited events the stream cap had dropped",
                     rec.run_id, len(rows))
            for row in rows:
                emit("event_ingested", Replayer.envelope(row))

        for a in anomalies:
            emit("anomaly_detected", a.model_dump(mode="json"))

        topology = st.load_topology(rec.case_id)
        if topology is not None:
            blast = blast_radius(topology, {a.component_id for a in anomalies})
            for comp in sorted({a.component_id for a in anomalies}):
                if comp not in topology:
                    continue
                affected = sorted(n for n in blast.nodes if n != comp)
                emit("blast_radius", {"component_id": comp, "radius": len(affected),
                                      "affected": affected})

        return pipeline_run(rec.case_id, store_root=P.store, anomalies_dir=P.anomalies,
                            ledger_dir=P.ledger, transcripts_dir=P.transcripts,
                            run_id=rec.run_id, twin_enabled=rec.twin_enabled, emit=emit)

    async def _drive(rec: RunRecord) -> None:
        stage = "replay"
        try:
            rep = Replayer(store(), rec.case_id, speed=rec.speed, seed=rec.seed,
                           max_stream_events=max_stream_events)
            rec.replay = await rep.stream(rec.bus.emitter())
            if rec.replay.truncated:                    # a cap is never silent
                log.warning("run %s: %s", rec.run_id, rec.replay.note())
            stage = "pipeline"
            rec.verdict = await asyncio.to_thread(_compute, rec)
            rec.bus.publish("pipeline_done", {"run_id": rec.run_id,
                                              "n_hypotheses": len(rec.verdict.hypotheses)})
        except Exception as exc:                        # rule 11's last line of defence
            rec.error = f"{type(exc).__name__}: {exc}"
            log.exception("run %s failed in %s", rec.run_id, stage)
            rec.bus.publish("pipeline_error", {"run_id": rec.run_id, "stage": stage,
                                               "error": rec.error})
        finally:
            rec.done = True

    # =====================================================================
    # cases
    # =====================================================================
    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": VERSION}

    @app.get("/cases")
    async def cases() -> list[dict]:
        return [{"case_id": s["case_id"], "title": s["case_id"].replace("_", " "),
                 "n_components": s["n_components"], "n_events": s["n_events"]}
                for s in store().case_summaries()]

    @app.get("/case/{case_id}/topology")
    async def topology(case_id: str):
        g = store().load_topology(case_id)
        if g is None:
            return _404("case_id", case_id)
        try:
            return json.loads(json.dumps(node_link_data(g, edges="links"), default=str))
        except TypeError:                               # pragma: no cover - older networkx
            return json.loads(json.dumps(node_link_data(g), default=str))

    @app.post("/case/{case_id}/run", status_code=202)
    async def start_run(case_id: str, request: Request):
        try:
            body = RunRequest.model_validate(await request.json())
        except (ValidationError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": "malformed body", "detail": str(exc)}, status_code=422)
        if store().load_topology(case_id) is None:
            return _404("case_id", case_id)

        run_id = case_id                                # see module docstring
        existing = runs.get(run_id)
        if existing is not None and not existing.done:
            return JSONResponse({"error": f"run {run_id!r} for case {case_id!r} is already "
                                          f"in flight", "run_id": run_id}, status_code=409)

        bus = buses.create(run_id, loop=asyncio.get_running_loop())
        rec = RunRecord(run_id=run_id, case_id=case_id, speed=body.speed, seed=body.seed,
                        twin_enabled=body.twin_enabled, bus=bus)
        runs[run_id] = rec
        rec.task = asyncio.create_task(_drive(rec))
        return {"run_id": run_id, "stream": f"/stream/{run_id}"}

    # =====================================================================
    # the stream
    # =====================================================================
    @app.get("/stream/{run_id}")
    async def stream(run_id: str, request: Request):
        bus = buses.get(run_id)
        if bus is None:
            return _404("run_id", run_id)

        async def gen():
            async for ev in bus.subscribe():
                if await request.is_disconnected():
                    return
                if ev is None:                          # heartbeat: a comment frame
                    yield f": heartbeat {HEARTBEAT_S:.0f}s\n\n"
                    continue
                yield f"event: {ev.name}\ndata: {json.dumps(ev.data, default=str)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    # =====================================================================
    # run results
    # =====================================================================
    @app.get("/run/{run_id}/verdict")
    async def verdict(run_id: str):
        rec = _run_or_404(run_id)
        if isinstance(rec, JSONResponse):
            return rec
        hyps = rec.verdict.hypotheses if rec.verdict else []
        return {"run_id": run_id, "case_id": rec.case_id,
                "hypotheses": [h.model_dump(mode="json") for h in hyps], "done": rec.done}

    @app.get("/run/{run_id}/anomalies")
    async def anomalies(run_id: str):
        rec = _run_or_404(run_id)
        if isinstance(rec, JSONResponse):
            return rec
        return [a.model_dump(mode="json")
                for a in load_anomalies(rec.case_id, P.anomalies)]

    @app.get("/run/{run_id}/ledger")
    async def ledger(run_id: str, component_id: str | None = None, kind: str | None = None,
                     hypothesis_id: str | None = None):
        rec = _run_or_404(run_id)
        if isinstance(rec, JSONResponse):
            return rec
        led = Ledger(run_id, rec.case_id, P.ledger)
        return [f.model_dump(mode="json") for f in led.query(
            component_id=component_id, kind=kind, hypothesis_id=hypothesis_id, limit=100_000)]

    @app.get("/run/{run_id}/narration")
    async def narration(run_id: str):
        rec = _run_or_404(run_id)
        if isinstance(rec, JSONResponse):
            return rec
        chunks = [ev.data for ev in rec.bus.log if ev.name == "narration_chunk"]
        if not chunks and rec.verdict is not None and rec.verdict.narration is not None:
            chunks = [{"ts": 0.0, "text": rec.verdict.narration.text}]
        return {"run_id": run_id, "chunks": chunks}

    @app.get("/run/{run_id}/remediation")
    async def remediation(run_id: str):
        rec = _run_or_404(run_id)
        if isinstance(rec, JSONResponse):
            return rec
        if rec.verdict is None or rec.verdict.remediation is None:
            return JSONResponse({"error": f"run {run_id!r} has no remediation report yet",
                                 "done": rec.done}, status_code=404)
        return rec.verdict.remediation.to_dict()

    @app.get("/run/{run_id}/agent/{agent_name}/transcript")
    async def transcript(run_id: str, agent_name: str):
        rec = _run_or_404(run_id)
        if isinstance(rec, JSONResponse):
            return rec
        path = (rec.verdict.transcripts or {}).get(agent_name) if rec.verdict else None
        if not path or not Path(path).exists():
            return JSONResponse(
                {"error": f"no transcript for agent {agent_name!r} in run {run_id!r}"},
                status_code=404)
        return PlainTextResponse(Path(path).read_text(encoding="utf-8"),
                                 media_type="application/x-ndjson")

    @app.get("/run/{run_id}/report.pdf")
    async def report(run_id: str):
        rec = _run_or_404(run_id)
        if isinstance(rec, JSONResponse):
            return rec
        if rec.verdict is None:
            return JSONResponse({"error": f"run {run_id!r} has not produced a verdict yet"},
                                status_code=404)
        v = rec.verdict
        out = build_pdf(P.reports / f"{run_id}.pdf", case_id=rec.case_id,
                        narration_text=v.narration.text if v.narration else "",
                        remediation=v.remediation,
                        summary=InvestigationSummary(mode=v.mode, tool_calls=v.tool_calls))
        return Response(out.read_bytes(), media_type="application/pdf", headers={
            "Content-Disposition": f'inline; filename="{run_id}.pdf"'})

    @app.post("/run/{run_id}/counterfactual")
    async def counterfactual(run_id: str, request: Request):
        rec = _run_or_404(run_id)
        if isinstance(rec, JSONResponse):
            return rec
        try:
            body = CounterfactualRequest.model_validate(await request.json())
        except (ValidationError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": "malformed body", "detail": str(exc)}, status_code=422)

        topo = store().load_topology(rec.case_id)
        if topo is None or body.remove_component not in topo:
            return JSONResponse({"error": "remove_component must be a component_id present in "
                                          f"the case topology", "detail": body.remove_component},
                                status_code=422)
        anoms: list[AnomalyEvent] = load_anomalies(rec.case_id, P.anomalies)
        from backend.localize.blast import reachable_upstream
        hyps = rec.verdict.hypotheses if rec.verdict else []
        reach_by = {h.suspect_component: reachable_upstream(topo, h.suspect_component)
                    for h in hyps}
        reach_by.setdefault(body.remove_component,
                            reachable_upstream(topo, body.remove_component))
        pct = remove_and_explain(blast_radius(topo, {a.component_id for a in anoms}),
                                 anoms, reach_by, body.remove_component)
        return {"removed": body.remove_component, "anomalies_still_explained_pct": pct,
                "affected_hypotheses": [h.hypothesis_id for h in hyps
                                        if h.suspect_component == body.remove_component]}

    # =====================================================================
    # benchmark
    # =====================================================================
    @app.get("/benchmark")
    async def benchmark():
        p = P.eval / "results.json"
        if not p.exists():
            return {"runs": [], "metrics": {},
                    "note": "no benchmark yet — run `python -m eval.run_benchmark --heldout`"}
        return json.loads(p.read_text(encoding="utf-8"))

    return app


app = create_app()
