"""Evidence chat: retrieve -> ground -> answer -> validate every citation.

The shape is classic RAG and deliberately not an agent: retrieve the chunks that
answer the question, put them in the prompt, take one completion. There is no
tool loop because there is nothing to decide — the verdict already exists, and
rule 12 says chat does not get a vote in it. That also makes the cost a single
metered call per question instead of a budget.

What it inherits rather than reinvents:

- `narrator.validate_citations` verbatim. Same `[fact-xxxx-0000]` contract, same
  strip-the-whole-claim-on-unresolved behaviour, same one retry. The narrator's
  discipline is the product; chat is a second mouth on the same evidence and has
  no business being more permissive.
- `usage.METER` — checked before the request, so `VERDICT_SPEND_CAP_USD` bounds
  chat exactly as it bounds the agents (rule 10).
- `narrate.cache` — an answer is cached under `run_id|question|prompt_version`,
  so a repeated question is free and, more importantly, an OFFLINE demo replays
  it with zero API calls (rule 13's intent). Note the transcript cache key could
  NOT have been reused: it is `hash(run_id | ledger_digest | prompt_version)` and
  has no slot for the question, so two different questions in one run would
  collide onto one cached answer.

Degradation is a demotion, never an error (rule 11's spirit): no key, OFFLINE
with a cold cache, a tripped spend cap or a raising client all fall through to
`build_deterministic`, which answers out of the retrieved facts with no model at
all. The engineer still gets cited evidence; they just get it without prose.

    py -m backend.chat.chat --run clean_cascade-01 --q "what should I do?"
    py -m backend.chat.chat --run clean_cascade-01 --q "why catalogue?" --live
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.agents import usage
from backend.chat.corpus import Chunk, build, pinned
from backend.chat.retrieve import DEFAULT_K, search
from backend.narrate import cache
from backend.narrate.narrator import validate_citations

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

MODEL = "gpt-4o-mini"
PROMPT_VERSION = "chat-v1"
MAX_QUESTION_CHARS = 1000
MAX_HISTORY_TURNS = 6
# History is conversational context ("and the alternative?" needs an antecedent), not
# evidence — the evidence is retrieved fresh every turn. So old turns are trimmed rather
# than carried whole: it bounds the prompt, and it means a long prior answer can never
# make the NEXT question fail.
MAX_HISTORY_CHARS = 800


@dataclass
class ChatAnswer:
    text: str
    mode: str                                    # llm | deterministic | cached
    citations: list[str] = field(default_factory=list)
    stripped: list[str] = field(default_factory=list)
    citations_valid: bool = True
    retrieved: list[dict] = field(default_factory=list)
    usd: float = 0.0
    attempts: int = 1

    def to_dict(self) -> dict:
        return {"answer": self.text, "mode": self.mode, "citations": self.citations,
                "stripped": self.stripped, "citations_valid": self.citations_valid,
                "retrieved": self.retrieved, "usd": round(self.usd, 6),
                "attempts": self.attempts}


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), undefined=StrictUndefined,
                       trim_blocks=True, lstrip_blocks=True)


def render_prompt(case_id: str, question: str, chunks: list[Chunk],
                  history: list[dict] | None = None, violation: str | None = None) -> str:
    return _env().get_template("chat.j2").render(
        case_id=case_id, question=question, chunks=chunks,
        history=history or [], violation=violation)


def select(chunks: list[Chunk], question: str, hypotheses=None, remediation=None,
           k: int = DEFAULT_K) -> list[Chunk]:
    """Pinned context first, then the retrieved hits, deduped by chunk_id.

    Pinned chunks lead because they are the answer to most operator questions, and a
    model reads the top of its context best."""
    out: list[Chunk] = []
    seen: set[str] = set()
    for c in pinned(hypotheses, remediation):
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            out.append(c)
    for hit in search(chunks, question, k):
        if hit.chunk.chunk_id not in seen:
            seen.add(hit.chunk.chunk_id)
            out.append(hit.chunk)
    return out


def build_deterministic(question: str, chunks: list[Chunk], remediation=None) -> str:
    """The no-model answer: the retrieved evidence, quoted, with its citations.

    Not a fallback in the apologetic sense — it is the floor the LLM decorates. Every
    line here is a fact we hold, so it validates clean by construction."""
    if not chunks:
        return ("No evidence in this run's ledger matches that question. The chat can only "
                "answer from facts the pipeline filed — try naming a component, or ask "
                "about the ranking, the counterfactual, the twin, or the recommended fix.")

    L = ["Answering from the evidence ledger directly (no model available — "
         "these are the facts that match your question):", ""]
    for c in chunks[:6]:
        cite = f" [{c.fact_id}]" if c.fact_id else ""
        L.append(f"- {c.text}{cite}")

    rec = getattr(remediation, "recommended", None) if remediation is not None else None
    if rec is not None:
        cite = f" [{rec.fact_id}]" if rec.fact_id else ""
        L += ["", f"Recommended fix: **{rec.remedy}** on "
                  f"`{getattr(remediation, 'component', 'the suspect')}` — cleared "
                  f"{rec.symptoms_cleared_pct:.0f}% of symptoms **in the digital twin**, "
                  f"not in production{cite}."]
    return "\n".join(L)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8),
       reraise=True)
def _create(client, model: str, prompt: str):
    return client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], temperature=0)


def _complete(prompt: str, model: str, meter: usage.Meter, client=None) -> tuple[str, float]:
    """One metered completion. `meter.check()` runs BEFORE the spend, not after."""
    if client is None:
        from openai import OpenAI

        client = OpenAI()
    before = meter.usd
    meter.check()
    resp = _create(client, model, prompt)
    u = getattr(resp, "usage", None)
    if u is not None:
        meter.record(model, getattr(u, "prompt_tokens", 0) or 0,
                     getattr(u, "completion_tokens", 0) or 0)
    return (resp.choices[0].message.content or ""), meter.usd - before


def _offline() -> bool:
    return os.getenv("OFFLINE", "0") == "1"


def _have_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def answer(
    question: str,
    *,
    run_id: str,
    case_id: str | None = None,
    ledger=None,
    hypotheses=None,
    remediation=None,
    client=None,
    model: str = MODEL,
    k: int = DEFAULT_K,
    history: list[dict] | None = None,
    cache_dir: str | Path = cache.DEFAULT_DIR,
    use_cache: bool = True,
) -> ChatAnswer:
    """Answer one question about one run. Never raises on an LLM failure."""
    question = (question or "").strip()[:MAX_QUESTION_CHARS]
    case_id = case_id or run_id
    history = [{"role": t.get("role", "user"),
                "content": (t.get("content") or "")[:MAX_HISTORY_CHARS]}
               for t in (history or [])[-MAX_HISTORY_TURNS:]]

    chunks = build(ledger=ledger, hypotheses=hypotheses, remediation=remediation)
    selected = select(chunks, question, hypotheses, remediation, k)
    retrieved = [{"fact_id": c.fact_id, "kind": c.kind, "text": c.text} for c in selected]

    # The cache key must carry the question; the transcript cache key does not, which
    # is exactly why this uses its own namespace rather than reusing that machinery.
    ckey = cache.key_for(run_id, question.lower(), PROMPT_VERSION, model)
    if use_cache and not history:
        hit = cache.get("chat", ckey, cache_dir)
        if hit is not None and hit.get("answer"):
            return ChatAnswer(text=hit["answer"], mode="cached",
                              citations=hit.get("citations", []),
                              stripped=hit.get("stripped", []),
                              citations_valid=hit.get("citations_valid", True),
                              retrieved=retrieved, usd=0.0)

    def _deterministic(reason_mode: str = "deterministic") -> ChatAnswer:
        text = build_deterministic(question, selected, remediation)
        clean, cites, stripped, valid = _validate(text, ledger)
        return ChatAnswer(clean, reason_mode, cites, stripped, valid, retrieved, 0.0)

    if client is None and (_offline() or not _have_key()):
        return _deterministic()

    violation, spent, text = None, 0.0, ""
    clean, cites, stripped, valid, attempts = "", [], [], False, 0
    for attempt in (1, 2):                       # generate -> validate -> ONE retry
        attempts = attempt
        prompt = render_prompt(case_id, question, selected, history, violation)
        try:
            text, usd = _complete(prompt, model, usage.METER, client)
            spent += usd
        except Exception:                        # SpendCap, API error, no key at runtime
            # `spent` survives the fall-back: attempt 1 can succeed and be BILLED before
            # the retry raises, and reporting usd=0 there would understate real money.
            # `usd` is documented as measured, not estimated — so it has to include the
            # spend that bought nothing.
            out = _deterministic()
            out.usd, out.attempts = spent, attempt
            return out
        if not text.strip():
            break
        clean, cites, stripped, valid = _validate(text, ledger)
        if valid:
            break
        violation = (f"These citations did not resolve and their claims were deleted: "
                     f"{sorted(set(stripped))}. Cite only fact ids shown in the CONTEXT.")

    if not clean.strip():                        # model produced nothing usable
        out = _deterministic()
        out.usd = spent
        out.attempts = attempts
        return out

    result = ChatAnswer(clean, "llm", cites, stripped, valid, retrieved, spent, attempts)
    if use_cache and not history and valid:
        cache.put("chat", ckey, {"answer": clean, "citations": cites, "stripped": stripped,
                                 "citations_valid": valid, "question": question,
                                 "run_id": run_id}, cache_dir)
    return result


def _validate(text: str, ledger) -> tuple[str, list[str], list[str], bool]:
    """Citations resolve against the ledger. With no ledger there is nothing to
    resolve against, so nothing may be cited — an uncheckable citation is worse
    than none."""
    if ledger is None:
        from backend.narrate.narrator import CITATION_RE

        found = CITATION_RE.findall(text)
        return text, found, [], not found
    return validate_citations(text, ledger)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="chat.chat",
                                 description="Ask a question about a finished run.")
    ap.add_argument("--run", required=True, help="run_id (== case_id)")
    ap.add_argument("--case", default=None)
    ap.add_argument("--q", required=True)
    ap.add_argument("--live", action="store_true",
                    help="use the real model (costs money; needs OPENAI_API_KEY)")
    ap.add_argument("-k", type=int, default=DEFAULT_K)
    ap.add_argument("--ledger-dir", default="data/ledger")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv[1:])

    from backend.ledger.ledger import Ledger

    if not args.live:
        os.environ["OFFLINE"] = "1"              # explicit: the CLI does not spend by default

    led = Ledger(args.run, args.case or args.run, args.ledger_dir)
    ans = answer(args.q, run_id=args.run, case_id=args.case or args.run, ledger=led,
                 k=args.k, use_cache=not args.no_cache)
    print(f"mode={ans.mode} citations_valid={ans.citations_valid} "
          f"retrieved={len(ans.retrieved)} usd={ans.usd:.6f}\n")
    print(ans.text)
    if ans.stripped:
        print(f"\n-- stripped unresolved citations: {sorted(set(ans.stripped))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
