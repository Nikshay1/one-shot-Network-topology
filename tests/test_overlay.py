"""Tests for the config/alert overlay and the 7 scenario generators."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from backend.ingest.normalize import EVENT_ID_RE
from backend.ingest.re2ss_adapter import build_topology
from backend.ingest.store import EventStore
from backend.overlay import config_overlay as co
from backend.overlay import scenarios as sc


def _topo():
    return build_topology(set(sc.CANON_COMPONENTS))


# --------------------------- config overlay ---------------------------
def test_inject_config_placement_and_split() -> None:
    topo = _topo()
    f = co.EventFactory("case-x")
    injected = co.inject_config_events(f, topo, "catalogue", "cpu", 1000.0, random.Random(1))
    assert 3 <= len(injected) <= 6
    _, offpath = co.causal_and_offpath(topo, "catalogue")
    for c in injected:
        assert 1000.0 - 120.0 <= c.ts <= 1000.0 - 30.0, "config not 30-120s before inject"
        if c.innocent:
            assert c.component_id in offpath, "innocent herring must be off the causal path"
        else:
            assert c.component_id == "catalogue", "plausible trigger must be on the faulty component"
    assert any(c.innocent for c in injected)          # 70% innocent
    assert any(not c.innocent for c in injected)      # cpu is a plausible-trigger fault


def test_non_plausible_fault_yields_all_innocent() -> None:
    # disk faults do not plausibly follow a config change -> no plausible triggers
    f = co.EventFactory("case-x")
    injected = co.inject_config_events(f, _topo(), "orders", "disk", 1000.0, random.Random(2))
    assert injected and all(c.innocent for c in injected)


def test_config_overlay_is_deterministic() -> None:
    def run() -> list[str]:
        f = co.EventFactory("case-x")
        co.inject_config_events(f, _topo(), "catalogue", "cpu", 1000.0, random.Random(42))
        co.synth_alert_events(f.events, f)
        return [e.event_id for e in f.events]

    assert run() == run()


def test_apply_updates_sidecar_alerts_and_no_payload_leak(tmp_path: Path) -> None:
    bundle, _ = sc.gen_clean_cascade("case-real", 5, fault_service="catalogue", fault_type="cpu")
    co.write_label(tmp_path, "case-real", {
        "case_id": "case-real", "fault_service": "catalogue",
        "fault_type": "cpu", "inject_time": bundle.inject_time,
    })
    n0 = len(bundle.events)
    co.apply(bundle, seed=7, labels_dir=tmp_path)

    cfg = [e for e in bundle.events if e.source == "config"]
    alr = [e for e in bundle.events if e.source == "alert"]
    assert len(bundle.events) > n0
    assert 3 <= len(cfg) <= 6
    assert len(alr) >= 1                              # cpu spike -> synthesized alerts

    label = json.loads((tmp_path / "case-real.json").read_text())
    assert label["ground_truth_innocent"]            # innocence recorded in sidecar
    for e in cfg:                                     # ...and NEVER in the payload
        assert "innocent" not in e.payload.model_dump()
    synthetic = set(label["synthetic_event_ids"])
    assert {e.event_id for e in cfg + alr}.issubset(synthetic)


def test_alerts_thresholded_from_metrics(tmp_path: Path) -> None:
    # a bundle with a hot cpu metric must yield a HighCPU alert; a cold one must not
    f = co.EventFactory("case-a")
    f.metric("catalogue", "cpu", 95.0, 100.0)        # > threshold
    f.metric("payment", "cpu", 5.0, 100.0)           # < threshold
    alerts = co.synth_alert_events(f.events, f)
    hot = {(a.component_id, a.payload.name) for a in alerts}
    assert ("catalogue", "HighCPU") in hot
    assert not any(a.component_id == "payment" for a in alerts)


# --------------------------- scenarios ---------------------------
def test_every_variant_builds_and_is_valid() -> None:
    reg = sc.load_registry()
    seen: set[str] = set()
    for v in reg["variants"]:
        bundle, label = sc.build_variant(v, 42)
        assert bundle.events, v["variant_id"]
        assert label.case_id == v["variant_id"]
        assert label.synthetic is True
        seen.add(label.scenario_type)
        nodes = set(bundle.topology.nodes)
        ids = [e.event_id for e in bundle.events]
        assert len(ids) == len(set(ids)), f"dup ids in {v['variant_id']}"
        for e in bundle.events:
            assert EVENT_ID_RE.match(e.event_id)
            assert e.component_id in nodes
    assert seen == set(reg["scenario_types"])
    assert len(reg["variants"]) >= 25


def test_red_herring_has_innocent_config_within_120s() -> None:
    reg = sc.load_registry()
    reds = [v for v in reg["variants"] if v["scenario_type"] == "red_herring_config"]
    assert reds
    for v in reds:
        bundle, label = sc.build_variant(v, 42)
        inj = label.inject_time
        cfg_ts = {e.event_id: e.ts for e in bundle.events if e.source == "config"}
        within = [eid for eid in label.ground_truth_innocent
                  if eid in cfg_ts and inj - 120 <= cfg_ts[eid] <= inj]
        assert within, f"{v['variant_id']}: no innocent config within 120s"
        assert label.expected_top1_innocent_config is True


def test_alert_storm_has_150_plus() -> None:
    reg = sc.load_registry()
    for v in reg["variants"]:
        if v["scenario_type"] != "alert_storm":
            continue
        bundle, _ = sc.build_variant(v, 42)
        n = sum(1 for e in bundle.events if e.source == "alert")
        assert n >= 150, (v["variant_id"], n)


def test_missing_telemetry_marks_uninstrumented_with_no_events() -> None:
    bundle, label = sc.gen_missing_telemetry("mt", 1, fault_service="carts-db", fault_type="disk")
    assert bundle.topology.nodes["carts-db"]["instrumented"] is False
    assert all(e.component_id != "carts-db" for e in bundle.events)
    assert label.expected_tier == "MISSING_EVIDENCE"
    assert label.expected_root_cause == "carts-db"


def test_topology_drift_emits_topology_events() -> None:
    bundle, _ = sc.gen_topology_drift("td", 1, fault_service="orders", fault_type="cpu")
    topo_events = [e for e in bundle.events if e.source == "topology"]
    assert topo_events
    assert all(e.payload.kind == "topology" for e in topo_events)


def test_ambiguous_has_no_root_cause() -> None:
    bundle, label = sc.gen_ambiguous("amb", 1, tag="a")
    assert label.expected_root_cause is None
    assert label.expected_tier == "MISSING_EVIDENCE"
    assert bundle.events


def test_build_all_writes_labels_and_is_deterministic(tmp_path: Path) -> None:
    r1 = sc.build_all(42, tmp_path / "p1", tmp_path / "l1")
    assert len(r1) >= 25
    assert (tmp_path / "l1" / "clean_cascade-01.json").exists()
    r2 = sc.build_all(42, tmp_path / "p2", tmp_path / "l2")
    for (b1, _), (b2, _) in zip(r1, r2):
        assert [e.event_id for e in b1.events] == [e.event_id for e in b2.events], b1.case_id


def test_scenario_events_store_roundtrip(tmp_path: Path) -> None:
    bundle, _ = sc.gen_red_herring_config("rr", 3, fault_service="catalogue", fault_type="cpu")
    store = EventStore(tmp_path / "p")
    store.write_case(bundle)
    sample = [e.event_id for e in bundle.events[:80]]
    assert store.resolve(sample) is True
    assert store.resolve(["config-nope-000001"]) is False
