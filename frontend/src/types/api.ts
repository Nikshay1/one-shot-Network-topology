/**
 * Mirrors contracts/api_contract.md v1.1, verified field-for-field against
 * backend/api/app.py, backend/api/sse.py and backend/agents/remediation.py.
 *
 * Shapes that have NO JSON schema file (RemediationReport, the benchmark doc,
 * transcript lines) are mirrored from the pydantic/dataclass definitions and
 * cite their source below.
 */
import type { ComponentId, EventEnvelope, EventId, TopologyRelation } from './events'
import type { AnomalyEvent } from './anomaly'
import type { FaultType, HypothesisId, RankedHypothesis, Tier, TwinVerdict } from './hypothesis'
import type { LedgerKind, LedgerRecord } from './ledger'

// ─────────────────────────────────────────────────────────────────────────────
// Errors
// ─────────────────────────────────────────────────────────────────────────────

/** 404 `{error}`; 422 `{error, detail}`. */
export interface ApiErrorBody {
  error: string
  detail?: string
}

// ─────────────────────────────────────────────────────────────────────────────
// GET /health · GET /cases · GET /case/{id}/topology
// ─────────────────────────────────────────────────────────────────────────────

export interface HealthResponse {
  status: 'ok'
  /** "1.1" */
  version: string
}

export interface CaseSummary {
  case_id: string
  /** Synthesized backend-side as case_id.replace("_", " ") — display only. */
  title: string
  n_components: number
  n_events: number
}

/**
 * NetworkX node-link graph (`edges="links"`), serialized straight off disk with
 * no enrichment (app.py:250-258).
 *
 * WHAT IS ACTUALLY ON A NODE: `id`, always. Plus `instrumented` — but ONLY on
 * the 25 synthetic scenario cases, where backend/overlay/scenarios.py:80-87
 * stamps every node. Real RE2-SS cases have NO `instrumented` key at all, and
 * the backend itself defaults the absent case to true (candidates.py:89,
 * tools.py:251). Use isInstrumented() rather than reading the field, or every
 * node of a real case renders as uninstrumented.
 *
 * `service_type` is NOT a backend field — it appears only in
 * fixtures/sample_topology.json and is written by no code path. It is optional
 * here and treated as cosmetic. Same for `graph.name`: the real graph dict is
 * `{}`.
 */
export interface TopologyNode {
  id: ComponentId
  /** Absent === true. Never read directly — see isInstrumented(). */
  instrumented?: boolean
  /** Fixture-only, cosmetic. Not produced by the backend. */
  service_type?: string
  [key: string]: unknown
}

export interface TopologyLink {
  source: ComponentId
  target: ComponentId
  /** Real topologies only ever carry "calls". Edges point caller → callee. */
  relation?: TopologyRelation
  [key: string]: unknown
}

export interface TopologyGraph {
  directed: boolean
  multigraph: boolean
  /** `{}` in real data — the fixture's {name, case_id} is invented. */
  graph: { name?: string; case_id?: string; [key: string]: unknown }
  nodes: TopologyNode[]
  links: TopologyLink[]
}

// ─────────────────────────────────────────────────────────────────────────────
// POST /case/{id}/run
// ─────────────────────────────────────────────────────────────────────────────

export interface RunRequest {
  /** >= 0. Use 10 for anything a human watches; 0 is eval-only (batch flush). */
  speed: number
  seed: number
  twin_enabled: boolean
}

/** 202 Accepted. `stream` is root-relative, e.g. "/stream/clean_cascade-01". */
export interface RunAccepted {
  run_id: string
  stream: string
}

/** 409 — a run for this case is already in flight. NOT an error: attach instead. */
export interface RunConflict {
  error: string
  run_id: string
}

/** Discriminated result of startRun() — 409 is a normal outcome, not a throw. */
export type StartRunResult =
  | { status: 'started'; run_id: string; stream: string }
  | { status: 'in_flight'; run_id: string; stream: string; error: string }

// ─────────────────────────────────────────────────────────────────────────────
// GET /run/{id}/verdict · /anomalies · /ledger · /narration
// ─────────────────────────────────────────────────────────────────────────────

export interface VerdictResponse {
  run_id: string
  case_id: string
  /** [] while the run is in flight. `done && hypotheses.length === 0` = errored. */
  hypotheses: RankedHypothesis[]
  done: boolean
}

/** All optional; omitted params are not sent. */
export interface LedgerQuery {
  component_id?: ComponentId
  kind?: LedgerKind
  hypothesis_id?: HypothesisId
}

export interface NarrationChunk {
  /** Hardcoded 0.0 by backend/narrate/narrator.py — do not use as a clock. */
  ts: number
  text: string
}

export interface NarrationResponse {
  run_id: string
  chunks: NarrationChunk[]
}

// ─────────────────────────────────────────────────────────────────────────────
// POST /run/{id}/counterfactual
// ─────────────────────────────────────────────────────────────────────────────

export interface CounterfactualRequest {
  remove_component: ComponentId
}

/**
 * NOTE: `removed` is a component_id STRING here, unlike the BOOLEAN of the same
 * name on RankedHypothesis.counterfactual. See ./hypothesis.
 */
export interface CounterfactualResponse {
  removed: ComponentId
  /** 0..100 */
  anomalies_still_explained_pct: number
  affected_hypotheses: HypothesisId[]
}

// ─────────────────────────────────────────────────────────────────────────────
// GET /run/{id}/remediation — mirrors backend/agents/remediation.py dataclasses.
// dataclasses.asdict() emits defaults, so every key is always present.
// ─────────────────────────────────────────────────────────────────────────────

export type RemediationStatus = 'ok' | 'uncertain' | 'skipped' | 'error'

/** completed | budget_exhausted | error (plain str backend-side). */
export type AgentStatus = string

export interface RecoveryReport {
  remedy: string
  /** 0..100 */
  symptoms_cleared_pct: number
  sim_time_to_recover_s: number
  residual_symptoms: string[]
  side_effects: string[]
  fact_id: string | null
}

export interface RemediationReport {
  status: RemediationStatus
  case_id: string
  hypothesis_id: HypothesisId | null
  component: ComponentId | null
  fault_type: FaultType | null
  /**
   * null unless status === "ok". On "uncertain" the backend deliberately leaves
   * this null and puts EVERY rehearsal in `alternatives` (best cleared <= 50%);
   * on "ok", `alternatives` excludes the recommended one.
   */
  recommended: RecoveryReport | null
  alternatives: RecoveryReport[]
  rehearsals: RecoveryReport[]
  /** "" when absent, never null. */
  caveat: string
  agent_status: AgentStatus | null
  transcript_path: string | null
}

/** 404 body for remediation carries an extra `done` the generic 404 does not. */
export interface RemediationNotReady {
  error: string
  done: boolean
}

// ─────────────────────────────────────────────────────────────────────────────
// GET /run/{run_id}/agent/{agent_name}/transcript — application/x-ndjson
// Mirrors backend/agents/transcript.py. Split on newlines, JSON.parse each.
// ─────────────────────────────────────────────────────────────────────────────

/**
 * The only agents registered in RunVerdict.transcripts. "narrator" writes a
 * transcript file to disk but is never registered, so it always 404s.
 */
export type AgentName = 'investigator' | 'challenger' | 'remediation'

export const AGENT_NAMES: readonly AgentName[] = [
  'investigator',
  'challenger',
  'remediation',
] as const

export interface TranscriptStepLine {
  type: 'step'
  agent: string
  ts: number
  tool: string
  args: Record<string, unknown>
  /** Truncated at 200 chars + "..." — frequently NOT parseable JSON. */
  result_summary: string
  ok: boolean
}

/** Always exactly one, always last. A transcript may have zero step lines. */
export interface TranscriptResultLine {
  type: 'result'
  agent: string
  status: 'completed' | 'budget_exhausted' | 'error'
  final_text: string | null
  ts: number
}

export type TranscriptLine = TranscriptStepLine | TranscriptResultLine

// ─────────────────────────────────────────────────────────────────────────────
// GET /benchmark — redact_benchmark() (app.py) strips truth / rank_of_truth /
// false_blame per run and adds `redacted`. Aggregate metrics survive.
// Ground truth is /eval-only by rule 4; there is deliberately no "was it right?"
// field here. Do not try to reconstruct one.
// ─────────────────────────────────────────────────────────────────────────────

export interface BenchmarkRun {
  case_id: string
  suite: string
  mode: string
  /** component_ids in rank order. */
  ranked: ComponentId[]
  tier_of_top1: Tier | null
  top1: ComponentId | null
  wall_clock_s: number
  usd: number
  tool_calls: number
  cost_points: number
  expensive_ops: number
  expensive_detail: string[]
  /** "" default; "agentic" | "autopilot". */
  pipeline_mode: string
  scenario_type: string | null
  error: string | null
  unscoreable: boolean
}

export interface BenchmarkEfficiency {
  n: number
  /** Absent when n === 0 (backend early-returns `{n: 0}`). */
  mean_tool_calls?: number
  mean_cost_points?: number
  mean_expensive_ops?: number
  mean_wall_clock_s?: number
  total_wall_clock_s?: number
}

export interface BenchmarkLlmCost {
  openai_api_key_present: boolean
  offline: boolean
  prices_usd_per_1m_tokens: Record<string, unknown>
  usd_per_case_measured: number
  usd_total_measured: number
  spend_cap_usd: number
  meter: {
    usd: number
    cap_usd: number
    calls: number
    prompt_tokens: number
    completion_tokens: number
    by_model: Record<
      string,
      { calls: number; prompt_tokens: number; completion_tokens: number; usd: number }
    >
  }
  degraded_to_autopilot: boolean
  note: string
}

/**
 * Two variants keyed `${suite}:${mode}`. Both early-return on empty rows with
 * only {n, excluded_unscoreable, errors}, so every scored field is optional.
 */
export interface BenchmarkMetrics {
  n: number
  excluded_unscoreable: number
  errors: number
  // AC variant (suites heldout / dev)
  'AC@1'?: number
  'AC@3'?: number
  'Avg@5'?: number
  // synthetic variant
  'precision@1'?: number
  'precision@3'?: number
  red_herring_false_blame_rate?: number | null
  red_herring_n?: number
  median_time_to_rca_s?: number
  // merged onto both
  efficiency?: BenchmarkEfficiency
  llm_cost?: BenchmarkLlmCost
  fixed_budget_note?: string
}

export interface BaselineResult {
  name: string
  skipped: boolean
  reason?: string
  n?: number
  'AC@1'?: number
  'AC@3'?: number
  'Avg@5'?: number
  notes?: string
}

export interface Baselines {
  generated_at: number
  results: BaselineResult[]
  skipped: boolean
  reason: string
}

/**
 * When eval/results.json is missing the backend returns a DIFFERENT object:
 * `{runs: [], metrics: {}, note}` — no `redacted`, `generated_at` or
 * `baselines`. Hence the optionals.
 */
export interface BenchmarkResponse {
  runs: BenchmarkRun[]
  metrics: Record<string, BenchmarkMetrics>
  generated_at?: number
  baselines?: Baselines
  /** ["truth", "rank_of_truth", "false_blame"] — added by redact_benchmark(). */
  redacted?: string[]
  note?: string
}

// ─────────────────────────────────────────────────────────────────────────────
// SSE payloads
// ─────────────────────────────────────────────────────────────────────────────

export interface BlastRadiusEvent {
  component_id: ComponentId
  /** === affected.length */
  radius: number
  affected: ComponentId[]
}

/** `removed` is a component_id STRING (cf. HypothesisCounterfactual.removed). */
export interface CounterfactualResultEvent {
  hypothesis_id: HypothesisId
  removed: ComponentId
  anomalies_still_explained_pct: number
}

export interface TwinStartedEvent {
  hypothesis_id: HypothesisId
  run: string
}

export interface TwinResultEvent {
  hypothesis_id: HypothesisId
  run: string
  similarity: number
  verdict: TwinVerdict
  missing_evidence: string[]
}

/**
 * The backend discards refuted attacks and hardcodes `upheld: true`, so
 * `upheld: false` never reaches the wire today. Typed as the contract's bool —
 * render the flag, don't assume it.
 */
export interface ChallengerAttackEvent {
  hypothesis_id: HypothesisId
  claim: string
  contradicting_event_id: EventId
  upheld: boolean
}

/**
 * Carries summaries only — NOT the `args`/`ok` fields of a transcript step line.
 * `tool` is null on the OFFLINE replay path.
 */
export interface AgentStepEvent {
  agent: string
  tool: string | null
  args_summary: string
  result_summary: string
}

export interface AgentDoneEvent {
  agent: string
  status: AgentStatus
  summary: string | null
}

export interface RemediationResultEvent {
  hypothesis_id: HypothesisId
  remedy: string
  symptoms_cleared_pct: number
  sim_time_to_recover_s: number
}

/** Emitted ONLY by the ranking stage (contract ordering guarantee 3). */
export interface TierChangedEvent {
  hypothesis_id: HypothesisId
  tier: Tier
  tier_reason: string
}

export interface PipelineDoneEvent {
  run_id: string
  n_hypotheses: number
}

export interface PipelineErrorEvent {
  run_id: string
  stage: 'replay' | 'pipeline'
  error: string
}

/** Discriminated union over every `event:` name in api_contract v1.1. */
export type SseMessage =
  | { event: 'event_ingested'; data: EventEnvelope }
  | { event: 'anomaly_detected'; data: AnomalyEvent }
  | { event: 'blast_radius'; data: BlastRadiusEvent }
  | { event: 'hypothesis_ranked'; data: RankedHypothesis }
  | { event: 'counterfactual_result'; data: CounterfactualResultEvent }
  | { event: 'twin_started'; data: TwinStartedEvent }
  | { event: 'twin_result'; data: TwinResultEvent }
  | { event: 'challenger_attack'; data: ChallengerAttackEvent }
  | { event: 'agent_step'; data: AgentStepEvent }
  | { event: 'agent_done'; data: AgentDoneEvent }
  | { event: 'remediation_result'; data: RemediationResultEvent }
  | { event: 'narration_chunk'; data: NarrationChunk }
  | { event: 'tier_changed'; data: TierChangedEvent }
  | { event: 'pipeline_done'; data: PipelineDoneEvent }
  | { event: 'pipeline_error'; data: PipelineErrorEvent }

export type SseEventName = SseMessage['event']

/** All 15. Order is the contract's table order, not the emission order. */
export const SSE_EVENT_NAMES: readonly SseEventName[] = [
  'event_ingested',
  'anomaly_detected',
  'blast_radius',
  'hypothesis_ranked',
  'counterfactual_result',
  'twin_started',
  'twin_result',
  'challenger_attack',
  'agent_step',
  'agent_done',
  'remediation_result',
  'narration_chunk',
  'tier_changed',
  'pipeline_done',
  'pipeline_error',
] as const

/** Terminal events. On either one the client MUST close the EventSource. */
export const TERMINAL_SSE_EVENTS = ['pipeline_done', 'pipeline_error'] as const

export type TerminalSseEvent = (typeof TERMINAL_SSE_EVENTS)[number]

const KNOWN = new Set<string>(SSE_EVENT_NAMES)

/** Forward compat: an unrecognized `event:` name is ignored, never an error. */
export function isKnownSseEvent(name: string): name is SseEventName {
  return KNOWN.has(name)
}

export function isTerminalSseEvent(name: string): name is TerminalSseEvent {
  return name === 'pipeline_done' || name === 'pipeline_error'
}

export type { AnomalyEvent, EventEnvelope, LedgerRecord, RankedHypothesis }
