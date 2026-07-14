"""Render eval/results.json into results.md + a grouped-bar PNG.

The same JSON feeds `GET /benchmark`, so the page, the markdown and the chart can
never disagree — there is one source of numbers.

    py -m eval.report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESULTS_JSON = Path("eval/results.json")
RESULTS_MD = Path("eval/results.md")
RESULTS_PNG = Path("eval/results.png")

_ORDER = ["agentic", "fixed", "fixed-no-counterfactual", "fixed-no-topology", "fixed-no-twin"]


def _sort_modes(keys: list[str]) -> list[str]:
    return sorted(keys, key=lambda k: (_ORDER.index(k) if k in _ORDER else 99, k))


def _table(rows: list[list[str]], head: list[str]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def build_md(doc: dict) -> str:
    metrics = doc.get("metrics", {})
    L = ["# VERDICT — benchmark results", ""]
    if not metrics:
        return "\n".join(L + ["_No benchmark has been run yet._", "",
                              "```", "make bench", "```"])

    suites = sorted({k.split(":", 1)[0] for k in metrics})

    # ---- accuracy -------------------------------------------------------
    for suite in suites:
        modes = _sort_modes([k.split(":", 1)[1] for k in metrics if k.startswith(f"{suite}:")])
        L += [f"## {suite}", ""]
        if suite in ("heldout", "dev"):
            head = ["mode", "n", "AC@1", "AC@3", "Avg@5"]
            rows = [[m, _fmt(metrics[f"{suite}:{m}"].get("n")),
                     _fmt(metrics[f"{suite}:{m}"].get("AC@1")),
                     _fmt(metrics[f"{suite}:{m}"].get("AC@3")),
                     _fmt(metrics[f"{suite}:{m}"].get("Avg@5"))] for m in modes]
        else:
            head = ["mode", "n", "precision@1", "precision@3",
                    "red-herring false-blame", "median time-to-RCA (s)"]
            rows = [[m, _fmt(metrics[f"{suite}:{m}"].get("n")),
                     _fmt(metrics[f"{suite}:{m}"].get("precision@1")),
                     _fmt(metrics[f"{suite}:{m}"].get("precision@3")),
                     _fmt(metrics[f"{suite}:{m}"].get("red_herring_false_blame_rate")),
                     _fmt(metrics[f"{suite}:{m}"].get("median_time_to_rca_s"))] for m in modes]
        L += _table(rows, head) + [""]

    # ---- the headline: agent efficiency ---------------------------------
    L += ["## Agent efficiency — agentic vs fixed", "",
          "The fixed pipeline spends the same budget on every case "
          "(5 counterfactuals + 1 twin) whether the case needs it or not. The agent "
          "chooses its targets. The claim is equal-or-better AC@k for fewer expensive "
          "ops — so this table, not AC@k alone, is the result.", ""]
    rows = []
    for key in sorted(metrics):
        suite, mode = key.split(":", 1)
        e = metrics[key].get("efficiency", {})
        rows.append([suite, mode, _fmt(e.get("n")), _fmt(e.get("mean_tool_calls")),
                     _fmt(e.get("mean_cost_points")), _fmt(e.get("mean_expensive_ops")),
                     _fmt(e.get("mean_wall_clock_s"))])
    L += _table(rows, ["suite", "mode", "n", "mean tool calls", "mean cost points",
                       "mean expensive ops", "mean wall-clock (s)"]) + [""]

    # ---- LLM cost -------------------------------------------------------
    any_cost = next((metrics[k].get("llm_cost") for k in sorted(metrics)
                     if metrics[k].get("llm_cost")), None)
    if any_cost:
        L += ["## LLM cost per case", "",
              f"- `OPENAI_API_KEY` present: **{any_cost['openai_api_key_present']}**",
              f"- measured USD/case: **{_fmt(any_cost['usd_per_case_measured'])}**",
              f"- runs that degraded to the autopilot: **{any_cost['degraded_to_autopilot']}**",
              "", f"> {any_cost['note']}", ""]

    # ---- baselines ------------------------------------------------------
    base = doc.get("baselines")
    if base:
        L += ["## External baselines (RCAEval)", ""]
        if base.get("skipped"):
            L += [f"> Skipped: {base['reason']}", ""]
        else:
            L += _table([[b["name"], _fmt(b.get("AC@1")), _fmt(b.get("AC@3")),
                          _fmt(b.get("Avg@5"))] for b in base.get("results", [])],
                        ["baseline", "AC@1", "AC@3", "Avg@5"]) + [""]

    if RESULTS_PNG.exists():
        L += [f"![benchmark]({RESULTS_PNG.name})", ""]
    L += ["---", "", "Reproduce: `make bench` (see README §Reproduce the numbers)."]
    return "\n".join(L)


def build_png(doc: dict, out: Path = RESULTS_PNG) -> Path | None:
    """Grouped bars: agentic vs fixed vs ablations vs baselines."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:                                  # pragma: no cover
        return None

    metrics = doc.get("metrics", {})
    keys = [k for k in metrics if k.startswith("heldout:")] or list(metrics)
    if not keys:
        return None
    modes = _sort_modes([k.split(":", 1)[1] for k in keys])
    suite = keys[0].split(":", 1)[0]

    series = {"AC@1": [], "AC@3": [], "Avg@5": []} if suite in ("heldout", "dev") else \
             {"precision@1": [], "precision@3": []}
    labels = list(modes)
    for m in modes:
        for name in series:
            series[name].append(metrics[f"{suite}:{m}"].get(name) or 0.0)
    for b in (doc.get("baselines") or {}).get("results", []):
        labels.append(f"{b['name']} (baseline)")
        for name in series:
            series[name].append(b.get(name) or 0.0)

    x = range(len(labels))
    width = 0.8 / len(series)
    fig, ax = plt.subplots(figsize=(max(7, 1.6 * len(labels)), 4.2))
    for i, (name, vals) in enumerate(series.items()):
        ax.bar([p + i * width for p in x], vals, width, label=name)
    ax.set_xticks([p + width * (len(series) - 1) / 2 for p in x])
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("accuracy")
    ax.set_title(f"VERDICT — {suite}: agentic vs fixed vs ablations")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def build(doc: dict | None = None, md_path: Path = RESULTS_MD) -> Path:
    doc = doc if doc is not None else json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    build_png(doc)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(build_md(doc), encoding="utf-8")
    return md_path


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="eval.report")
    ap.add_argument("--results", default=str(RESULTS_JSON))
    args = ap.parse_args(argv[1:])
    p = Path(args.results)
    if not p.exists():
        print(f"no results at {p} — run `make bench` first", file=sys.stderr)
        return 1
    doc = json.loads(p.read_text(encoding="utf-8"))
    md = build(doc)
    print(f"wrote {md}" + (f" + {RESULTS_PNG}" if RESULTS_PNG.exists() else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
