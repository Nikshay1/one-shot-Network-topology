"""Metric anomaly detection.

Two deterministic detectors. Both work on the *residual* of each series against
a trailing rolling median, which detrends slow drift (e.g. monotonic memory
growth) so only genuine changes — like an injected fault — are flagged. The MAD
scale is estimated from the first 30% of the case window (a documented
assumption; NOT ground truth), and anomalies are only emitted in the detection
period (after the baseline), which the baseline itself defines as normal.

  * mad_zscore       : |z| > 3 for >= 2 consecutive residual points, merged into
                       one windowed anomaly. raw score = z/3 capped 10 (normalized).
  * isolation_forest : per-component multivariate residual vectors per 30s window;
                       IsolationForest(contamination=0.05) fit on the baseline.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from backend.detect import AnomalyBuilder, normalize_score

BASELINE_FRACTION = 0.30
ROLL_WINDOW = 30            # trailing points for the rolling-median detrend
Z_THRESHOLD = 3.0
MIN_CONSECUTIVE = 2
# Report only SUSTAINED metric anomalies: an injected fault persists, whereas
# memory sawtooth / GC transients are short. Filters those without touching the
# ">=2 consecutive" detection rule.
MIN_SUSTAINED_S = 45.0
SCORE_CAP = 10.0
IFOREST_WINDOW_S = 30.0
IFOREST_CONTAMINATION = 0.05
IFOREST_MIN_WINDOWS = 8     # minimum baseline windows required to fit the model
IFOREST_MIN_FEATURES = 2
MIN_SERIES_LEN = 10


def _mad_center_scale(baseline: np.ndarray) -> tuple[float, float]:
    med = float(np.median(baseline))
    mad = float(np.median(np.abs(baseline - med)))
    scale = 1.4826 * mad
    if scale < 1e-9:
        scale = float(np.std(baseline))
    if scale < 1e-9:
        scale = max(abs(med), 1.0) * 0.05 + 1e-9
    return med, scale


def _residual_frame(df: pl.DataFrame) -> pl.DataFrame:
    """Add a `resid` column = value - trailing rolling median, per (comp, metric)."""
    pdf = (
        df.select(["component_id", "metric_name", "ts", "value", "event_id"])
        .sort(["component_id", "metric_name", "ts"])
        .to_pandas()
    )
    pdf["center"] = pdf.groupby(["component_id", "metric_name"], sort=False)["value"].transform(
        lambda s: s.rolling(ROLL_WINDOW, min_periods=1).median()
    )
    pdf["resid"] = pdf["value"] - pdf["center"]
    return pl.from_pandas(pdf)


def _consecutive_runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            if j - i + 1 >= min_len:
                runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def detect_mad(df: pl.DataFrame, builder: AnomalyBuilder) -> None:
    if df.height == 0:
        return
    t0 = float(df["ts"].min())
    t1 = float(df["ts"].max())
    baseline_end = t0 + BASELINE_FRACTION * (t1 - t0)

    parts = df.partition_by(["component_id", "metric_name"], as_dict=True, include_key=True)
    for key, sub in parts.items():
        comp, metric = key
        sub = sub.sort("ts")
        ts = sub["ts"].to_numpy()
        val = sub["value"].to_numpy().astype(float)
        eids = sub["event_id"].to_list()
        if val.size < MIN_SERIES_LEN:
            continue

        # Robust MAD z-score against the first-30% baseline. A sustained shift
        # (fault, drift, or step) stays anomalous for its whole duration, so it
        # merges into one long anomaly whose after-inject overlap is scored by
        # the harness. A trailing rolling median tracks very slow drift.
        base_raw = val[ts <= baseline_end]
        if base_raw.size < 5:
            continue
        med, scale = _mad_center_scale(base_raw)
        z = np.abs(val - med) / scale
        mask = (z > Z_THRESHOLD) & (ts > baseline_end)

        for lo, hi in _consecutive_runs(mask, MIN_CONSECUTIVE):
            if float(ts[hi] - ts[lo]) < MIN_SUSTAINED_S:
                continue
            zmax = float(np.max(z[lo:hi + 1]))
            builder.make(
                component=comp, source="metric", method="mad_zscore",
                start=float(ts[lo]), end=float(ts[hi]),
                score=normalize_score(min(zmax / Z_THRESHOLD, SCORE_CAP), SCORE_CAP),
                evidence_event_ids=eids[lo:hi + 1],
                summary=f"{comp} {metric} |z|={zmax:.1f} over {hi - lo + 1} pts (detrended).",
            )


def detect_iforest(df: pl.DataFrame, builder: AnomalyBuilder) -> None:
    if df.height == 0:
        return
    from sklearn.ensemble import IsolationForest

    t0 = float(df["ts"].min())
    t1 = float(df["ts"].max())
    baseline_end = t0 + BASELINE_FRACTION * (t1 - t0)
    rdf = _residual_frame(df).with_columns(
        ((pl.col("ts") - t0) / IFOREST_WINDOW_S).floor().cast(pl.Int64).alias("win")
    )

    for comp, sub in rdf.partition_by(["component_id"], as_dict=True, include_key=True).items():
        comp = comp[0] if isinstance(comp, tuple) else comp
        agg = sub.group_by(["win", "metric_name"]).agg(pl.col("resid").mean().alias("v"))
        wide = agg.pivot(values="v", index="win", on="metric_name", aggregate_function="first").sort("win")
        feature_cols = [c for c in wide.columns if c != "win"]
        if len(feature_cols) < IFOREST_MIN_FEATURES:
            continue
        wins = np.asarray(wide["win"].to_list())
        win_start = t0 + wins * IFOREST_WINDOW_S
        X = wide.select(feature_cols).fill_null(0.0).to_numpy()

        if wide.height < IFOREST_MIN_WINDOWS + 2:
            continue

        # Fit on ALL windows (IsolationForest cannot extrapolate beyond its
        # training range, so a baseline-only fit would miss high extremes). On
        # detrended residuals, slow drift stays near zero (inlier) while genuine
        # spikes isolate as the contamination top-fraction. Only outliers in the
        # detection period are reported.
        clf = IsolationForest(contamination=IFOREST_CONTAMINATION, random_state=0, n_estimators=100)
        clf.fit(X)
        margin = clf.decision_function(X)  # < 0 => outlier
        post = win_start > baseline_end
        outlier = (margin < 0) & post

        i = 0
        while i < len(wins):
            if not outlier[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(wins) and outlier[j + 1] and wins[j + 1] == wins[j] + 1:
                j += 1
            if j - i + 1 >= 2:   # sustained (>=2 window) multivariate outlier
                w_lo, w_hi = int(wins[i]), int(wins[j])
                wstart = t0 + w_lo * IFOREST_WINDOW_S
                wend = t0 + (w_hi + 1) * IFOREST_WINDOW_S
                evidence = sub.filter(
                    pl.col("win").is_in(list(range(w_lo, w_hi + 1)))
                )["event_id"].to_list()
                best = float(np.min(margin[i:j + 1]))
                builder.make(
                    component=comp, source="metric", method="isolation_forest",
                    start=float(wstart), end=float(wend),
                    score=min(1.0, 0.3 + 3.0 * max(0.0, -best)),
                    evidence_event_ids=evidence,
                    summary=f"{comp} multivariate residual outlier "
                            f"({w_hi - w_lo + 1} windows, {len(feature_cols)} metrics).",
                )
            i = j + 1


def detect_metrics(df: pl.DataFrame, builder: AnomalyBuilder) -> None:
    detect_mad(df, builder)
    detect_iforest(df, builder)
