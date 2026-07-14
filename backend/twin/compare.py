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


def _std(col: list[float]) -> float:
    mean = sum(col) / len(col)
    return (sum((x - mean) ** 2 for x in col) / len(col)) ** 0.5


def informative_features(deltas: dict[str, list[float]], comps: list[str]) -> set[int]:
    """Feature indices that actually vary across components. A feature the system
    does not instrument (all zeros) carries no signal and must not be compared."""
    return {f for f in range(len(FEATURES))
            if comps and _std([deltas[c][f] for c in comps]) > 1e-9}


def _znorm(deltas: dict[str, list[float]], comps: list[str], feats: list[int]) -> dict[str, list[float]]:
    """z-normalize each SHARED feature across the shared components."""
    out: dict[str, list[float]] = {c: [] for c in comps}
    for f in feats:
        col = [deltas[c][f] for c in comps]
        mean = sum(col) / len(col)
        std = _std(col) or 1.0
        for c in comps:
            out[c].append((deltas[c][f] - mean) / std)
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
    # compare only over features BOTH sides actually carry signal in — a feature the
    # real system does not instrument (all zeros) would otherwise drag cosine down
    # even when sim and real agree on which component is anomalous.
    feats = sorted(informative_features(sim_deltas, shared)
                   & informative_features(real_deltas, shared))
    if shared and feats:
        sim_sig = _znorm(sim_deltas, shared, feats)
        real_sig = _znorm(real_deltas, shared, feats)
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
        "shared_features": [FEATURES[f] for f in feats],
    }
