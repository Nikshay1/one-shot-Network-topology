"""PDF export: narration markdown + remediation table + agent investigation summary.

The audit-trail document — the auto-drafted postmortem. Rendered with matplotlib
(already a dependency) so no extra PDF toolchain is needed.

    py -m backend.api.pdf_report --case catalogue_cpu-1 --out data/reports
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_OUT = Path("data/reports")
_LINES_PER_PAGE = 46
_WRAP = 96


@dataclass
class InvestigationSummary:
    mode: str = "autopilot"
    investigator_status: str | None = None
    challenger_status: str | None = None
    tool_calls: int = 0
    cost_points_spent: int = 0
    expensive_checks: list[str] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    challenger_attacks: int = 0


def remediation_table(remediation) -> list[str]:
    if remediation is None:
        return ["(no remediation rehearsed)"]
    head = f"{'remedy':<20}{'cleared %':>11}{'recover s':>11}  side effects"
    rows = [head, "-" * len(head)]
    for r in getattr(remediation, "rehearsals", []) or []:
        side = ", ".join(r.side_effects) if r.side_effects else "none"
        rows.append(f"{r.remedy:<20}{r.symptoms_cleared_pct:>11.0f}"
                    f"{r.sim_time_to_recover_s:>11.0f}  {side}")
    if len(rows) == 2:
        rows.append("(none rehearsed)")
    rows.append("")
    rec = getattr(remediation, "recommended", None)
    rows.append(f"RECOMMENDED: {rec.remedy}" if rec
                else f"RECOMMENDED: none — {getattr(remediation, 'caveat', 'n/a')}")
    if getattr(remediation, "caveat", "") and rec:
        rows.append(f"CAVEAT: {remediation.caveat}")
    return rows


def summary_block(s: InvestigationSummary) -> list[str]:
    out = [
        f"mode                : {s.mode}",
        f"investigator        : {s.investigator_status}",
        f"challenger          : {s.challenger_status} ({s.challenger_attacks} upheld attacks)",
        f"agent tool calls    : {s.tool_calls}",
        f"cost points spent   : {s.cost_points_spent}",
        f"expensive checks    : {', '.join(s.expensive_checks) or 'none'}",
        "",
        "key findings:",
    ]
    out += [f"  - {k}" for k in (s.key_findings or ["(none filed)"])]
    return out


def build_pdf(
    out_path: str | Path,
    *,
    case_id: str,
    narration_text: str,
    remediation=None,
    summary: InvestigationSummary | None = None,
) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    summary = summary or InvestigationSummary()
    lines = [f"VERDICT incident report — {case_id}", "=" * _WRAP, ""]
    for raw in narration_text.splitlines():
        lines.extend(textwrap.wrap(raw, _WRAP) or [""])
    lines += ["", "=" * _WRAP, "REMEDIATION REHEARSALS (simulated before production)", ""]
    lines += remediation_table(remediation)
    lines += ["", "=" * _WRAP, "AGENT INVESTIGATION SUMMARY (audit trail)", ""]
    lines += summary_block(summary)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pages = [lines[i:i + _LINES_PER_PAGE] for i in range(0, len(lines), _LINES_PER_PAGE)] or [[""]]
    with PdfPages(out_path) as pdf:
        for page in pages:
            fig = plt.figure(figsize=(8.27, 11.69))          # A4
            fig.text(0.06, 0.96, "\n".join(page), family="monospace", fontsize=7.5,
                     va="top", ha="left")
            pdf.savefig(fig)
            plt.close(fig)
    return out_path


def _main(argv: list[str]) -> int:
    from backend.pipeline import run as pipeline_run

    ap = argparse.ArgumentParser(prog="api.pdf_report")
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", default=str(_DEFAULT_OUT))
    ap.add_argument("--fixed-pipeline", action="store_true")
    args = ap.parse_args(argv[1:])

    v = pipeline_run(args.case, fixed_pipeline=args.fixed_pipeline)
    s = InvestigationSummary(
        mode=v.mode, investigator_status=v.investigator_status,
        challenger_status=v.challenger_status, tool_calls=v.tool_calls,
        cost_points_spent=v.cost_points_spent, expensive_checks=v.expensive_checks,
        challenger_attacks=len(v.challenger_attacks or []),
        key_findings=[f.statement for f in v.ledger.query(kind="investigation_note", limit=8)],
    )
    path = build_pdf(Path(args.out) / f"{args.case}.pdf", case_id=args.case,
                     narration_text=v.narration.text if v.narration else "(no narration)",
                     remediation=v.remediation, summary=s)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
