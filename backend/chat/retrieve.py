"""Lexical retrieval over the chat corpus. TF-IDF cosine, no embeddings.

Why not embeddings
------------------
Rule 6 bans deep learning, and an embedding model is one — but the honest reason
is that this corpus does not need one. A run's ledger is a few hundred short,
vocabulary-controlled statements written by our own code: the words in them are
component ids, fact kinds and metric names, which are exactly the words an
engineer types. TF-IDF over that is deterministic, free, instant, works with the
network unplugged, and adds no dependency (scikit-learn is already here for the
IsolationForest detector).

What that costs, stated plainly: pure lexical matching cannot connect a question
to a chunk it shares no words with ("is it slow?" will not match a `latency_p95`
fact). Two things absorb that — `corpus.pinned()` always puts the verdict and the
recommended fix in the prompt regardless of the query, and `_expand()` below maps
the handful of operator synonyms that actually recur. Neither pretends to be
semantic search. If a real semantic gap shows up in use, that is the point to
reach for embeddings, with a cache and a rule-6 conversation — not before.

    py -m backend.chat.retrieve --run clean_cascade-01 --q "why catalogue?"
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

from backend.chat.corpus import Chunk, build

DEFAULT_K = 8

# Operator vocabulary -> corpus vocabulary. Kept deliberately tiny: every entry is
# a word an engineer types that our own writers never emit. This is a lexical
# bridge, not a thesaurus — if it starts growing, that is evidence the corpus
# wording is wrong, or that retrieval needs to be semantic.
#
# Every target below must be a token our writers ACTUALLY emit — checked by
# tests/test_chat.py::test_every_synonym_target_exists_in_a_real_corpus, because the
# first draft of this map pointed "fix" at "remediation" while the ledger says
# "remediation_result", and the two never met: the question that motivated the whole
# feature retrieved nothing at all.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "fix": ("remedy", "remediation", "rehearsed", "recommended", "cleared"),
    "do": ("remedy", "remediation", "recommended"),
    "solve": ("remedy", "remediation", "recommended", "cleared"),
    "resolve": ("remedy", "remediation", "recommended"),
    "mitigate": ("remedy", "remediation", "recommended"),
    "remediate": ("remedy", "remediation", "rehearsed"),
    "remedy": ("remediation", "rehearsed", "recommended"),
    "slow": ("latency", "p95"),
    "slowness": ("latency", "p95"),
    "latency": ("p95", "mean"),
    "down": ("error", "rate", "anomaly"),
    "broken": ("error", "anomaly", "fault"),
    "cause": ("hypothesis", "scored", "suspect"),
    "blame": ("hypothesis", "scored", "rank"),
    # NOT "observed": it drags in any fact whose prose happens to say "observed
    # symptoms", which is how "evidence for catalogue" started returning a payment
    # fact. A synonym that matches a common English word in our own writers' prose
    # is a false-positive generator.
    "evidence": ("fact", "anomaly", "cited"),
    "proof": ("fact", "evidence", "anomaly"),
    "rule": ("absent", "topology", "path", "counterfactual", "removing"),
    "ruled": ("absent", "topology", "path", "counterfactual", "removing"),
    "exonerate": ("absent", "topology", "path", "counterfactual"),
    "innocent": ("absent", "topology", "path", "counterfactual", "removing"),
    "rollback": ("rollback_config", "config"),
    "why": ("hypothesis", "scored", "tier"),
    "confident": ("tier", "confirmed", "correlated"),
    "sure": ("tier", "confirmed", "correlated"),
}

_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_SPLIT = re.compile(r"[_.-]+")


def _stop_words() -> frozenset[str]:
    """English stop words, filtered out of BOTH the corpus and the query.

    Not cosmetic. TF-IDF gives "the" a low weight but not a zero one, so a question
    sharing nothing with the corpus but the word "the" still scores above zero — and
    `search()` promises that a zero-similarity chunk is dropped rather than padded
    into the prompt. Without this, "quantum tunnelling in the mesosphere" retrieves
    evidence, and the model is handed irrelevant facts to explain.
    """
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    return frozenset(ENGLISH_STOP_WORDS)


def _tokens(text: str) -> list[str]:
    """Lowercase words minus stop words, PLUS the parts of every compound.

    `remediation_result` and `scale_replicas` are single tokens to a word tokenizer,
    so a question asking about "remediation" or "replicas" matches neither. Emitting
    the compound *and* its parts means an engineer's plain word reaches our snake_case
    vocabulary without a stemmer or an embedding.
    """
    stop = _stop_words()
    out: list[str] = []
    for t in _WORD.findall(text.lower()):
        parts = [p for p in _SPLIT.split(t) if len(p) > 1]
        # The compound survives even if its parts are stop words ("front_end" stays
        # whole); only a bare stop word is dropped.
        if len(parts) > 1:
            out.append(t)
            out.extend(p for p in parts if p not in stop)
        elif t not in stop:
            out.append(t)
    return out


def _analyzer(text: str) -> list[str]:
    """Tokens + adjacent bigrams. A callable analyzer overrides `ngram_range`, so the
    bigrams TfidfVectorizer would have made are built here instead."""
    toks = _tokens(text)
    return toks + [f"{a} {b}" for a, b in zip(toks, toks[1:])]


def _expand(query: str) -> str:
    """Append synonym terms so an operator's words can reach the corpus's words."""
    words = _WORD.findall(query.lower())
    extra: list[str] = []
    for w in words:
        extra.extend(_SYNONYMS.get(w, ()))
    return " ".join(words + extra)


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class Index:
    """A TF-IDF index over one run's chunks. Build once per question; the corpus is
    small enough that fitting costs less than a millisecond."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = list(chunks)
        self._matrix = None
        self._vec = None
        if not self.chunks:
            return
        from sklearn.feature_extraction.text import TfidfVectorizer

        # sublinear_tf: a fact repeating a component name 5x is not 5x more relevant.
        # The custom analyzer handles compound-splitting and bigrams; see _analyzer.
        self._vec = TfidfVectorizer(analyzer=_analyzer, sublinear_tf=True)
        self._matrix = self._vec.fit_transform([c.text for c in self.chunks])

    def search(self, query: str, k: int = DEFAULT_K) -> list[Hit]:
        """Top-k chunks by cosine similarity. Zero-similarity chunks are dropped:
        padding the prompt with text that matched nothing is how a model starts
        citing something irrelevant because it was there."""
        if not self.chunks or self._vec is None or not query.strip():
            return []
        from sklearn.metrics.pairwise import linear_kernel

        try:
            q = self._vec.transform([_expand(query)])
        except ValueError:                       # pragma: no cover - empty vocabulary
            return []
        sims = linear_kernel(q, self._matrix).ravel()
        order = sims.argsort()[::-1][:k]
        return [Hit(self.chunks[i], float(sims[i])) for i in order if sims[i] > 0.0]


def search(chunks: list[Chunk], query: str, k: int = DEFAULT_K) -> list[Hit]:
    return Index(chunks).search(query, k)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="chat.retrieve",
                                 description="Retrieve chunks for a question (no LLM).")
    ap.add_argument("--run", required=True)
    ap.add_argument("--case", default=None)
    ap.add_argument("--q", required=True, help="the question")
    ap.add_argument("-k", type=int, default=DEFAULT_K)
    ap.add_argument("--ledger-dir", default="data/ledger")
    args = ap.parse_args(argv[1:])

    from backend.ledger.ledger import Ledger

    led = Ledger(args.run, args.case or args.run, args.ledger_dir)
    hits = search(build(ledger=led), args.q, args.k)
    if not hits:
        print("no chunks matched")
        return 0
    for h in hits:
        print(f"{h.score:.3f}  {(h.chunk.fact_id or '-'):<24} {h.chunk.text[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
