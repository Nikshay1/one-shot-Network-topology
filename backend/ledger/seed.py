"""Seed a run's ledger with the evidence detection actually found.

Why this exists
---------------
The ledger is documented as the evidence trail, and `Ledger.query()` is the ONLY
read surface the narrator gets (`NARRATOR_TOOLS = ["query_evidence_ledger"]` —
exactly one tool). But nothing ever filed the *observations*: `anomaly_observed`
had zero call sites in the whole repo, as did `config_change_observed`. Every
fact in a finished run's ledger was a derived conclusion — counterfactual, twin,
hypothesis_scored — so the ledger recorded what we CONCLUDED and never what we
SAW.

The consequence was reported as "the model keeps saying nothing was detected",
and the model was right. On `red_herring_config-01` the detectors find 12
anomalies; the ledger held 14 facts and not one of them was an observation, so
the narrator's Timeline printed this on every run that has ever executed:

    ## Timeline
    - No timeline facts were recorded for this run.

It was not confused. It was correctly refusing to assert something it could not
cite, because a claim without a resolving `[fact-...]` is deleted by
`validate_citations`. The chat inherited the same blindness for the same reason.

How the gap happened is worth keeping: filing evidence was delegated to the
agent (rule 9 — `file_finding` is the only mutation an agent gets, and the
prompt says "file_finding every conclusion with event ids"). The deterministic
floor writes its own conclusions directly, but was never given the same job for
the anomalies it detected. So when the agent errors, has no key, or simply does
not bother, nobody files them.

    py -m backend.ledger.seed --case red_herring_config-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.models import AnomalyEvent

# AnomalyEvent.source values that are a config CHANGE rather than a symptom. The
# detector emits these as anomalies (`config_risky_flag` CANDIDATE triggers), but a
# config change is not a symptom, and the narrator's Timeline reads the two kinds
# separately — so file each as what it actually is.
_CONFIG_SOURCE = "config"


def already_seeded(ledger) -> bool:
    """True once this run's observations are filed.

    The investigator seeds its fresh ledger and then, on the rule-11 fallback, hands
    the SAME ledger to the autopilot — which would file everything a second time.
    Cheap to check (one bounded query), and the alternative is a ledger that
    double-counts its own evidence.
    """
    return bool(ledger.query(kind="anomaly_observed", limit=1)
                or ledger.query(kind="config_change_observed", limit=1))


def seed_from_anomalies(ledger, anomalies: list[AnomalyEvent]) -> int:
    """File one fact per detected anomaly. Idempotent. Returns the number filed.

    The mapping is deliberately lossless-ish: the detector's own `summary` is the
    statement, its `evidence_event_ids` become the fact's event_ids (so the claim is
    traceable back to raw telemetry), its window becomes the fact's ts_range, and its
    score becomes the confidence. Nothing is invented here — this is transcription,
    not analysis.
    """
    if already_seeded(ledger):
        return 0

    filed = 0
    # Time order, so the Timeline reads as a timeline rather than as detector output.
    for a in sorted(anomalies, key=lambda x: (x.window.start, x.anomaly_id)):
        ts_range = (a.window.start, a.window.end)
        if a.source == _CONFIG_SOURCE:
            ledger.config_change_observed(
                statement=a.summary,
                component_ids=[a.component_id],
                event_ids=list(a.evidence_event_ids),
                ts_range=ts_range,
                confidence=a.score,
            )
        else:
            ledger.anomaly_observed(
                statement=a.summary,
                component_ids=[a.component_id],
                event_ids=list(a.evidence_event_ids),
                ts_range=ts_range,
                modality=a.source,
                confidence=a.score,
            )
        filed += 1
    return filed


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ledger.seed",
                                 description="File a run's detected anomalies as ledger facts.")
    ap.add_argument("--case", required=True)
    ap.add_argument("--run", default=None, help="run_id (defaults to --case)")
    ap.add_argument("--anomalies-dir", default="data/anomalies")
    ap.add_argument("--ledger-dir", default="data/ledger")
    ap.add_argument("--fresh", action="store_true", help="start the run's ledger empty first")
    args = ap.parse_args(argv[1:])

    from backend.ledger.ledger import Ledger
    from backend.rank.scorer import load_anomalies

    run_id = args.run or args.case
    anomalies = load_anomalies(args.case, args.anomalies_dir)
    ledger = Ledger(run_id, args.case, args.ledger_dir, fresh=args.fresh)
    n = seed_from_anomalies(ledger, anomalies)
    print(f"case={args.case} anomalies={len(anomalies)} filed={n}"
          f"{' (already seeded)' if n == 0 and anomalies else ''}")
    for f in ledger.query(limit=20):
        print(f"  {f.fact_id:<24} {f.kind:<24} {f.statement[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
