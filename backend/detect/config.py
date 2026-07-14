"""Config anomaly detection.

Rule-based risky-change classifier over the config key + value diff. Keywords
(acl/route/limit/timeout/replica/...) flag a change as risky. These anomalies
are CANDIDATE triggers surfaced for later ranking — NOT accusations.
"""

from __future__ import annotations

import json

import polars as pl

from backend.detect import AnomalyBuilder, normalize_score

# Diff keywords that make a config change worth surfacing as a candidate trigger.
RISKY_KEYWORDS = frozenset({
    "acl", "route", "limit", "timeout", "replica", "connection", "pool",
    "memory", "mem", "cpu", "quota", "socket", "thread", "max", "heap", "gc",
})
SCORE_CAP = 10.0


def _classify(key: str, old: object, new: object, risky_flag: bool) -> tuple[bool, list[str]]:
    text = f"{key} {old} {new}".lower()
    hits = sorted(k for k in RISKY_KEYWORDS if k in text)
    is_risky = risky_flag or bool(hits)
    return is_risky, hits


def detect_config(df: pl.DataFrame, builder: AnomalyBuilder) -> None:
    if df.height == 0:
        return
    for r in df.select(["event_id", "component_id", "ts", "payload_json"]).iter_rows(named=True):
        payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        key = str(payload.get("key", ""))
        old, new = payload.get("old_value"), payload.get("new_value")
        risky_flag = bool(payload.get("risky", False))

        is_risky, hits = _classify(key, old, new, risky_flag)
        if not is_risky:
            continue

        raw = min(3.0 + 2.0 * len(hits) + (2.0 if risky_flag else 0.0), SCORE_CAP)
        builder.make(
            component=r["component_id"],
            source="config",
            method="config_risky_flag",
            start=float(r["ts"]),
            end=float(r["ts"]),
            score=normalize_score(raw, SCORE_CAP),
            evidence_event_ids=[r["event_id"]],
            summary=f"CANDIDATE: risky change {key} {old}->{new} "
                    f"(keywords: {','.join(hits) or 'none'}).",
        )
