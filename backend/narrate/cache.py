"""Shared response/transcript cache for the narrator and all agents.

Agent transcripts are cached by `backend.agents.transcript`; this adds the
narration-response cache and the warm-up CLI that runs every demo scenario
end-to-end so an OFFLINE demo has everything it needs.

    py -m backend.narrate.cache --warm-cache --all-demo-scenarios
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_DIR = Path("data/cache")


def key_for(*parts: str) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def path_for(kind: str, key: str, directory: str | Path = DEFAULT_DIR) -> Path:
    return Path(directory) / kind / f"{key}.json"


def get(kind: str, key: str, directory: str | Path = DEFAULT_DIR):
    p = path_for(kind, key, directory)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:                       # pragma: no cover - corrupt cache
        return None


def put(kind: str, key: str, value, directory: str | Path = DEFAULT_DIR) -> Path:
    p = path_for(kind, key, directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, default=str, indent=2), encoding="utf-8")
    return p


def demo_scenarios() -> list[dict]:
    """One variant per scenario type — the demo set."""
    from backend.overlay.scenarios import load_registry
    seen: set[str] = set()
    out: list[dict] = []
    for v in load_registry()["variants"]:
        if v["scenario_type"] in seen:
            continue
        seen.add(v["scenario_type"])
        out.append(v)
    return out


def warm_all_demo_scenarios(out_root: str | Path = "data/demo", verbose: bool = True) -> list[dict]:
    """Build + detect + run the full pipeline (remediate + narrate) for every demo
    scenario, so OFFLINE=1 can replay all of it with zero API calls."""
    from backend.detect.runner import detect
    from backend.ingest.store import EventStore
    from backend.overlay.scenarios import build_variant
    from backend.pipeline import run as pipeline_run

    root = Path(out_root)
    results = []
    for variant in demo_scenarios():
        bundle, _label = build_variant(variant, 42)
        cid = bundle.case_id
        store = EventStore(root / "parquet")
        store.write_case(bundle)
        store.write_topology(cid, bundle.topology)
        detect(cid, store_root=root / "parquet", out_dir=root / "anomalies",
               drain_dir=root / "drain3")
        v = pipeline_run(cid, store_root=root / "parquet", anomalies_dir=root / "anomalies",
                         ledger_dir=root / "ledger", transcripts_dir=root / "transcripts")
        rec = {"case_id": cid, "scenario_type": variant["scenario_type"], "mode": v.mode,
               "top": v.hypotheses[0].suspect_component if v.hypotheses else None,
               "remediation": v.remediation.status if v.remediation else None,
               "narration_valid": v.narration.citations_valid if v.narration else None}
        put("demo", key_for(cid), rec)
        results.append(rec)
        if verbose:
            print(f"  warmed {cid:<24} mode={rec['mode']:<9} top={rec['top']} "
                  f"remediation={rec['remediation']} narration_valid={rec['narration_valid']}")
    return results


def case_for(n: int) -> str:
    """The case_id of demo scenario N (1-based) — the mapping `make demo-N` uses."""
    demo = demo_scenarios()
    if not 1 <= n <= len(demo):
        raise SystemExit(f"demo scenario {n} out of range (1..{len(demo)})")
    return demo[n - 1]["variant_id"]


def assert_warm(case_id: str, directory: str | Path = DEFAULT_DIR) -> dict:
    """A demo must never discover a cold cache in front of an audience. Fail loudly
    and say exactly how to fix it."""
    rec = get("demo", key_for(case_id), directory)
    if rec is None:
        raise SystemExit(
            f"COLD CACHE for {case_id!r}: no demo cache entry under {Path(directory) / 'demo'}.\n"
            f"  fix: py -m backend.narrate.cache --warm-cache --all-demo-scenarios")
    print(f"-- warm cache OK for {case_id}: mode={rec.get('mode')} top={rec.get('top')} "
          f"remediation={rec.get('remediation')} narration_valid={rec.get('narration_valid')}")
    return rec


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="narrate.cache")
    ap.add_argument("--warm-cache", action="store_true")
    ap.add_argument("--all-demo-scenarios", action="store_true")
    ap.add_argument("--case-for", type=int, default=None, metavar="N",
                    help="print the case_id of demo scenario N (used by make demo-N)")
    ap.add_argument("--assert-warm", default=None, metavar="CASE_ID",
                    help="exit non-zero unless CASE_ID's demo cache is warm")
    ap.add_argument("--list", action="store_true", help="list the demo scenarios")
    ap.add_argument("--out", default="data/demo")
    ap.add_argument("--cache-dir", default=str(DEFAULT_DIR))
    args = ap.parse_args(argv[1:])

    if args.case_for is not None:
        print(case_for(args.case_for))
        return 0
    if args.assert_warm:
        assert_warm(args.assert_warm, args.cache_dir)
        return 0
    if args.list:
        for i, v in enumerate(demo_scenarios(), start=1):
            print(f"{i:>2}  {v['variant_id']:<24} {v['scenario_type']}")
        return 0
    if args.warm_cache and args.all_demo_scenarios:
        print(f"warming {len(demo_scenarios())} demo scenarios end-to-end ...")
        rows = warm_all_demo_scenarios(args.out)
        print(f"cached {len(rows)} demo scenarios -> {args.out} (+ {DEFAULT_DIR})")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
