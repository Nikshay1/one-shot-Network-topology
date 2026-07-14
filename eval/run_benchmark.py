"""The numbers.

Suites
------
`--heldout`   the held-out RE2-SS split  -> AC@1 / AC@3 / Avg@5 (the RCAEval metrics)
`--synthetic` the 25 overlay scenarios   -> precision@1/@3, red-herring false-blame
                                            rate, median time-to-RCA

Modes
-----
`--agentic` (default)  investigator + challenger decide where to spend
`--fixed-pipeline`     the deterministic autopilot — and the ablation base
`--with-ablations`     also runs --no-counterfactual / --no-topology / --no-twin

THE HEADLINE is the agent-efficiency table. The fixed pipeline ALWAYS spends 5
counterfactuals + 1 twin, whatever the case looks like. The agent picks its
targets, so it should reach equal-or-better AC@k for fewer expensive ops. That
comparison — not AC@k alone — is the claim this project is making.

Everything is run at speed=0 (no replay pacing), so wall-clock is compute, not sleep.

    py -m eval.run_benchmark --heldout --agentic
    py -m eval.run_benchmark --heldout --fixed-pipeline --with-ablations
    py -m eval.run_benchmark --synthetic --fixed-pipeline
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from eval.labels import Label, synthetic_cases
from eval.split import load_split

RESULTS_JSON = Path("eval/results.json")
AC_K = 5
SCENARIO_SEED = 42                          # the seed data/labels was written at

# What the fixed pipeline spends on EVERY case, by construction (autopilot._CF_TOP_K
# counterfactuals + one twin for the top-1). The agent's spend is measured.
FIXED_BUDGET_NOTE = "fixed always spends 5 counterfactuals + 1 twin"

# Per-case LLM cost. gpt-4o $2.50/1M in, $10/1M out; gpt-4o-mini $0.15/$0.60.
# Only the investigator/challenger/remediation/narrator calls count.
_PRICES = {"gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60)}


@dataclass
class CaseResult:
    case_id: str
    suite: str
    mode: str
    truth: str | None
    ranked: list[str] = field(default_factory=list)
    rank_of_truth: int | None = None
    tier_of_top1: str | None = None
    top1: str | None = None
    wall_clock_s: float = 0.0
    tool_calls: int = 0
    cost_points: int = 0
    expensive_ops: int = 0
    expensive_detail: list[str] = field(default_factory=list)
    pipeline_mode: str = ""                 # what the pipeline ACTUALLY ran as
    false_blame: bool = False               # top-1 is a known-innocent component
    scenario_type: str | None = None
    error: str | None = None
    unscoreable: bool = False               # the scenario has no single right answer

    def hit_at(self, k: int) -> bool:
        return self.rank_of_truth is not None and self.rank_of_truth <= k


# =========================================================================
# ablations
# =========================================================================
@contextlib.contextmanager
def no_topology():
    """Zero the two topology-derived sub-scores and renormalize the rest.

    The scorer looks `WEIGHTS` up at call time, so mutating the dict in place is
    the ablation — no plumbing, and `score_breakdown` still sums to `score`.
    """
    from backend.rank import constants
    saved = dict(constants.WEIGHTS)
    keep = {k: v for k, v in saved.items() if k not in ("topo_consistency", "pagerank")}
    total = sum(keep.values())
    constants.WEIGHTS.clear()
    constants.WEIGHTS.update({k: (v / total if total else 0.0) for k, v in keep.items()})
    constants.WEIGHTS.update({"topo_consistency": 0.0, "pagerank": 0.0})
    try:
        yield
    finally:
        constants.WEIGHTS.clear()
        constants.WEIGHTS.update(saved)


ABLATIONS = {
    "no-counterfactual": {"counterfactual_enabled": False},
    "no-twin": {"twin_enabled": False},
    "no-topology": {},                       # applied via the context manager
}


# =========================================================================
# one case
# =========================================================================
def ensure_case(case_id: str, store_root: str = "data/parquet",
                anomalies_dir: Path = Path("data/anomalies")) -> None:
    """Materialize a case before benchmarking it, instead of trusting whatever is on disk.

    Synthetic variants are rebuilt from the registry at the same seed the labels were
    written with, so the events being scored are the events the ground truth describes.
    """
    from backend.detect.runner import detect
    from backend.ingest.store import EventStore

    store = EventStore(store_root)
    built = False
    if store.load_topology(case_id) is None:
        from backend.overlay.scenarios import build_variant, load_registry
        variant = next((v for v in load_registry()["variants"]
                        if v["variant_id"] == case_id), None)
        if variant is None:
            raise FileNotFoundError(f"no topology and no registry variant for {case_id!r}")
        bundle, _ = build_variant(variant, SCENARIO_SEED)
        store.write_case(bundle)
        store.write_topology(case_id, bundle.topology)
        built = True
    if built or not (anomalies_dir / f"{case_id}.json").exists():
        detect(case_id, store_root=store_root, out_dir=anomalies_dir)


def run_case(lab: Label, suite: str, mode: str, *, fixed: bool, ablation: str | None = None,
             ledger_root: Path = Path("data/ledger_eval")) -> CaseResult:
    from backend.pipeline import run as pipeline_run

    res = CaseResult(case_id=lab.case_id, suite=suite, mode=mode, truth=lab.truth,
                     scenario_type=lab.scenario_type)
    kwargs = dict(ABLATIONS.get(ablation or "", {}))
    ctx = no_topology() if ablation == "no-topology" else contextlib.nullcontext()

    res.unscoreable = lab.truth is None
    t0 = time.monotonic()
    try:
        ensure_case(lab.case_id)
        with ctx:
            v = pipeline_run(lab.case_id, fixed_pipeline=fixed,
                             ledger_dir=ledger_root / mode, run_id=f"{mode}-{lab.case_id}",
                             **kwargs)
    except Exception as exc:                 # a case that explodes is a zero, not a crash
        res.error = f"{type(exc).__name__}: {exc}"
        res.wall_clock_s = round(time.monotonic() - t0, 3)
        return res
    res.wall_clock_s = round(time.monotonic() - t0, 3)

    res.ranked = [h.suspect_component for h in v.hypotheses]
    res.top1 = res.ranked[0] if res.ranked else None
    res.tier_of_top1 = v.hypotheses[0].tier if v.hypotheses else None
    res.rank_of_truth = ((res.ranked.index(lab.truth) + 1)
                         if lab.truth and lab.truth in res.ranked else None)
    res.tool_calls = v.tool_calls
    res.cost_points = v.cost_points_spent
    res.expensive_detail = list(v.expensive_checks)
    res.expensive_ops = len(v.expensive_checks)
    res.pipeline_mode = v.mode
    res.false_blame = bool(res.top1 and res.top1 in lab.ground_truth_innocent)
    return res


# =========================================================================
# metrics
# =========================================================================
def _scoreable(rows: list[CaseResult]) -> list[CaseResult]:
    return [r for r in rows if not r.error and not r.unscoreable]


def ac_metrics(rows: list[CaseResult]) -> dict:
    scored = _scoreable(rows)
    n = len(scored)
    if not n:
        return {"n": 0, "excluded_unscoreable": sum(r.unscoreable for r in rows),
                "errors": sum(bool(r.error) for r in rows)}
    ac = {f"AC@{k}": round(sum(r.hit_at(k) for r in scored) / n, 4) for k in (1, 3)}
    ac["Avg@5"] = round(sum(sum(r.hit_at(k) for r in scored) / n
                            for k in range(1, AC_K + 1)) / AC_K, 4)
    ac["n"] = n
    ac["excluded_unscoreable"] = sum(r.unscoreable for r in rows)
    ac["errors"] = sum(bool(r.error) for r in rows)
    return ac


def synthetic_metrics(rows: list[CaseResult]) -> dict:
    scored = _scoreable(rows)
    n = len(scored)
    if not n:
        return {"n": 0, "excluded_unscoreable": sum(r.unscoreable for r in rows),
                "errors": sum(bool(r.error) for r in rows)}
    rh = [r for r in scored if r.scenario_type == "red_herring_config"]
    return {
        "n": n,
        "excluded_unscoreable": sum(r.unscoreable for r in rows),
        "errors": sum(bool(r.error) for r in rows),
        "precision@1": round(sum(r.hit_at(1) for r in scored) / n, 4),
        "precision@3": round(sum(r.hit_at(3) for r in scored) / n, 4),
        "red_herring_false_blame_rate": (round(sum(r.false_blame for r in rh) / len(rh), 4)
                                         if rh else None),
        "red_herring_n": len(rh),
        "median_time_to_rca_s": round(statistics.median(r.wall_clock_s for r in scored), 3),
    }


def efficiency(rows: list[CaseResult]) -> dict:
    scored = [r for r in rows if not r.error]      # spend is real even when unscoreable
    if not scored:
        return {"n": 0}
    mean = lambda f: round(statistics.fmean(f(r) for r in scored), 3)   # noqa: E731
    return {
        "n": len(scored),
        "mean_tool_calls": mean(lambda r: r.tool_calls),
        "mean_cost_points": mean(lambda r: r.cost_points),
        "mean_expensive_ops": mean(lambda r: r.expensive_ops),
        "mean_wall_clock_s": mean(lambda r: r.wall_clock_s),
        "total_wall_clock_s": round(sum(r.wall_clock_s for r in scored), 3),
    }


def llm_cost_note(rows: list[CaseResult]) -> dict:
    """What the LLM actually cost. With no key the agents never call out, and rule 11
    turns every 'agentic' run into the autopilot — so the honest number is $0.00 and
    the honest caveat is that this is NOT an agentic measurement."""
    import os
    degraded = [r for r in rows if r.mode.startswith("agentic") and r.pipeline_mode != "agentic"]
    has_key = bool(os.getenv("OPENAI_API_KEY"))
    return {
        "openai_api_key_present": has_key,
        "offline": os.getenv("OFFLINE", "0") == "1",
        "prices_usd_per_1m_tokens": {m: {"in": i, "out": o} for m, (i, o) in _PRICES.items()},
        "usd_per_case_measured": 0.0 if not has_key else None,
        "degraded_to_autopilot": len(degraded),
        "note": ("No OPENAI_API_KEY: every agent resolved to no-LLM, so rule 11 ran the "
                 "deterministic autopilot and cost per case is $0.00. The agentic rows "
                 "below are therefore NOT a measurement of the agent — they are the "
                 "autopilot under an agentic label. Re-run with a key for the real table."
                 if not has_key else
                 "OPENAI_API_KEY present: per-case cost is the sum of investigator (gpt-4o), "
                 "challenger/remediation (gpt-4o-mini) and narrator calls."),
    }


# =========================================================================
# driver
# =========================================================================
def suite_cases(suite: str) -> list[Label]:
    from eval.labels import load as load_label
    if suite == "heldout":
        ids = load_split()["heldout"]
        return [x for x in (load_label(c) for c in ids) if x is not None]
    if suite == "dev":
        ids = load_split()["dev"]
        return [x for x in (load_label(c) for c in ids) if x is not None]
    return synthetic_cases()


def load_results(path: Path = RESULTS_JSON) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"runs": [], "metrics": {}}


def save_results(doc: dict, path: Path = RESULTS_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")


def merge(doc: dict, suite: str, mode: str, rows: list[CaseResult]) -> dict:
    """Upsert one (suite, mode) block. Separate CLI invocations build one report."""
    key = f"{suite}:{mode}"
    doc["runs"] = [r for r in doc["runs"] if f"{r['suite']}:{r['mode']}" != key]
    doc["runs"].extend(asdict(r) for r in rows)
    m = ac_metrics(rows) if suite in ("heldout", "dev") else synthetic_metrics(rows)
    m["efficiency"] = efficiency(rows)
    m["llm_cost"] = llm_cost_note(rows)
    m["fixed_budget_note"] = FIXED_BUDGET_NOTE
    doc.setdefault("metrics", {})[key] = m
    doc["generated_at"] = time.time()
    return doc


def _print_block(key: str, m: dict) -> None:
    core = {k: v for k, v in m.items() if k not in ("efficiency", "llm_cost", "fixed_budget_note")}
    print(f"\n== {key} ==")
    print("  " + "  ".join(f"{k}={v}" for k, v in core.items()))
    e = m["efficiency"]
    print(f"  efficiency: tool_calls={e.get('mean_tool_calls')} "
          f"cost_points={e.get('mean_cost_points')} "
          f"expensive_ops={e.get('mean_expensive_ops')} "
          f"wall_clock={e.get('mean_wall_clock_s')}s")


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="eval.run_benchmark")
    ap.add_argument("--heldout", action="store_true")
    ap.add_argument("--dev", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--agentic", action="store_true", help="investigator + challenger (default)")
    ap.add_argument("--fixed-pipeline", action="store_true", help="the deterministic autopilot")
    ap.add_argument("--with-ablations", action="store_true",
                    help="also run --no-counterfactual / --no-topology / --no-twin (fixed)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=str(RESULTS_JSON))
    args = ap.parse_args(argv[1:])

    suite = "synthetic" if args.synthetic else "dev" if args.dev else "heldout"
    fixed = args.fixed_pipeline
    mode = "fixed" if fixed else "agentic"

    labels = suite_cases(suite)[: args.limit]
    if not labels:
        print(f"no cases in suite {suite!r} — nothing to benchmark", file=sys.stderr)
        return 1
    doc = load_results(Path(args.out))

    plan: list[tuple[str, str | None]] = [(mode, None)]
    if args.with_ablations:
        plan += [(f"fixed-{a}", a) for a in ABLATIONS]

    for mode_name, ablation in plan:
        rows: list[CaseResult] = []
        print(f"\n--- {suite} / {mode_name} ({len(labels)} cases) ---")
        for lab in labels:
            r = run_case(lab, suite, mode_name, fixed=fixed or ablation is not None,
                         ablation=ablation)
            rows.append(r)
            flag = f" ERROR {r.error}" if r.error else ""
            note = " [unscoreable: no single root cause]" if r.unscoreable else ""
            print(f"  {lab.case_id:<24} top1={str(r.top1):<14} truth={str(lab.truth):<12} "
                  f"rank={r.rank_of_truth} ops={r.expensive_ops} "
                  f"{r.wall_clock_s}s{flag}{note}")
        doc = merge(doc, suite, mode_name, rows)
        _print_block(f"{suite}:{mode_name}", doc["metrics"][f"{suite}:{mode_name}"])

    save_results(doc, Path(args.out))
    print(f"\nwrote {args.out}")
    try:
        from eval.report import build
        print(f"wrote {build(doc)}")
    except Exception as exc:                            # a report failure must not lose the data
        print(f"WARN: report not regenerated: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
