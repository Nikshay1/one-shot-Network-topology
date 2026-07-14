"""Detector unit tests + the golden-case sanity harness.

The sanity harness is the STEP 4 acceptance gate: on the real golden case, >=80%
of anomaly score mass must fall AFTER inject_time (read from the label sidecar
INSIDE THIS TEST ONLY), and no anomaly may reference a component absent from the
topology.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from backend.detect import AnomalyBuilder
from backend.detect.alerts import detect_alerts
from backend.detect.config import detect_config
from backend.detect.logs import detect_logs
from backend.detect.metrics import detect_iforest, detect_mad
from backend.detect.runner import detect
from backend.ingest.store import EventStore

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "parquet"
LABELS = ROOT / "data" / "labels"
GOLDEN = "catalogue_cpu-1"


# --------------------------- metrics ---------------------------
def test_mad_detects_sustained_spike() -> None:
    b = AnomalyBuilder("t")
    rows = []
    for i in range(100):
        v = 10.0 + (0.1 if i % 2 else -0.1)      # baseline wiggle -> MAD > 0
        if i >= 50:
            v = 40.0                              # sustained spike (>= MIN_SUSTAINED_S)
        rows.append((f"metric-catalogue-{i:06d}", "catalogue", float(i), "cpu", v))
    df = pl.DataFrame(rows, schema=["event_id", "component_id", "ts", "metric_name", "value"],
                      orient="row")
    detect_mad(df, b)
    mad = [a for a in b.anomalies if a.method == "mad_zscore"]
    assert mad
    top = max(mad, key=lambda a: a.score)
    assert top.window.start == 50.0
    assert 0.0 < top.score <= 1.0
    assert len(top.evidence_event_ids) >= 2


def test_mad_drops_short_transient() -> None:
    # a brief spike (< MIN_SUSTAINED_S) must not be reported
    b = AnomalyBuilder("t")
    rows = []
    for i in range(100):
        v = 10.0 + (0.1 if i % 2 else -0.1)
        if 50 <= i <= 54:                         # 4s spike, below the sustained floor
            v = 40.0
        rows.append((f"metric-carts-{i:06d}", "carts", float(i), "cpu", v))
    df = pl.DataFrame(rows, schema=["event_id", "component_id", "ts", "metric_name", "value"],
                      orient="row")
    detect_mad(df, b)
    assert not b.anomalies


def test_mad_quiet_series_yields_nothing() -> None:
    b = AnomalyBuilder("t")
    rows = [(f"metric-carts-{i:06d}", "carts", float(i), "cpu", 5.0 + (0.05 if i % 2 else -0.05))
            for i in range(40)]
    df = pl.DataFrame(rows, schema=["event_id", "component_id", "ts", "metric_name", "value"],
                      orient="row")
    detect_mad(df, b)
    assert not b.anomalies


def test_iforest_flags_outlier_window() -> None:
    b = AnomalyBuilder("t")
    rows = []
    k = 0
    for w in range(30):                       # baseline (first 30%) = 9 windows >= min
        ts = float(w * 30)
        cpu, mem = (100.0, 5.0) if w in (22, 23) else (10.0 + (w % 3) * 0.2, 0.5)
        rows.append((f"metric-catalogue-{k:06d}", "catalogue", ts, "cpu", cpu)); k += 1
        rows.append((f"metric-catalogue-{k:06d}", "catalogue", ts, "mem", mem)); k += 1
    df = pl.DataFrame(rows, schema=["event_id", "component_id", "ts", "metric_name", "value"],
                      orient="row")
    detect_iforest(df, b)
    iso = [a for a in b.anomalies if a.method == "isolation_forest"]
    assert iso
    for a in iso:
        assert 0.0 <= a.score <= 1.0


# --------------------------- logs ---------------------------
def test_logs_rare_template_after_baseline(tmp_path: Path) -> None:
    b = AnomalyBuilder("t")
    rows = []
    i = 0
    for ts in range(0, 22):                       # baseline: common health template
        rows.append((f"log-catalogue-{i:06d}", "catalogue", float(ts),
                     "GET /health check ok status 200")); i += 1
    for ts in range(60, 71):                       # after baseline: brand-new error template
        rows.append((f"log-catalogue-{i:06d}", "catalogue", float(ts),
                     "ERROR database connection refused retrying now")); i += 1
    df = pl.DataFrame(rows, schema=["event_id", "component_id", "ts", "message"], orient="row")
    tmap = detect_logs(df, b, "t", persist_dir=tmp_path / "drain")
    assert len(tmap) == df.height                  # every event gets a template
    rare = [a for a in b.anomalies if a.method == "log_rare_template"]
    assert rare
    assert all(0.0 < a.score <= 1.0 for a in b.anomalies)


# --------------------------- alerts ---------------------------
def _alert_row(eid, comp, ts, name, severity, state):
    payload = json.dumps({"kind": "alert", "name": name, "severity": severity, "state": state})
    return (eid, comp, float(ts), name, float(severity), payload)


def test_alerts_dedup_and_flap() -> None:
    b = AnomalyBuilder("t")
    rows = []
    # 3 identical firings within 60s -> 1 deduped anomaly
    for k, ts in enumerate((0, 20, 40)):
        rows.append(_alert_row(f"alert-catalogue-{k:06d}", "catalogue", ts, "HighCPU", 0.8, "firing"))
    # flapping: 3 resolve->fire cycles within 5min
    seq = 0
    for ts in (0, 30, 60, 90, 120, 150):
        state = "firing" if seq % 2 == 0 else "resolved"
        rows.append(_alert_row(f"alert-payment-{seq:06d}", "payment", ts, "Flappy", 0.6, state))
        seq += 1
    df = pl.DataFrame(rows, schema=["event_id", "component_id", "ts", "metric_name", "value", "payload_json"],
                      orient="row")
    detect_alerts(df, b)
    cat = [a for a in b.anomalies if a.component_id == "catalogue"]
    pay = [a for a in b.anomalies if a.component_id == "payment"]
    assert len(cat) == 1 and cat[0].method == "alert_dedup"
    assert cat[0].score == pytest.approx(0.8)
    assert len(pay) == 1 and "flapping" in pay[0].summary.lower()


# --------------------------- config ---------------------------
def _config_row(eid, comp, ts, key, old, new, risky):
    payload = json.dumps({"kind": "config", "key": key, "old_value": old,
                          "new_value": new, "risky": risky})
    return (eid, comp, float(ts), payload)


def test_config_risky_flag_and_benign() -> None:
    b = AnomalyBuilder("t")
    rows = [
        _config_row("config-catalogue_db-000000", "catalogue-db", 100, "max_connections", 200, 20, True),
        _config_row("config-front_end-000000", "front-end", 100, "feature_flag_beta", False, True, False),
    ]
    df = pl.DataFrame(rows, schema=["event_id", "component_id", "ts", "payload_json"], orient="row")
    detect_config(df, b)
    risky = [a for a in b.anomalies if a.method == "config_risky_flag"]
    assert len(risky) == 1                         # only the max_connections change
    assert risky[0].component_id == "catalogue-db"
    assert "CANDIDATE" in risky[0].summary
    assert 0.0 < risky[0].score <= 1.0


# --------------------------- golden sanity harness ---------------------------
@pytest.fixture(scope="module")
def golden_anomalies():
    if not (STORE / f"case_id={GOLDEN}" / "topology.json").exists():
        pytest.skip("golden case not in store (run adapter + fetch_golden_case)")
    return detect(GOLDEN, store_root=STORE)


def _mass_after_fraction(anomalies, inject: float, min_dur: float = 30.0) -> float:
    """Score mass = score integrated over each anomaly's extent; the fraction
    'after inject' is the score-weighted anomalous time at/after inject_time. A
    sustained anomaly straddling inject contributes only its after-inject portion
    (window.start alone would misclassify a fault-onset or drift anomaly whose
    bulk is after inject). Point anomalies get a nominal `min_dur` extent."""
    total = after = 0.0
    for a in anomalies:
        start, end = a.window.start, max(a.window.end, a.window.start + min_dur)
        total += a.score * (end - start)
        after += a.score * max(0.0, end - max(start, inject))
    return after / total if total else 0.0


def test_golden_score_mass_after_inject(golden_anomalies) -> None:
    inject = json.loads((LABELS / f"{GOLDEN}.json").read_text())["inject_time"]  # test-only ground truth
    anomalies = golden_anomalies
    assert anomalies, "no anomalies detected on golden case"
    frac = _mass_after_fraction(anomalies, inject)
    assert frac >= 0.80, f"only {frac:.1%} of anomaly score mass after inject"


def test_golden_all_components_in_topology(golden_anomalies) -> None:
    topo = EventStore(STORE).load_topology(GOLDEN)
    assert topo is not None
    nodes = set(topo.nodes)
    offenders = [a.component_id for a in golden_anomalies if a.component_id not in nodes]
    assert not offenders, f"anomalies off-topology: {offenders}"


def test_golden_anomaly_ids_unique_and_valid(golden_anomalies) -> None:
    from backend.ingest.normalize import COMPONENT_RE  # noqa: F401  (regex sibling)
    import re
    ids = [a.anomaly_id for a in golden_anomalies]
    assert len(ids) == len(set(ids))
    for aid in ids:
        assert re.match(r"^anom-[A-Za-z0-9_.]+-[0-9]{4}$", aid)
