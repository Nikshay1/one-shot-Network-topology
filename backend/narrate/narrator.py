"""The Narrator — the incident report, structurally unable to outrun its evidence.

Runs LAST (after remediation) so it can cite `remediation_result` facts. Its only
tool is `query_evidence_ledger`, so the ledger is its entire world.

Post-validation: every `[fact-...]` citation is extracted and checked against the
ledger. Unresolved -> the claim is STRIPPED, `citations_valid=False`, and ONE retry
is issued with the violation appended to the prompt. If no LLM is configured the
deterministic builder runs, which cites only ids it read out of the ledger and
therefore always validates clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from backend.agents.harness import LLM
from backend.agents.tools import ToolContext
from backend.models import RankedHypothesis
from backend.narrate import llm as narrate_llm

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
CITATION_RE = re.compile(r"\[(fact-[A-Za-z0-9_.]+-[0-9]{4})\]")
_EXONERATING_KINDS = ("anomaly_absent", "topology_no_path", "counterfactual_result")

SECTIONS = ["## Timeline", "## Ranked hypotheses", "## What we ruled out and why",
            "## Recommended fix", "## Runbook",
            "## Missing evidence & instrumentation recommendations"]


@dataclass
class Narration:
    text: str
    mode: str                                    # deterministic | llm
    citations: list[str] = field(default_factory=list)
    stripped: list[str] = field(default_factory=list)
    citations_valid: bool = True
    attempts: int = 1


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), undefined=StrictUndefined,
                       trim_blocks=True, lstrip_blocks=True)


def render_prompt(case_id: str, hypotheses, remediation, violation: str | None = None) -> str:
    return _env().get_template("narrator.j2").render(
        case_id=case_id, hypotheses=hypotheses, remediation=remediation,
        budget={"max_calls": narrate_llm.MAX_LEDGER_CALLS}, violation=violation,
    )


def validate_citations(text: str, ledger) -> tuple[str, list[str], list[str], bool]:
    """Strip any claim whose citation does not resolve; report validity."""
    valid = {f.fact_id for f in ledger.query(limit=100_000)}
    kept, stripped = [], []
    for line in text.splitlines():
        bad = [c for c in CITATION_RE.findall(line) if c not in valid]
        if bad:
            stripped.extend(bad)
            continue                             # the whole claim goes
        kept.append(line)
    clean = "\n".join(kept)
    return clean, CITATION_RE.findall(clean), stripped, not stripped


def _facts(ledger, kind: str, limit: int = 200):
    return ledger.query(kind=kind, limit=limit)


def build_deterministic(case_id: str, hypotheses: list[RankedHypothesis],
                        remediation, ledger) -> str:
    L = [f"# Incident report — {case_id}", ""]

    L.append("## Timeline")
    timeline = []
    for kind in ("config_change_observed", "anomaly_observed", "temporal_order"):
        timeline.extend(_facts(ledger, kind))
    timeline.sort(key=lambda f: f.ts_range.start)
    L += [f"- {f.statement} [{f.fact_id}]" for f in timeline[:12]] or \
         ["- No timeline facts were recorded for this run."]
    L.append("")

    L.append("## Ranked hypotheses")
    if not hypotheses:
        L.append("- No hypotheses were produced.")
    for h in hypotheses[:5]:
        L.append(f"- **#{h.rank} {h.suspect_component}** — score {h.score:.3f}, "
                 f"tier **{h.tier}**, fault guess `{h.fault_type_guess}`.")
        L.append(f"  - Tier rationale: {h.tier_reason}")
        if h.twin is not None:
            L.append(f"  - Twin: verdict={h.twin.verdict}, similarity={h.twin.similarity}.")
    L.append("")

    L.append("## What we ruled out and why")
    negatives = []
    for kind in _EXONERATING_KINDS:
        negatives.extend(_facts(ledger, kind))
    L += [f"- {f.statement} [{f.fact_id}]" for f in negatives[:12]] or \
         ["- Nothing was explicitly ruled out in this run."]
    L.append("")

    L.append("## Recommended fix")
    if remediation is None or remediation.status == "skipped":
        L.append(f"- No fix rehearsed: {getattr(remediation, 'caveat', 'verdict not strong enough')}.")
    elif remediation.status == "uncertain":
        L.append(f"- **{remediation.caveat}**")
        for r in remediation.rehearsals:
            cite = f" [{r.fact_id}]" if r.fact_id else ""
            L.append(f"  - {r.remedy}: cleared {r.symptoms_cleared_pct:.0f}%{cite}")
    elif remediation.status == "ok" and remediation.recommended:
        r = remediation.recommended
        cite = f" [{r.fact_id}]" if r.fact_id else ""
        L.append(f"- **Recommended: {r.remedy}** on `{remediation.component}` — clears "
                 f"{r.symptoms_cleared_pct:.0f}% of simulated symptoms in "
                 f"~{r.sim_time_to_recover_s:.0f}s sim-time{cite}")
        if r.side_effects:
            L.append(f"  - Side effects in the twin: {', '.join(r.side_effects)}")
        for alt in remediation.alternatives:
            cite = f" [{alt.fact_id}]" if alt.fact_id else ""
            L.append(f"  - Alternative {alt.remedy}: cleared {alt.symptoms_cleared_pct:.0f}%{cite}")
        if remediation.caveat:
            L.append(f"  - Caveat: {remediation.caveat}")
    else:
        L.append(f"- {getattr(remediation, 'caveat', 'no remedy could be rehearsed')}.")
    L.append("")

    L.append("## Runbook")
    top = hypotheses[0] if hypotheses else None
    if top is None:
        L.append("1. No verdict was produced — escalate to a human.")
    else:
        L.append(f"1. Inspect `{top.suspect_component}` ({top.fault_type_guess}) — the "
                 f"leading suspect at tier {top.tier}.")
        if remediation is not None and remediation.status == "ok" and remediation.recommended:
            L.append(f"2. Apply the rehearsed remedy **{remediation.recommended.remedy}**.")
            L.append(f"3. Verify symptoms clear within "
                     f"~{remediation.recommended.sim_time_to_recover_s:.0f}s; roll back if not.")
        else:
            L.append("2. No fix is recommended with confidence — human review required.")
        L.append("4. Record the outcome against this report's fact ids.")
    L.append("")

    L.append("## Missing evidence & instrumentation recommendations")
    gaps, seen = _facts(ledger, "coverage_gap"), set()
    rows = []
    for f in gaps:
        if f.statement in seen:
            continue
        seen.add(f.statement)
        rows.append(f"- {f.statement} [{f.fact_id}]")
    L += rows[:10] or ["- No instrumentation gaps blocked this investigation."]
    return "\n".join(L)


def narrate(
    ctx: ToolContext,
    hypotheses: list[RankedHypothesis],
    remediation=None,
    *,
    run_id: str,
    llm: LLM | None = None,
    emit: Callable[[str, dict], None] | None = None,
    transcripts_dir: str | Path = "data/transcripts",
) -> Narration:
    from backend.agents.harness import resolve_llm
    from backend.agents import transcript as tr

    cached_steps, cached_status, cached_final = tr.read(
        tr.path_for(transcripts_dir, "narrator",
                    tr.cache_key(run_id, tr.ledger_digest(ctx.ledger),
                                 f"{narrate_llm.PROMPT_VERSION}-a1")))
    probe, _ = resolve_llm("narrator", narrate_llm.MODEL, llm, cached_steps, cached_final,
                           cached_status)
    if probe is None:                            # no model available -> deterministic report
        text = build_deterministic(ctx.case_id, hypotheses, remediation, ctx.ledger)
        clean, cites, stripped, valid = validate_citations(text, ctx.ledger)
        _emit(clean, emit)
        return Narration(clean, "deterministic", cites, stripped, valid, attempts=1)

    violation, attempts = None, 0
    clean, cites, stripped, valid = "", [], [], False
    for attempt in (1, 2):                       # generate -> validate -> ONE retry
        attempts = attempt
        prompt = render_prompt(ctx.case_id, hypotheses, remediation, violation)
        gen = narrate_llm.generate(ctx, prompt, run_id=run_id, llm=llm, emit=emit,
                                   transcripts_dir=transcripts_dir, attempt=attempt)
        if not gen.text:
            break
        clean, cites, stripped, valid = validate_citations(gen.text, ctx.ledger)
        if valid:
            break
        violation = (f"These citations did not resolve and their claims were deleted: "
                     f"{sorted(set(stripped))}. Cite only fact ids returned by "
                     f"query_evidence_ledger.")

    if not clean:                                # model produced nothing usable
        text = build_deterministic(ctx.case_id, hypotheses, remediation, ctx.ledger)
        clean, cites, stripped, valid = validate_citations(text, ctx.ledger)
        _emit(clean, emit)
        return Narration(clean, "deterministic", cites, stripped, valid, attempts=attempts)

    _emit(clean, emit)
    return Narration(clean, "llm", cites, stripped, valid, attempts=attempts)


def _emit(text: str, emit) -> None:
    if emit:
        for chunk in text.split("\n\n"):
            emit("narration_chunk", {"ts": 0.0, "text": chunk})
