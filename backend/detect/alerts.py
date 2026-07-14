"""Alert anomaly detection.

  * de-duplicate identical (component, alert_name) firings within 60s;
  * flap suppression: >=3 fire/resolve cycles within 5min -> a single "flapping"
    anomaly;
  * every surviving alert group -> one anomaly (method=alert_dedup).
"""

from __future__ import annotations

import json

import polars as pl

from backend.detect import AnomalyBuilder

DEDUP_WINDOW_S = 60.0
FLAP_WINDOW_S = 300.0
FLAP_CYCLES = 3


def _rows(df: pl.DataFrame) -> list[dict]:
    out = []
    for r in df.select(["event_id", "component_id", "ts", "metric_name", "value", "payload_json"]).iter_rows(named=True):
        payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        out.append({
            "event_id": r["event_id"],
            "component_id": r["component_id"],
            "ts": float(r["ts"]),
            "name": payload.get("name", r["metric_name"] or "alert"),
            "severity": float(payload.get("severity", r["value"] or 0.0)),
            "state": payload.get("state", "firing"),
        })
    return out


def detect_alerts(df: pl.DataFrame, builder: AnomalyBuilder) -> None:
    if df.height == 0:
        return
    rows = _rows(df)
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["component_id"], r["name"]), []).append(r)

    for (comp, name), evs in groups.items():
        evs.sort(key=lambda e: e["ts"])

        # flap detection: a cycle = a firing later completed by a resolve.
        # >=FLAP_CYCLES cycles within any FLAP_WINDOW_S window => flapping.
        cycle_ts: list[float] = []
        armed = False
        for e in evs:
            if e["state"] == "firing":
                armed = True
            elif e["state"] == "resolved" and armed:
                cycle_ts.append(e["ts"])
                armed = False
        flapping = any(
            sum(1 for t in cycle_ts if ts <= t < ts + FLAP_WINDOW_S) >= FLAP_CYCLES
            for ts in cycle_ts
        )
        if flapping:
            builder.make(
                component=comp, source="alert", method="alert_dedup",
                start=evs[0]["ts"], end=evs[-1]["ts"],
                score=max(e["severity"] for e in evs),
                evidence_event_ids=[e["event_id"] for e in evs],
                summary=f"{comp} {name} flapping: {len(cycle_ts)} fire/resolve cycles.",
            )
            continue

        # dedup firing events into clusters separated by > DEDUP_WINDOW_S
        firing = [e for e in evs if e["state"] == "firing"]
        if not firing:
            continue
        cluster: list[dict] = [firing[0]]
        clusters: list[list[dict]] = []
        for e in firing[1:]:
            if e["ts"] - cluster[-1]["ts"] <= DEDUP_WINDOW_S:
                cluster.append(e)
            else:
                clusters.append(cluster)
                cluster = [e]
        clusters.append(cluster)

        for cl in clusters:
            builder.make(
                component=comp, source="alert", method="alert_dedup",
                start=cl[0]["ts"], end=cl[-1]["ts"],
                score=max(e["severity"] for e in cl),
                evidence_event_ids=[e["event_id"] for e in cl],
                summary=f"{comp} {name} firing ({len(cl)} deduped within 60s).",
            )
