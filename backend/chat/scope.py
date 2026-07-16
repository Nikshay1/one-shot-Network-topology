"""The scope gate: this assistant answers about incidents, or it declines.

Why this is code and not a prompt
---------------------------------
The project's own principle is "enforce in code, not in prose — a budget in a
prompt is a suggestion". A system prompt saying "only discuss root-cause
analysis" is precisely that suggestion, and it is one sentence of user text away
from being renegotiated.

The citation validator does not cover this either, and it is worth being precise
about why: it deletes claims whose `[fact-...]` does not RESOLVE. A sentence
about the weather carries no citation at all, so it sails through untouched. The
validator polices bad citations, not off-topic prose.

So the gate runs BEFORE the model, and an out-of-scope question is answered
deterministically for $0.00. The model is never asked the question, so it cannot
be talked into answering it.

What counts as in scope
-----------------------
A question is in scope if it touches this run's world: a component in this case's
topology, or a word from the domain vocabulary our own writers and retriever
already use. The lexicon is not a new invention — it is assembled from
`retrieve._SYNONYMS` (the operator words we already map), the ledger's own fact
kinds, the tier ladder, and the fault types. Adding a word here is a claim that
the system knows something about it.

Deliberately NOT a classifier: an LLM call to decide whether to make an LLM call
costs money, adds latency, and is itself promptable.

    py -m backend.chat.scope --q "what is the best food in hyderabad?"
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from backend.chat.retrieve import _SYNONYMS, _tokens

# The words the system genuinely knows something about. Assembled from vocabulary
# that already exists elsewhere in the codebase rather than invented here, so it
# cannot drift away from what the retriever and the ledger actually say.
_LEDGER_KINDS = {
    "anomaly", "anomalies", "observed", "absent", "topology", "path", "temporal",
    "order", "config", "change", "counterfactual", "twin", "coverage", "gap",
    "hypothesis", "hypotheses", "scored", "investigation", "note", "remediation",
    "result", "ledger", "fact", "facts", "evidence", "citation", "cited",
}
_TIERS = {"tier", "tiers", "confirmed", "correlated", "missing"}
_FAULTS = {"cpu", "mem", "memory", "disk", "delay", "loss", "socket", "config_push",
           "fault", "faults", "error", "errors", "latency", "throughput",
           "utilization", "utilisation", "p95", "p90", "z", "zscore", "spike"}
_RCA = {
    "root", "cause", "causes", "blame", "suspect", "suspects", "culprit",
    # How a human actually opens: "what happened?", "what went wrong?". These are the
    # incident question, and the gate refusing them was the gate being clever rather
    # than useful. They are content words, so they cost nothing on "what's for lunch?".
    "happened", "happen", "happening", "wrong", "issue", "issues", "problem",
    "problems", "summary", "summarise", "summarize", "overview", "story",
    "incident", "outage", "broke", "broken", "break", "failing", "failed", "failure",
    "symptom", "symptoms", "cascade", "upstream", "downstream", "component",
    "components", "service", "services", "node", "graph", "rank", "ranked",
    "ranking", "score", "scores", "verdict", "run", "case", "detected", "detect",
    "detection", "investigate", "investigation", "agent", "agentic", "autopilot",
    "ensemble", "fix", "fixes", "remedy", "remedies", "rehearsal", "rehearsed",
    # "recommended" (our word: "RECOMMENDED remedy"), but NOT the bare verb
    # "recommend" — it let "recommend a good restaurant nearby" through, and no
    # lexicon can tell that apart from "what do you recommend?". The narrower word
    # costs a redirect on one phrasing; the broader one costs the whole gate.
    "recommended", "alternative", "alternatives", "slow", "slowness", "stuck",
    "option", "options", "mitigate", "resolve", "restart", "rollback", "scale",
    "replicas", "throttle", "reroute", "explain", "explanation", "ruled", "rule",
    "innocent", "redundant", "confidence", "certain", "sure", "simulation",
    "simulated", "sim", "instrumented", "uninstrumented", "telemetry", "metric",
    "metrics", "log", "logs", "alert", "alerts", "window", "timeline",
}
# NOT in the lexicon, deliberately: what / why / which / how / when. They are English
# stop words, so `_tokens` strips them before the gate ever sees them — listing them
# would be dead weight that reads like coverage. They also carry no domain signal:
# "what" is in "what broke?" and in "what's for lunch?" alike. The cost is that a bare
# "why?" follow-up is declined and redirected rather than answered; the alternative
# was letting every "what ..." question through, which is the whole thing this gate
# exists to prevent.

# Component ids are split into parts by `_tokens` ("front-end" -> front-end, front,
# end), and a 3-letter part like "end" is an ordinary English word — without this
# floor, "when does the world end?" names a component. The full id always matches.
_MIN_COMPONENT_PART = 4

# The retriever's synonym TARGETS are domain vocabulary — "remedy", "counterfactual",
# "p95" — so they belong here. Its KEYS deliberately do not: they are the operator's
# side of the mapping ("do", "why", "down", "sure"), which is ordinary English. Pulling
# them in let "do you like pizza?" name a domain word, and put three entries in the
# lexicon that tokenisation strips anyway.
DOMAIN_LEXICON: frozenset[str] = frozenset(
    _LEDGER_KINDS | _TIERS | _FAULTS | _RCA
    | {w for targets in _SYNONYMS.values() for w in targets}
)

# What we say instead of answering. Not a scolding: it names what the assistant
# can actually do, because a refusal that does not redirect is just a dead end.
REFUSAL = (
    "I can only answer questions about this incident — the evidence in this run's "
    "ledger, the ranked suspects, and the fixes that were rehearsed. I don't have "
    "anything to say about that.\n"
    "\n"
    "Things I can answer for this case:\n"
    "- What should I do to fix this?\n"
    "- What is the evidence for the top suspect?\n"
    "- What did you rule out, and why?\n"
    "- Why is this only CORRELATED and not CONFIRMED?"
)

GREETING_TOKENS = frozenset({
    "hi", "hello", "hey", "yo", "sup", "hiya", "greetings", "thanks", "thank",
    "thankyou", "cheers", "ok", "okay", "cool", "nice", "bye", "goodbye", "please",
})

GREETING_REPLY = (
    "Hello. I'm the evidence assistant for this incident run — ask me about what "
    "was detected, why a component is ranked where it is, or what to do about it, "
    "and I'll answer from this run's evidence ledger with citations you can check.\n"
    "\n"
    "Try:\n"
    "- What should I do to fix this?\n"
    "- What is the evidence for the top suspect?\n"
    "- What did you rule out, and why?"
)


@dataclass(frozen=True)
class ScopeResult:
    in_scope: bool
    reply: str | None = None     # the deterministic answer, when we decline
    reason: str = ""


def check(question: str, components: set[str] | None = None) -> ScopeResult:
    """Decide whether to spend a model call on this question.

    `components` are this case's topology nodes, so "why is carts slow?" is in scope
    on a case that has a `carts` and out of scope on one that does not — the gate is
    per-case, not global.
    """
    q = (question or "").strip()
    if not q:
        return ScopeResult(False, REFUSAL, "empty question")

    toks = set(_tokens(q.lower()))
    if not toks:
        # Every word was a stop word — "why?", "what now?", "what should I do?". A
        # question with no content words CANNOT be about the weather, because
        # "weather" is a content word and would be sitting right there. So an empty
        # token set is not an unknown topic; it is a follow-up leaning on the
        # conversation, and the only sensible reading of it is "about this incident".
        return ScopeResult(True, None, "contextual follow-up: no content words to be off-topic")

    # component ids are the strongest possible signal, and they are per-case.
    comp_words: set[str] = set()
    for c in components or set():
        for part in _tokens(c.lower()):
            if len(part) >= _MIN_COMPONENT_PART or part == c.lower():
                comp_words.add(part)
    hit_components = toks & comp_words
    if hit_components:
        return ScopeResult(True, None, f"names a component in this case: {sorted(hit_components)}")

    hit_domain = toks & DOMAIN_LEXICON
    if hit_domain:
        return ScopeResult(True, None, f"uses domain vocabulary: {sorted(hit_domain)[:4]}")

    # Nothing about this question touches the incident. Greetings get a warmer door
    # than "what's the weather" — both are declined, but one of them is a person
    # saying hello, and answering that with a refusal notice is just rude.
    if toks & GREETING_TOKENS:
        return ScopeResult(False, GREETING_REPLY, "greeting / small talk")

    return ScopeResult(False, REFUSAL, "no overlap with this case or the domain")


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="chat.scope",
                                 description="Would this question get a model call?")
    ap.add_argument("--q", required=True)
    ap.add_argument("--components", default="", help="comma-separated component ids")
    args = ap.parse_args(argv[1:])

    comps = {c.strip() for c in args.components.split(",") if c.strip()}
    r = check(args.q, comps)
    print(f"in_scope={r.in_scope}  ({r.reason})")
    if r.reply:
        print()
        print(r.reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
