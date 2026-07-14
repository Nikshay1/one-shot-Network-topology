"""Log anomaly detection.

  * Drain3 (depth 4, sim 0.4, state persisted per case) mines templates and fills
    `template_id` back onto the log events. Messages are truncated and numeric/
    hex/timestamp tokens are masked so highly-variable payloads (RE2-SS logs carry
    large JSON blobs) collapse to a small, meaningful template set instead of
    thousands of one-off templates.
  * log_freq_spike   : per (component, template) counts per 30s window; a window
    whose count exceeds its baseline Poisson rate with tail p < 1e-4.
  * log_rare_template: templates never seen during the baseline (first 30%) that
    then recur (>= MIN_RARE_COUNT) afterwards.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import poisson

from backend.detect import AnomalyBuilder, normalize_score

BASELINE_FRACTION = 0.30
WINDOW_S = 30.0
DRAIN_DEPTH = 4
DRAIN_SIM_TH = 0.4
POISSON_P = 1e-4
SCORE_CAP = 10.0
MSG_TRUNCATE = 200
MIN_RARE_COUNT = 5          # a genuinely rare template must recur, not fire once
MIN_SPIKE_OBS = 5           # ignore micro-spikes over tiny baselines
_DEFAULT_PERSIST_DIR = Path("data/drain3")


def _masking():
    from drain3.masking import MaskingInstruction
    return [
        MaskingInstruction(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*", "TS"),
        MaskingInstruction(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "IP"),
        MaskingInstruction(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b", "UUID"),
        MaskingInstruction(r"0x[0-9a-fA-F]+", "HEX"),
        MaskingInstruction(r"\b\d+(?:\.\d+)?(?:ms|s|us|µs)?\b", "NUM"),
    ]


def _build_miner(case_id: str, persist_dir: Path):
    from drain3 import TemplateMiner
    from drain3.file_persistence import FilePersistence
    from drain3.template_miner_config import TemplateMinerConfig

    persist_dir.mkdir(parents=True, exist_ok=True)
    cfg = TemplateMinerConfig()
    cfg.drain_depth = DRAIN_DEPTH
    cfg.drain_sim_th = DRAIN_SIM_TH
    cfg.profiling_enabled = False
    cfg.masking_instructions = _masking()
    persistence = FilePersistence(str(persist_dir / f"{case_id}.bin"))
    return TemplateMiner(persistence, cfg)


def mine_templates(df: pl.DataFrame, case_id: str, persist_dir: Path) -> dict[str, str]:
    """Return {event_id -> template_id}, mining messages (truncated) in time order."""
    miner = _build_miner(case_id, persist_dir)
    ordered = df.sort("ts").select(["event_id", "message"])
    mapping: dict[str, str] = {}
    for event_id, message in ordered.iter_rows():
        msg = (message or "")[:MSG_TRUNCATE]
        result = miner.add_log_message(msg)
        mapping[event_id] = str(result["cluster_id"])
    miner.save_state("final")
    return mapping


def detect_logs(
    df: pl.DataFrame,
    builder: AnomalyBuilder,
    case_id: str,
    persist_dir: Path = _DEFAULT_PERSIST_DIR,
) -> dict[str, str]:
    """Mine templates, emit spike + rare-template anomalies, and return the
    {event_id -> template_id} map so the caller can fill it back onto events."""
    if df.height == 0:
        return {}

    template_map = mine_templates(df, case_id, Path(persist_dir))
    t0 = float(df["ts"].min())
    t1 = float(df["ts"].max())
    baseline_end = t0 + BASELINE_FRACTION * (t1 - t0)
    n_baseline_windows = max(1.0, (baseline_end - t0) / WINDOW_S)

    work = df.select(["event_id", "component_id", "ts"]).with_columns(
        pl.col("event_id").replace_strict(template_map, default="?").alias("template_id"),
        ((pl.col("ts") - t0) / WINDOW_S).floor().cast(pl.Int64).alias("win"),
        (pl.col("ts") <= baseline_end).alias("is_base"),
    )

    for key, sub in work.partition_by(["component_id", "template_id"], as_dict=True, include_key=True).items():
        comp, template = key
        base_n = int(sub["is_base"].sum())
        post = sub.filter(~pl.col("is_base"))
        if post.height == 0:
            continue

        if base_n == 0:
            # never seen during baseline -> rare (only if it recurs meaningfully)
            if post.height < MIN_RARE_COUNT:
                continue
            builder.make(
                component=comp, source="log", method="log_rare_template",
                start=float(post["ts"].min()), end=float(post["ts"].max()),
                score=normalize_score(min(post.height, SCORE_CAP), SCORE_CAP),
                evidence_event_ids=post["event_id"].to_list(),
                summary=f"{comp} template {template} unseen in baseline; "
                        f"{post.height} occurrences after.",
            )
            continue

        exp = base_n / n_baseline_windows
        counts = post.group_by("win").agg(
            pl.len().alias("obs"), pl.col("event_id"),
            pl.col("ts").min().alias("wmin"), pl.col("ts").max().alias("wmax"),
        )
        obs = counts["obs"].to_numpy()
        pvals = poisson.sf(obs - 1, exp)  # vectorized P(X >= obs)
        spike_mask = (pvals < POISSON_P) & (obs >= MIN_SPIKE_OBS)
        if not spike_mask.any():
            continue
        spikes = counts.filter(pl.Series(spike_mask))
        max_obs = int(spikes["obs"].max())
        evidence = [e for lst in spikes["event_id"].to_list() for e in lst]
        raw = min(max_obs / exp, SCORE_CAP)
        builder.make(
            component=comp, source="log", method="log_freq_spike",
            start=float(spikes["wmin"].min()), end=float(spikes["wmax"].max()),
            score=normalize_score(raw, SCORE_CAP),
            evidence_event_ids=evidence,
            summary=f"{comp} template {template} spike: obs={max_obs} vs exp={exp:.2f}/win.",
        )

    return template_map
