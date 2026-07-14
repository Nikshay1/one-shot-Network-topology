"""The API + SSE contract, and the ground-truth leak gate.

One real run drives the whole module (a `session`-scoped fixture): POST the run,
consume the SSE stream to `pipeline_done`, then assert against the captured
frames and every REST response. Re-running the pipeline per test would triple the
suite's wall-clock for no extra coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import Paths, create_app
from backend.detect.runner import detect
from backend.ingest.store import EventStore
from backend.models import AnomalyEvent, LedgerRecord, RankedHypothesis
from backend.overlay.scenarios import gen_clean_cascade
from backend.replayer.replay import Replayer

# Rule 4: these three names may exist only in /eval and /scenarios label files.
GROUND_TRUTH_FIELDS = ("fault_service", "inject_time", "ground_truth_innocent")
CASE = "api-cc"


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api")
    bundle, label = gen_clean_cascade(CASE, 42, fault_service="catalogue", fault_type="cpu")
    store = EventStore(tmp / "p")
    store.write_case(bundle)
    store.write_topology(CASE, bundle.topology)
    paths = Paths(store=tmp / "p", anomalies=tmp / "a", ledger=tmp / "l",
                  transcripts=tmp / "t", reports=tmp / "r", eval=tmp / "e")
    app = create_app(paths)
    with TestClient(app) as client:
        yield {"client": client, "paths": paths, "label": label, "bundle": bundle}


@pytest.fixture(scope="session")
def run(env):
    """POST a run, drain the SSE stream, return the captured frames."""
    client = env["client"]
    r = client.post(f"/case/{CASE}/run", json={"speed": 0, "seed": 42, "twin_enabled": True})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["stream"] == f"/stream/{body['run_id']}"

    frames: list[tuple[str, dict]] = []
    raw: list[str] = []
    with client.stream("GET", body["stream"]) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        name = None
        for line in resp.iter_lines():
            raw.append(line)
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                frames.append((name, json.loads(line[6:])))
                if name in ("pipeline_done", "pipeline_error"):
                    break
    return {"run_id": body["run_id"], "frames": frames, "raw": raw}


def names(run) -> list[str]:
    return [n for n, _ in run["frames"]]


def payloads(run, name: str) -> list[dict]:
    return [d for n, d in run["frames"] if n == name]


# =========================================================================
# REST surface
# =========================================================================
def test_health(env) -> None:
    r = env["client"].get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.json()["version"]


def test_cases_lists_the_case_with_counts(env) -> None:
    rows = env["client"].get("/cases").json()
    row = next(c for c in rows if c["case_id"] == CASE)
    assert row["n_events"] > 0 and row["n_components"] > 0 and row["title"]


def test_topology_is_node_link(env) -> None:
    g = env["client"].get(f"/case/{CASE}/topology").json()
    assert {"directed", "multigraph", "graph", "nodes", "links"} <= set(g)
    assert any(n["id"] == "catalogue" for n in g["nodes"])


def test_unknown_case_and_run_are_404(env) -> None:
    assert env["client"].get("/case/nope/topology").status_code == 404
    assert env["client"].post("/case/nope/run", json={"speed": 0}).status_code == 404
    assert env["client"].get("/run/nope/verdict").status_code == 404
    assert env["client"].get("/stream/nope").status_code == 404
    assert "error" in env["client"].get("/run/nope/verdict").json()


def test_malformed_body_is_422(env) -> None:
    r = env["client"].post(f"/case/{CASE}/run", json={"speed": "fast"})
    assert r.status_code == 422 and {"error", "detail"} <= set(r.json())


def test_duplicate_run_is_409(env, monkeypatch) -> None:
    """A second run of a case whose run is still in flight is rejected."""
    from backend.api.app import RunRecord
    from backend.api.sse import RunBus

    runs = env["client"].app.state.runs
    saved = runs.get(CASE)
    runs[CASE] = RunRecord(run_id=CASE, case_id=CASE, speed=0, seed=1, twin_enabled=True,
                           bus=RunBus(run_id=CASE), done=False)
    try:
        r = env["client"].post(f"/case/{CASE}/run", json={"speed": 0, "seed": 42})
        assert r.status_code == 409 and "error" in r.json()
    finally:
        if saved is not None:
            runs[CASE] = saved
        else:
            runs.pop(CASE, None)


# =========================================================================
# SSE ordering guarantees (contract §"Ordering guarantees")
# =========================================================================
def test_pipeline_done_is_the_last_event(run) -> None:
    assert names(run)[-1] == "pipeline_done", names(run)[-5:]
    assert names(run).count("pipeline_done") == 1
    assert "pipeline_error" not in names(run)
    done = payloads(run, "pipeline_done")[0]
    assert done["run_id"] == run["run_id"] and done["n_hypotheses"] > 0


def test_event_ingested_precedes_every_anomaly_that_cites_it(run) -> None:
    seen: set[str] = set()
    checked = 0
    for name, data in run["frames"]:
        if name == "event_ingested":
            seen.add(data["event_id"])
        elif name == "anomaly_detected":
            for ev in data["evidence_event_ids"]:
                assert ev in seen, f"{data['anomaly_id']} cites unstreamed {ev}"
                checked += 1
    assert checked > 0, "no anomaly cited any evidence — the check proved nothing"


def test_ordering_holds_even_when_the_stream_cap_drops_cited_events(tmp_path) -> None:
    """The guarantee must survive the cap, not just small cases.

    A fresh app with cap=5 is the real RE2-SS shape in miniature: the case cites far
    more evidence than the stream carries. Without the catch-up flush every cited
    event past #5 would arrive after (or never before) the anomaly citing it.
    """
    bundle, _ = gen_clean_cascade("cap", 42, fault_service="catalogue", fault_type="cpu")
    store = EventStore(tmp_path / "p")
    store.write_case(bundle)
    store.write_topology("cap", bundle.topology)
    app = create_app(Paths(store=tmp_path / "p", anomalies=tmp_path / "a", ledger=tmp_path / "l",
                           transcripts=tmp_path / "t", reports=tmp_path / "r", eval=tmp_path / "e"),
                     max_stream_events=5)
    with TestClient(app) as client:
        body = client.post("/case/cap/run", json={"speed": 0, "seed": 42}).json()
        seen, checked, name = set(), 0, None
        with client.stream("GET", body["stream"]) as resp:
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    name = line[7:].strip()
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    if name == "event_ingested":
                        seen.add(data["event_id"])
                    elif name == "anomaly_detected":
                        for ev in data["evidence_event_ids"]:
                            assert ev in seen, f"cap dropped {ev}, cited by {data['anomaly_id']}"
                            checked += 1
                    elif name in ("pipeline_done", "pipeline_error"):
                        assert name == "pipeline_done"
                        break
    assert checked > 5, f"only {checked} citations checked — the cap was never exercised"
    assert len(seen) > 5, "the catch-up flush never fired"


def test_hypothesis_ranked_is_a_full_object_upsert(run) -> None:
    ranked = payloads(run, "hypothesis_ranked")
    assert ranked
    for d in ranked:
        RankedHypothesis.model_validate(d)              # full object, not a delta
    by_id: dict[str, list[dict]] = {}
    for d in ranked:
        by_id.setdefault(d["hypothesis_id"], []).append(d)
    # a re-emitted id must carry the whole object again, so the latest simply wins
    for versions in by_id.values():
        assert all(set(v) == set(versions[0]) for v in versions)


def test_tier_changed_only_from_the_ranking_stage(run) -> None:
    """Every tier_changed must be backed by a hypothesis_ranked that already
    carries that tier — i.e. it came out of tiers.py via the ranking stage."""
    tiers_seen: dict[str, str] = {}
    for name, data in run["frames"]:
        if name == "hypothesis_ranked":
            tiers_seen[data["hypothesis_id"]] = data["tier"]
        elif name == "tier_changed":
            assert tiers_seen.get(data["hypothesis_id"]) == data["tier"], data
            assert data["tier_reason"]
    assert payloads(run, "tier_changed"), "no tier was ever announced"


def test_anomaly_and_blast_and_narration_events_are_present(run) -> None:
    for a in payloads(run, "anomaly_detected"):
        AnomalyEvent.model_validate(a)
    for b in payloads(run, "blast_radius"):
        assert b["radius"] == len(b["affected"]) and b["component_id"] not in b["affected"]
    assert payloads(run, "narration_chunk"), "the narrator streamed nothing"


def test_agent_events_carry_the_v11_shape(run) -> None:
    for s in payloads(run, "agent_step"):
        assert {"agent", "tool", "args_summary", "result_summary"} == set(s)
    for d in payloads(run, "agent_done"):
        assert {"agent", "status", "summary"} == set(d)
        assert d["status"] in ("completed", "budget_exhausted", "error")
    assert payloads(run, "agent_done"), "no agent reported a terminal event"


def test_late_subscriber_sees_the_whole_stream_from_the_start(env, run) -> None:
    """The bus replays its log, so a UI that connects late is not missing events."""
    client = env["client"]
    seen = []
    with client.stream("GET", f"/stream/{run['run_id']}") as resp:
        name = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                seen.append(name)
                if name == "pipeline_done":
                    break
    assert seen == names(run)


# =========================================================================
# run results
# =========================================================================
def test_verdict_and_anomalies_and_ledger(env, run) -> None:
    client, rid = env["client"], run["run_id"]
    v = client.get(f"/run/{rid}/verdict").json()
    assert v["done"] is True and v["case_id"] == CASE and v["hypotheses"]
    hyps = [RankedHypothesis.model_validate(h) for h in v["hypotheses"]]
    assert [h.rank for h in hyps] == list(range(1, len(hyps) + 1))

    for a in client.get(f"/run/{rid}/anomalies").json():
        AnomalyEvent.model_validate(a)

    facts = client.get(f"/run/{rid}/ledger").json()
    assert facts
    for f in facts:
        LedgerRecord.model_validate(f)
    only = client.get(f"/run/{rid}/ledger", params={"kind": "hypothesis_scored"}).json()
    assert only and all(f["kind"] == "hypothesis_scored" for f in only)
    comp = client.get(f"/run/{rid}/ledger", params={"component_id": "catalogue"}).json()
    assert all("catalogue" in f["component_ids"] for f in comp)


def test_narration_chunks(env, run) -> None:
    n = env["client"].get(f"/run/{run['run_id']}/narration").json()
    assert n["run_id"] == run["run_id"] and n["chunks"]
    assert all({"ts", "text"} == set(c) for c in n["chunks"])


def test_remediation_endpoint(env, run) -> None:
    r = env["client"].get(f"/run/{run['run_id']}/remediation")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["status"] in ("ok", "uncertain", "skipped", "error")
    if rep["status"] == "ok":
        assert rep["recommended"]["symptoms_cleared_pct"] > 50


def test_agent_transcript_is_ndjson(env, run) -> None:
    r = env["client"].get(f"/run/{run['run_id']}/agent/investigator/transcript")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    assert lines and all("type" in rec for rec in lines)
    assert lines[-1]["type"] == "result"
    assert env["client"].get(f"/run/{run['run_id']}/agent/nope/transcript").status_code == 404


def test_report_pdf(env, run) -> None:
    r = env["client"].get(f"/run/{run['run_id']}/report.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_counterfactual_endpoint(env, run) -> None:
    client, rid = env["client"], run["run_id"]
    r = client.post(f"/run/{rid}/counterfactual", json={"remove_component": "catalogue"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["removed"] == "catalogue" and 0 <= out["anomalies_still_explained_pct"] <= 100
    assert isinstance(out["affected_hypotheses"], list)

    bad = client.post(f"/run/{rid}/counterfactual", json={"remove_component": "not-a-node"})
    assert bad.status_code == 422 and "error" in bad.json()


def test_benchmark_endpoint_is_shaped_even_when_empty(env) -> None:
    b = env["client"].get("/benchmark").json()
    assert {"runs", "metrics"} <= set(b)


# =========================================================================
# THE LEAK GATE (rule 4)
# =========================================================================
def _leaks(blob: str) -> list[str]:
    return [f for f in GROUND_TRUTH_FIELDS if f in blob]


def test_the_label_actually_holds_the_secrets(env) -> None:
    """Guard the guard: if the label stopped carrying ground truth, the greps below
    would pass while proving nothing."""
    label = env["label"]
    assert label.fault_service == "catalogue" and label.inject_time > 0


def test_no_endpoint_leaks_ground_truth(env, run) -> None:
    client, rid = env["client"], run["run_id"]
    endpoints = ["/cases", "/health", "/benchmark", f"/case/{CASE}/topology",
                 f"/run/{rid}/verdict", f"/run/{rid}/anomalies", f"/run/{rid}/ledger",
                 f"/run/{rid}/narration", f"/run/{rid}/remediation",
                 f"/run/{rid}/agent/investigator/transcript"]
    for ep in endpoints:
        body = client.get(ep).text
        assert not _leaks(body), f"{ep} leaked {_leaks(body)}"
    cf = client.post(f"/run/{rid}/counterfactual", json={"remove_component": "catalogue"}).text
    assert not _leaks(cf)


def test_no_sse_payload_leaks_ground_truth(run) -> None:
    for name, data in run["frames"]:
        blob = json.dumps(data, default=str)
        assert not _leaks(blob), f"SSE {name} leaked {_leaks(blob)}"
    assert not _leaks("\n".join(run["raw"]))


def test_the_inject_timestamp_value_never_appears(env, run) -> None:
    """The field NAMES are one leak; the VALUE is another. A verdict that echoed the
    exact inject_time would be ground truth by another name."""
    inject = env["label"].inject_time
    for name, data in run["frames"]:
        if name in ("event_ingested", "anomaly_detected"):
            continue                                     # real telemetry legitimately spans it
        blob = json.dumps(data, default=str)
        assert f'"{inject}"' not in blob and f": {inject}" not in blob, f"{name} echoed inject_time"


# =========================================================================
# replayer
# =========================================================================
def test_replay_is_ordered_and_deterministic(env) -> None:
    store = EventStore(env["paths"].store)
    import asyncio

    def ids(speed):
        got: list[str] = []
        r = Replayer(store, CASE, speed=speed, seed=42, max_stream_events=50)
        stats = asyncio.run(r.stream(lambda n, d: got.append(d["event_id"])))
        return got, stats

    a, sa = ids(0)
    b, _ = ids(0)
    assert a == b, "replay is not deterministic"
    assert len(a) == 50 and sa.streamed == 50 and sa.dropped == sa.total - 50
    assert sa.truncated and "were NOT streamed" in sa.note()   # the cap is reported, not silent

    rows = Replayer(store, CASE, max_stream_events=50).rows()
    keys = [(r["ts"], r["event_id"]) for r in rows[:50]]
    assert keys == sorted(keys), "replay is not ordered by (ts, event_id)"


def test_speed_zero_does_not_sleep(env) -> None:
    import asyncio
    import time

    store = EventStore(env["paths"].store)
    t0 = time.monotonic()
    stats = asyncio.run(Replayer(store, CASE, speed=0).stream(lambda n, d: None))
    # speed=0 is the eval path: a case spanning ~10 min of telemetry must replay instantly
    assert time.monotonic() - t0 < 5.0
    assert stats.speed == 0 and stats.streamed > 0
