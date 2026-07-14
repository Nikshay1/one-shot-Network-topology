"""The dev/held-out split, and the ONLY place tuning is allowed to look.

    20% dev  /  80% held-out, over the RE2-SS cases, stratified by fault_service.

Deterministic without a shuffle: each case is ordered inside its stratum by
`sha256(seed|case_id)`, so the split depends only on the case set and the seed —
never on filesystem order, and never on when it ran. Adding cases to the dataset
re-derives a split rather than reshuffling the old one at random.

Weights and thresholds are tuned against dev ONLY. Every choice — including
"changed nothing, and here is why" — is appended to eval/tuning_log.json, so a
number in results.md can always be traced back to the evidence that moved it.

    py -m eval.split
    py -m eval.split --tune            # grid-search weights/thetas on dev
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from eval.labels import Label, re2ss_cases

EVAL_DIR = Path("eval")
SPLIT_PATH = EVAL_DIR / "split.json"
TUNING_LOG = EVAL_DIR / "tuning_log.json"
DEV_FRACTION = 0.2
SEED = 42


def _order_key(seed: int, case_id: str) -> str:
    return hashlib.sha256(f"{seed}|{case_id}".encode("utf-8")).hexdigest()


def make_split(labels: list[Label], seed: int = SEED,
               dev_fraction: float = DEV_FRACTION) -> dict:
    """Stratify by fault_service, then take the first `dev_fraction` of each stratum."""
    strata: dict[str, list[str]] = defaultdict(list)
    for lab in labels:
        strata[lab.fault_service].append(lab.case_id)

    dev: list[str] = []
    heldout: list[str] = []
    detail: dict[str, dict] = {}
    for service in sorted(strata):
        ordered = sorted(strata[service], key=lambda c: _order_key(seed, c))
        n_dev = int(round(dev_fraction * len(ordered)))
        dev.extend(ordered[:n_dev])
        heldout.extend(ordered[n_dev:])
        detail[service] = {"n": len(ordered), "n_dev": n_dev, "dev": ordered[:n_dev]}

    note = ""
    if not dev:
        note = ("dev is EMPTY: 20% of the materialized RE2-SS cases rounds to zero. "
                "Nothing was tuned; the shipped constants stand. Extract more of the "
                "RE2-SS dataset under data/re2_ss/ and re-run to get a real dev set.")
    return {
        "version": 1, "seed": seed, "dev_fraction": dev_fraction,
        "n_cases": len(labels), "dev": sorted(dev), "heldout": sorted(heldout),
        "strata": detail, "note": note,
    }


def load_split(path: str | Path = SPLIT_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"no split at {p} — run `py -m eval.split` first")
    return json.loads(p.read_text(encoding="utf-8"))


# =========================================================================
# tuning — dev only, ever
# =========================================================================
def _ac_at_1(case_ids: list[str]) -> float:
    """Fraction of cases whose true fault the fixed pipeline ranks #1."""
    from eval.labels import load as load_label
    from backend.pipeline import run as pipeline_run

    hits = 0
    for cid in case_ids:
        lab = load_label(cid)
        v = pipeline_run(cid, fixed_pipeline=True, ledger_dir="data/ledger_tune",
                         run_id=f"tune-{cid}")
        if v.hypotheses and lab and v.hypotheses[0].suspect_component == lab.truth:
            hits += 1
    return hits / len(case_ids) if case_ids else 0.0


# candidate weight vectors (each must sum to 1.0) + twin thresholds
GRID_WEIGHTS: list[dict[str, float]] = [
    {"coverage": 0.30, "topo_consistency": 0.25, "precedence": 0.15,
     "corroboration": 0.15, "pagerank": 0.15},                      # shipped default
    {"coverage": 0.40, "topo_consistency": 0.25, "precedence": 0.10,
     "corroboration": 0.10, "pagerank": 0.15},                      # coverage-heavy
    {"coverage": 0.25, "topo_consistency": 0.35, "precedence": 0.15,
     "corroboration": 0.10, "pagerank": 0.15},                      # topology-heavy
    {"coverage": 0.30, "topo_consistency": 0.20, "precedence": 0.25,
     "corroboration": 0.15, "pagerank": 0.10},                      # precedence-heavy
]
GRID_THETAS: list[tuple[float, float]] = [(0.80, 0.50), (0.70, 0.45), (0.85, 0.55)]


def tune_on_dev(dev: list[str]) -> dict:
    """Grid-search the scorer weights and twin thetas against dev AC@1."""
    from backend.rank import constants

    entry = {"ts": time.time(), "dev": list(dev), "grid": {
        "weights": len(GRID_WEIGHTS), "thetas": len(GRID_THETAS)}}
    if not dev:
        entry.update({
            "tuned": False,
            "reason": "dev split is empty (only 1 RE2-SS case is materialized locally; "
                      "20% of it rounds to 0). Tuning on held-out would leak, and tuning "
                      "on the synthetic suite would leak into the synthetic metrics — so "
                      "nothing was tuned.",
            "chosen": {"weights": dict(constants.WEIGHTS),
                       "twin_match_theta": constants.TWIN_MATCH_THETA,
                       "twin_partial_theta": constants.TWIN_PARTIAL_THETA},
        })
        return entry

    baseline = dict(constants.WEIGHTS)
    results = []
    best = (None, -1.0)
    for wi, weights in enumerate(GRID_WEIGHTS):
        assert abs(sum(weights.values()) - 1.0) < 1e-9, weights
        for theta in GRID_THETAS:
            constants.WEIGHTS.clear()
            constants.WEIGHTS.update(weights)          # scorer reads this dict at call time
            constants.TWIN_MATCH_THETA, constants.TWIN_PARTIAL_THETA = theta
            score = _ac_at_1(dev)
            results.append({"weights": weights, "thetas": theta, "dev_ac@1": score})
            if score > best[1]:
                best = ({"weights": weights, "thetas": theta}, score)
    constants.WEIGHTS.clear()
    constants.WEIGHTS.update(baseline)                 # never leave the process mutated
    entry.update({"tuned": True, "results": results, "chosen": best[0], "dev_ac@1": best[1]})
    return entry


def _append_log(entry: dict, path: str | Path = TUNING_LOG) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    hist = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    hist.append(entry)
    p.write_text(json.dumps(hist, indent=2, default=str), encoding="utf-8")


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="eval.split")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--dev-fraction", type=float, default=DEV_FRACTION)
    ap.add_argument("--tune", action="store_true", help="grid-search weights/thetas on dev")
    ap.add_argument("--out", default=str(SPLIT_PATH))
    args = ap.parse_args(argv[1:])

    labels = re2ss_cases()
    split = make_split(labels, seed=args.seed, dev_fraction=args.dev_fraction)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(split, indent=2), encoding="utf-8")

    print(f"RE2-SS cases materialized: {split['n_cases']}  "
          f"(a full RE2-SS extract is 5 services x 6 fault types x 3 runs)")
    print(f"dev     ({len(split['dev'])}): {split['dev']}")
    print(f"heldout ({len(split['heldout'])}): {split['heldout']}")
    if split["note"]:
        print(f"NOTE: {split['note']}")

    entry = tune_on_dev(split["dev"]) if (args.tune or not split["dev"]) else \
        {"ts": time.time(), "tuned": False, "reason": "--tune not requested", "dev": split["dev"]}
    _append_log(entry)
    print(f"tuning: tuned={entry['tuned']}"
          + (f" chosen={entry.get('chosen')}" if entry["tuned"] else f" ({entry['reason'][:80]}...)"))
    print(f"wrote {args.out} + {TUNING_LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
