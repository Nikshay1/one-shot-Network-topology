/** Mirrors contracts/ranked_hypothesis.schema.json (frozen). */
import type { ComponentId, EventId } from './events'

/** `^hyp-[A-Za-z0-9_.]+-[0-9]{2}$` */
export type HypothesisId = string

/**
 * Assigned ONLY by backend/rank/tiers.py and delivered by the `tier_changed` /
 * `hypothesis_ranked` SSE events. Never derive or recompute a tier in the UI.
 */
export type Tier = 'CONFIRMED' | 'CORRELATED' | 'MISSING_EVIDENCE'

export const TIERS: readonly Tier[] = ['CONFIRMED', 'CORRELATED', 'MISSING_EVIDENCE'] as const

export type FaultType =
  | 'cpu'
  | 'mem'
  | 'disk'
  | 'delay'
  | 'loss'
  | 'socket'
  | 'config_push'
  | 'unknown'

export type TwinVerdict = 'match' | 'partial' | 'mismatch'

/**
 * Pre-weighted contributions that sum to `score` (enforced in backend code, not
 * expressible in JSON Schema). They are contributions, NOT normalized factors —
 * render them as a stacked breakdown of `score`, never rescaled.
 */
export interface ScoreBreakdown {
  coverage: number
  topo_consistency: number
  precedence: number
  corroboration: number
  pagerank: number
}

export const SCORE_BREAKDOWN_KEYS: readonly (keyof ScoreBreakdown)[] = [
  'coverage',
  'topo_consistency',
  'precedence',
  'corroboration',
  'pagerank',
] as const

export interface PredictedSymptom {
  component_id: ComponentId
  expectation: string
  /** null = not yet checked / unknown. Distinct from false = checked, absent. */
  observed: boolean | null
}

/**
 * NOTE: `removed` is a BOOLEAN here (did the counterfactual actually run?).
 * The `counterfactual_result` SSE payload and POST /run/{id}/counterfactual
 * response both use a field of the same name holding a component_id STRING.
 * Same name, different types — see CounterfactualResponse in ./api. Do not
 * share a type between them.
 */
export interface HypothesisCounterfactual {
  removed: boolean
  /** 0..100 */
  anomalies_still_explained_pct: number
}

export interface TwinSummary {
  run: string
  /** 0..1 */
  similarity: number
  verdict: TwinVerdict
  missing_evidence: string[]
}

export interface ChallengerAttack {
  claim: string
  contradicting_event_id: EventId
  upheld: boolean
}

export interface ChallengerSummary {
  attacks: ChallengerAttack[]
}

export interface RankedHypothesis {
  hypothesis_id: HypothesisId
  case_id: string
  /** >= 1 */
  rank: number
  suspect_component: ComponentId
  statement: string
  /** 0..1 — computed ONLY by backend/rank/scorer.py. */
  score: number
  score_breakdown: ScoreBreakdown
  tier: Tier
  tier_reason: string
  cited_evidence_ids: EventId[]
  predicted_symptoms: PredictedSymptom[]
  counterfactual: HypothesisCounterfactual
  twin: TwinSummary | null
  challenger: ChallengerSummary | null
  trigger_event_id: EventId | null
  fault_type_guess: FaultType | null
}
