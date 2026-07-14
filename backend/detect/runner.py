"""Detection runner: detect(case_id) -> list[AnomalyEvent].

Reads events from the EventStore, runs the four modality detectors, fills mined
`template_id`s back onto the log events, and writes the anomalies to
`data/anomalies/{case_id}.{parquet,json}`. Runtime pipeline code: reads NO
ground truth.

    py -m backend.detect.runner --case catalogue_cpu-1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

from backend.detect import AnomalyBuilder
from backend.detect.alerts import detect_alerts
from backend.detect.config import detect_config
from backend.detect.logs import detect_logs
from backend.detect.metrics import detect_metrics
from backend.ingest.store import EventStore
from backend.models import AnomalyEvent

_DEFAULT_STORE = "data/parquet"
_DEFAULT_OUT = Path("data/anomalies")
_DEFAULT_DRAIN = Path("data/drain3")
# Only report anomalies at least this strong (normalized score). Filters weak
# background deviations so genuine anomalies cluster clearly.
MIN_ANOMALY_SCORE = 0.25


def detect(
    case_id: str,
    store_root: str | Path = _DEFAULT_STORE,
    out_dir: str | Path = _DEFAULT_OUT,
    drain_dir: str | Path = _DEFAULT_DRAIN,
    write: bool = True,
) -> list[AnomalyEvent]:
    store = EventStore(store_root)
    builder = AnomalyBuilder(case_id, min_score=MIN_ANOMALY_SCORE)

    df_metric = store.events(case_id, source="metric")
    df_log = store.events(case_id, source="log")
    df_alert = store.events(case_id, source="alert")
    df_config = store.events(case_id, source="config")

    detect_metrics(df_metric, builder)

    if df_log.height:
        template_map = detect_logs(df_log, builder, case_id, Path(drain_dir))
        # fill template_id back onto the stored log events
        filled = df_log.with_columns(
            pl.col("event_id").replace_strict(template_map, default=None).alias("template_id")
        )
        store.replace_source(case_id, "log", filled)

    detect_alerts(df_alert, builder)
    detect_config(df_config, builder)

    anomalies = builder.anomalies
    if write:
        _write(case_id, anomalies, Path(out_dir))
    return anomalies


def _write(case_id: str, anomalies: list[AnomalyEvent], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{case_id}.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in anomalies], indent=2), encoding="utf-8"
    )
    rows = [{
        "anomaly_id": a.anomaly_id,
        "case_id": a.case_id,
        "source": a.source,
        "component_id": a.component_id,
        "method": a.method,
        "start": a.window.start,
        "end": a.window.end,
        "score": a.score,
        "n_evidence": len(a.evidence_event_ids),
        "evidence_json": json.dumps(a.evidence_event_ids),
        "summary": a.summary,
    } for a in anomalies]
    schema = {
        "anomaly_id": pl.Utf8, "case_id": pl.Utf8, "source": pl.Utf8, "component_id": pl.Utf8,
        "method": pl.Utf8, "start": pl.Float64, "end": pl.Float64, "score": pl.Float64,
        "n_evidence": pl.Int64, "evidence_json": pl.Utf8, "summary": pl.Utf8,
    }
    pl.DataFrame(rows, schema=schema).write_parquet(out_dir / f"{case_id}.parquet")


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="detect.runner")
    ap.add_argument("--case", required=True)
    ap.add_argument("--store", default=_DEFAULT_STORE)
    ap.add_argument("--out", default=str(_DEFAULT_OUT))
    args = ap.parse_args(argv[1:])

    anomalies = detect(args.case, store_root=args.store, out_dir=args.out)
    by_method: dict[str, int] = {}
    for a in anomalies:
        by_method[a.method] = by_method.get(a.method, 0) + 1
    print(f"case_id     : {args.case}")
    print(f"anomalies   : {len(anomalies)}")
    print(f"by method   : {by_method}")
    print(f"written     : {args.out}/{args.case}.[json|parquet]")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
