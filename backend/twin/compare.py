"""Compare a simulated fault signature against the real observed signature.

Signature = z-normalized per-component delta (fault window − baseline) over the
shared feature vector. Verdict from the cosine similarity of the two signatures
over components instrumented in BOTH. Sim-only symptoms at components the real
system does not instrument become missing-evidence entries + recommendations.
"""

from __future__ import annotations

import math

from backend.rank.constants import TWIN_MATCH_THETA, TWIN_PARTIAL_THETA
from backend.twin.model import FEATURES


def _znorm(deltas: dict[str, list[float]]) -> dict[str, list[float]]:
    comps = sorted(deltas)
    if not comps:
        return {}
    nf = len(FEATURES)
    out = {c: [0.0] * nf for c in comps}
    for f in range(nf):
        col = [deltas[c][f] for c in comps]
        mean = sum(col) / len(col)
        std = (sum((x - mean) ** 2 for x in col) / len(col)) ** 0.5 or 1.0
        for c in comps:
            out[c][f] = (deltas[c][f] - mean) / std
    return out


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def _is_symptom(delta: list[float]) -> bool:
    return delta[0] > 0.02 or delta[2] > 0.05  # latency up or error-rate up


def compare(
    sim_deltas: dict[str, list[float]],
    real_deltas: dict[str, list[float]],
    instrumented_real: set[str],
) -> dict:
    shared = sorted(set(sim_deltas) & set(real_deltas) & set(instrumented_real))
    if shared:
        sim_sig = _znorm({c: sim_deltas[c] for c in shared})
        real_sig = _znorm({c: real_deltas[c] for c in shared})
        vs = [x for c in shared for x in sim_sig[c]]
        vr = [x for c in shared for x in real_sig[c]]
        similarity = max(0.0, cosine(vs, vr))
    else:
        similarity = 0.0

    verdict = ("match" if similarity >= TWIN_MATCH_THETA
               else "partial" if similarity >= TWIN_PARTIAL_THETA
               else "mismatch")

    missing = sorted(c for c in sim_deltas
                     if c not in instrumented_real and _is_symptom(sim_deltas[c]))
    recommendations = [f"instrument {c} to verify the simulated symptom" for c in missing]

    return {
        "similarity": round(similarity, 4),
        "verdict": verdict,
        "missing_evidence": missing,
        "recommendations": recommendations,
        "shared": shared,
    }
