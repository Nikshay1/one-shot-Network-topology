"""The chat corpus: everything a finished run knows, as retrievable chunks.

Three sources, and the choice of three is the whole design:

- **ledger facts** — the natural corpus. Each `LedgerRecord` is already a short
  natural-language `statement` carrying a stable, resolvable `fact_id`, which is
  exactly a chunk plus a citation. The narrator's entire world is this same
  ledger (`query_evidence_ledger`), so chat inherits its evidence discipline for
  free.
- **ranked hypotheses** — the verdict as context, so "why catalogue?" has the
  ranking in front of it. Cited via each hypothesis's `hypothesis_scored` fact,
  never by `hypothesis_id`: only ledger ids resolve in `validate_citations`.
- **remediation rehearsals** — what was tried in the twin and what it cleared.
  `RecoveryReport.fact_id` is a real ledger id, so a fix recommendation is
  citable like any other claim.

Deliberately NOT in the corpus: the narration. It is itself LLM output, and
retrieving it would let one model cite another's prose as evidence — the
citation would resolve (it copied a real fact id) while the claim drifted. Facts
are the floor; prose is not evidence.

Rule 4: nothing here opens `data/labels` or `eval/`. The corpus is built from a
ledger, a hypothesis list and a remediation report — all of them runtime
artefacts that already passed the API's leak gate.

    py -m backend.chat.corpus --run clean_cascade-01
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

_MAX_FACTS = 100_000


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit. `fact_id` is None when the chunk is not citable."""

    chunk_id: str
    text: str
    kind: str                                   # ledger_fact | hypothesis | remediation
    fact_id: str | None = None
    component_ids: tuple[str, ...] = ()

    @property
    def citable(self) -> bool:
        return self.fact_id is not None


def from_ledger(ledger) -> list[Chunk]:
    """Every fact in the run's ledger. `query()` is the only read surface agents get."""
    out: list[Chunk] = []
    for f in ledger.query(limit=_MAX_FACTS):
        comps = tuple(f.component_ids or ())
        # The kind and components are prepended into the text on purpose: TF-IDF can
        # only match words it can see, and "counterfactual" / "catalogue" are exactly
        # the words an engineer types.
        text = f"[{f.kind}] {f.statement}"
        if comps:
            text += f" (components: {', '.join(comps)})"
        out.append(Chunk(chunk_id=f.fact_id, text=text, kind="ledger_fact",
                         fact_id=f.fact_id, component_ids=comps))
    return out


def _hypothesis_fact_id(h, by_hypothesis: dict[str, str]) -> str | None:
    return by_hypothesis.get(h.hypothesis_id)


def from_hypotheses(hypotheses, ledger=None) -> list[Chunk]:
    """The ranking, as prose. Cited via each hypothesis's `hypothesis_scored` fact."""
    by_hypothesis: dict[str, str] = {}
    if ledger is not None:
        for f in ledger.query(kind="hypothesis_scored", limit=_MAX_FACTS):
            if f.hypothesis_id and f.hypothesis_id not in by_hypothesis:
                by_hypothesis[f.hypothesis_id] = f.fact_id

    out: list[Chunk] = []
    for h in hypotheses:
        bits = [
            f"[hypothesis] Rank #{h.rank}: {h.suspect_component} — {h.statement}",
            f"Score {h.score:.3f}, evidence tier {h.tier} ({h.tier_reason}).",
            f"Suspected fault type: {h.fault_type_guess}.",
        ]
        if h.twin is not None:
            bits.append(f"Digital twin verdict: {h.twin.verdict} "
                        f"(similarity {h.twin.similarity}).")
        if h.counterfactual is not None:
            pct = getattr(h.counterfactual, "anomalies_still_explained_pct", None)
            if pct is not None:
                bits.append(f"Counterfactual: removing {h.suspect_component} still explains "
                            f"{pct}% of anomalies.")
        if h.challenger is not None:
            attacks = getattr(h.challenger, "attacks", None) or []
            if attacks:
                bits.append(f"The challenger raised {len(attacks)} upheld objection(s).")
        out.append(Chunk(
            chunk_id=h.hypothesis_id,
            text=" ".join(bits),
            kind="hypothesis",
            fact_id=_hypothesis_fact_id(h, by_hypothesis),
            component_ids=(h.suspect_component,),
        ))
    return out


def from_remediation(report) -> list[Chunk]:
    """Every rehearsal the twin actually ran. This is what answers "what do I do?"."""
    if report is None:
        return []
    out: list[Chunk] = []
    component = getattr(report, "component", None) or "unknown"
    comps = (component,) if component != "unknown" else ()

    status = getattr(report, "status", "unknown")
    caveat = getattr(report, "caveat", "") or ""
    header = (f"[remediation] Fix rehearsal status for {component}: {status}."
              f"{(' Caveat: ' + caveat) if caveat else ''}")
    out.append(Chunk(chunk_id="remediation-status", text=header, kind="remediation",
                     component_ids=comps))

    recommended = getattr(report, "recommended", None)
    for r in getattr(report, "rehearsals", []) or []:
        is_rec = recommended is not None and r.remedy == recommended.remedy
        label = "RECOMMENDED remedy" if is_rec else "alternative remedy"
        bits = [f"[remediation] {label} for {component}: {r.remedy}.",
                f"Rehearsed in the digital twin it cleared "
                f"{r.symptoms_cleared_pct:.0f}% of simulated symptoms in about "
                f"{r.sim_time_to_recover_s:.0f}s of simulated time."]
        if r.residual_symptoms:
            bits.append(f"Residual symptoms: {', '.join(r.residual_symptoms)}.")
        if r.side_effects:
            bits.append(f"Side effects observed in the twin: {', '.join(r.side_effects)}.")
        out.append(Chunk(chunk_id=f"remedy-{r.remedy}", text=" ".join(bits),
                         kind="remediation", fact_id=r.fact_id, component_ids=comps))
    return out


def build(ledger=None, hypotheses=None, remediation=None) -> list[Chunk]:
    """The whole retrievable corpus for one run."""
    chunks: list[Chunk] = []
    if ledger is not None:
        chunks += from_ledger(ledger)
    if hypotheses:
        chunks += from_hypotheses(hypotheses, ledger)
    chunks += from_remediation(remediation)
    return chunks


def pinned(hypotheses=None, remediation=None) -> list[Chunk]:
    """Chunks that go into the prompt whatever the question was.

    Lexical retrieval cannot connect "what should I do?" to a chunk whose words are
    `scale_replicas` and `cleared 87%` — they share no vocabulary. Rather than paper
    over that with a synonym list that silently rots, the verdict's headline and the
    recommended fix are simply always present: they are what an operator is asking
    about in most questions anyway, and they cost ~200 tokens.
    """
    out: list[Chunk] = []
    hyps = list(hypotheses or [])
    if hyps:
        out += from_hypotheses(hyps[:1])
    rep = remediation
    if rep is not None and getattr(rep, "status", None) in ("ok", "uncertain"):
        rec = getattr(rep, "recommended", None)
        for c in from_remediation(rep):
            if rec is not None and c.chunk_id == f"remedy-{rec.remedy}":
                out.append(c)
            elif rec is None and c.chunk_id == "remediation-status":
                out.append(c)
    return out


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="chat.corpus",
                                 description="Print the chat corpus for a run.")
    ap.add_argument("--run", required=True, help="run_id (== case_id)")
    ap.add_argument("--case", default=None, help="case_id (defaults to --run)")
    ap.add_argument("--ledger-dir", default="data/ledger")
    args = ap.parse_args(argv[1:])

    from backend.ledger.ledger import Ledger

    led = Ledger(args.run, args.case or args.run, args.ledger_dir)
    chunks = build(ledger=led)
    for c in chunks:
        cite = c.fact_id or "-"
        print(f"{cite:<24} {c.kind:<12} {c.text[:100]}")
    print(f"\n{len(chunks)} chunks ({sum(1 for c in chunks if c.citable)} citable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
