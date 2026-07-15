/**
 * Challenger constants, mirrored from the backend.
 *
 * CHALLENGER_PENALTY = 0.1 per upheld attack (backend/rank/rescore.py:22). It is
 * NOT a flat subtraction you can draw as its own bar segment: rescore computes
 * `new = max(0, old - 0.1 * n)` and then rescales the whole score_breakdown by
 * `new / old`, so every component shrinks proportionally and the terms still sum
 * to the score. That is why the note below says "score ×" rather than promising
 * a −0.1 segment in the breakdown.
 */
export const CHALLENGER_PENALTY = 0.1

export const CHALLENGER_PENALTY_NOTE = `−${CHALLENGER_PENALTY} to score, breakdown rescaled`

/** backend/agents/challenger.py:32 — the pertinence window slack. */
export const CHALLENGER_TIME_SKEW_S = 5.0
