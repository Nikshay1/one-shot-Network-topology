"""Optional external baselines (N-Sigma, BARO) via the RCAEval package.

RCAEval is not a dependency of this project — it is a heavy, optional comparison.
If it is absent, or its API has moved, or the raw CSVs a baseline needs were never
extracted, this SKIPS and records the reason in eval/results.json. A skipped
baseline is a logged fact in results.md, never a silent gap and never a crash.

    py -m eval.baselines
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from eval.labels import load as load_label
from eval.split import load_split

RESULTS_JSON = Path("eval/results.json")
BASELINES = ("nsigma", "baro")
RE2SS_ROOT = Path("data/re2_ss")


def _import_rcaeval():
    """Return (module, error). RCAEval's entry point has moved between versions,
    so try the known homes rather than pinning one."""
    import importlib
    for path in ("RCAEval.e2e", "rcaeval.e2e", "RCAEval"):
        try:
            return importlib.import_module(path), None
        except ImportError as exc:
            last = str(exc)
    return None, f"RCAEval not importable ({last})"


def _case_dir(case_id: str) -> Path | None:
    """`catalogue_cpu-1` -> data/re2_ss/catalogue_cpu/1"""
    folder, _, run = case_id.rpartition("-")
    p = RE2SS_ROOT / folder / run
    return p if (p / "simple_metrics.csv").exists() else None


def run_baseline(name: str, case_ids: list[str]) -> dict:
    mod, err = _import_rcaeval()
    if mod is None:
        return {"name": name, "skipped": True, "reason": err}

    fn = getattr(mod, name, None) or getattr(mod, name.upper(), None)
    if fn is None:
        return {"name": name, "skipped": True,
                "reason": f"RCAEval exposes no {name!r} entry point (has: "
                          f"{[a for a in dir(mod) if not a.startswith('_')][:8]})"}

    import pandas as pd
    hits = {1: 0, 3: 0, 5: 0}
    scored, notes = 0, []
    for cid in case_ids:
        d = _case_dir(cid)
        lab = load_label(cid)
        if d is None or lab is None:
            notes.append(f"{cid}: raw case dir not extracted")
            continue
        try:
            df = pd.read_csv(d / "simple_metrics.csv")
            out = fn(df, inject_time=int(lab.inject_time))
            ranks = [str(r[0]) if isinstance(r, (list, tuple)) else str(r)
                     for r in (out.get("ranks") if isinstance(out, dict) else out)]
        except Exception as exc:                        # the package fought back
            notes.append(f"{cid}: {type(exc).__name__}: {exc}")
            continue
        scored += 1
        for k in hits:
            if any(lab.fault_service in r for r in ranks[:k]):
                hits[k] += 1

    if not scored:
        return {"name": name, "skipped": True,
                "reason": f"no case produced a ranking ({'; '.join(notes[:3]) or 'no cases'})"}
    return {"name": name, "skipped": False, "n": scored,
            "AC@1": round(hits[1] / scored, 4), "AC@3": round(hits[3] / scored, 4),
            "Avg@5": round(sum(hits[k] / scored for k in (1, 3, 5)) / 3, 4),
            "notes": notes[:5]}


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="eval.baselines")
    ap.add_argument("--baselines", nargs="*", default=list(BASELINES))
    ap.add_argument("--out", default=str(RESULTS_JSON))
    args = ap.parse_args(argv[1:])

    case_ids = load_split()["heldout"]
    results = [run_baseline(b, case_ids) for b in args.baselines]
    ok = [r for r in results if not r.get("skipped")]
    skipped = [r for r in results if r.get("skipped")]
    for r in results:
        print(f"  {r['name']:<8} " + (f"SKIPPED: {r['reason']}" if r.get("skipped")
                                      else f"AC@1={r['AC@1']} AC@3={r['AC@3']} n={r['n']}"))

    doc = json.loads(Path(args.out).read_text(encoding="utf-8")) \
        if Path(args.out).exists() else {"runs": [], "metrics": {}}
    doc["baselines"] = {
        "generated_at": time.time(), "results": ok,
        "skipped": not ok,
        "reason": "; ".join(f"{r['name']}: {r['reason']}" for r in skipped) if skipped else "",
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    print(f"wrote {args.out}")
    try:
        from eval.report import build
        build(doc)
    except Exception as exc:                            # pragma: no cover
        print(f"WARN: report not regenerated: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
