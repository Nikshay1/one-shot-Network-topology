"""Evidence chat — a read-only RAG surface over a single run's ledger.

The engineer asks "what should I do about this?" or "what's the evidence for
catalogue?"; retrieval pulls the facts that answer it and the model may only
speak in citations that resolve.

Nothing in here mutates state (rule 9), decides a verdict (rule 12), or reads
ground truth (rule 4).
"""
