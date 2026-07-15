/**
 * Run state, assembled from the SSE stream. Pure TypeScript — this module
 * imports no React, so `applySseMessage` is directly unit-testable.
 *
 * Two rules from CLAUDE.md shape every reducer here:
 *  - Tiers, scores and ranks are NEVER derived client-side. `hypothesis_ranked`
 *    and `tier_changed` are the only things that write them.
 *  - `hypothesis_ranked` is a FULL-OBJECT UPSERT keyed by hypothesis_id:
 *    replace, never merge. A re-emit is the newer truth in its entirety.
 *
 * Idempotence: any subscriber is replayed the run from index 0 on every
 * (re)connect, so nothing may be appended blindly. Everything that has an id is
 * keyed by it; the append-only logs (agent steps, tier changes) rely on
 * `reset()` being called when the stream (re)opens.
 */
import { createStore } from 'zustand/vanilla'
import type { ComponentId, EventEnvelope, EventId } from '@/types/events'
import type { AnomalyEvent, AnomalyId } from '@/types/anomaly'
import type { HypothesisId, RankedHypothesis } from '@/types/hypothesis'
import type {
  AgentDoneEvent,
  AgentStepEvent,
  BlastRadiusEvent,
  ChallengerAttackEvent,
  CounterfactualResultEvent,
  NarrationChunk,
  PipelineDoneEvent,
  PipelineErrorEvent,
  RemediationResultEvent,
  SseMessage,
  TierChangedEvent,
  TwinResultEvent,
  TwinStartedEvent,
} from '@/types/api'
import type { StreamStatus } from '@/api/stream'

/** The live event feed is a tail view: a real run can ingest ~186k events. */
export const EVENT_BUFFER_LIMIT = 500

/** Same cap for the merged arrival-ordered feed. */
export const FEED_BUFFER_LIMIT = 500

/**
 * One row of the live incident feed. Events and anomalies are stored separately
 * (different shapes, different ids, different consumers) and neither carries an
 * arrival index, so the merged feed cannot be derived from them after the fact —
 * insertion order into this Map IS the arrival order. It holds references to the
 * same objects, not copies.
 */
export type FeedItem =
  | { kind: 'event'; event: EventEnvelope }
  | { kind: 'anomaly'; anomaly: AnomalyEvent }

/**
 * Metric history for the sparklines.
 *
 * There is no metric endpoint — the SSE `event_ingested` stream is the ONLY
 * source of metric values, so the series has to be accumulated as it arrives.
 * Two consequences the UI must live with:
 *  - it is live-only: reload mid-run and the history is gone;
 *  - the server caps the replay (DEFAULT_MAX_STREAM_EVENTS) and does not expose
 *    `truncated` on any endpoint, so a series can be silently incomplete.
 *
 * The 500-event ring buffer is a tail view for the feed and is useless for a
 * chart, hence a separate per-series buffer.
 */
export interface MetricPoint {
  ts: number
  value: number
}

export interface MetricSeries {
  component_id: ComponentId
  name: string
  unit?: string
  /** Sorted by ts on read, not on write — see pushMetricPoint. */
  points: MetricPoint[]
}

/** Per (component, metric) cap. Decimated on overflow to keep the whole window. */
export const METRIC_SERIES_CAP = 2000

export function metricKey(component: ComponentId, name: string): string {
  return `${component}|${name}`
}

/** One bucket of the timeline's density ribbon, in seconds. */
export const DENSITY_BUCKET_S = 1

export function feedKey(item: FeedItem): string {
  return item.kind === 'event' ? `event:${item.event.event_id}` : `anomaly:${item.anomaly.anomaly_id}`
}

/** Pipeline status, distinct from transport status (`connection`). */
export type RunStatus = 'idle' | 'connecting' | 'streaming' | 'done' | 'error'

export interface RunState {
  runId: string | null
  caseId: string | null
  status: RunStatus
  connection: StreamStatus

  /**
   * Ring buffer, keyed by event_id and capped at EVENT_BUFFER_LIMIT. A Map
   * gives dedupe, insertion order and O(1) eviction in one structure.
   */
  events: Map<EventId, EventEnvelope>
  /** Total ever ingested, uncapped — the buffer only holds the tail. */
  eventsSeen: number

  /** Events and anomalies interleaved in arrival order, capped, deduped by id. */
  feed: Map<string, FeedItem>

  /**
   * Events referenced as evidence, held forever.
   *
   * There is NO endpoint to fetch an event by id — the 14 routes have no
   * /event/{id} — so the SSE stream is the only source, and the 500-event ring
   * buffer evicts. Anything cited by an anomaly or a hypothesis is copied here
   * as it is cited, which works because the server backfills dropped-but-cited
   * events immediately before the anomalies (app.py:190-198) — i.e. they are
   * still in the buffer at the moment anything cites them. Bounded by the number
   * of cited ids, which is small.
   */
  pinnedEvents: Map<EventId, EventEnvelope>

  /** Keyed by metricKey(component, name). */
  metricSeries: Map<string, MetricSeries>
  /** Event counts per DENSITY_BUCKET_S bucket, for the timeline ribbon. */
  density: Map<number, number>
  /** Config-change events, for the timeline's clickable diamonds. */
  configChanges: Map<EventId, EventEnvelope>
  /** Earliest/latest event ts seen. The API exposes no case window. */
  tsMin: number | null
  tsMax: number | null

  anomalies: Map<AnomalyId, AnomalyEvent>
  /** The entire verdict state. Survives replay and reconnect for free. */
  hypotheses: Map<HypothesisId, RankedHypothesis>
  blastRadius: Map<ComponentId, BlastRadiusEvent>
  counterfactuals: Map<HypothesisId, CounterfactualResultEvent>
  twinStarted: Map<HypothesisId, TwinStartedEvent>
  twinResults: Map<HypothesisId, TwinResultEvent>
  challengerAttacks: Map<HypothesisId, ChallengerAttackEvent[]>

  /** Grouped by agent name: investigator | challenger | remediation. */
  agentSteps: Record<string, AgentStepEvent[]>
  agentDone: Record<string, AgentDoneEvent>

  remediation: Map<HypothesisId, RemediationResultEvent>

  narrationChunks: NarrationChunk[]
  /** Chunk deltas concatenated into one markdown string. */
  narration: string

  /** Audit log of tier transitions, in emission order. */
  tierChanges: TierChangedEvent[]

  doneInfo: PipelineDoneEvent | null
  errorInfo: PipelineErrorEvent | null

  /** Count of `event:` names this build doesn't know. Forward compat, not a bug. */
  unknownEvents: number
}

export function createInitialRunState(): RunState {
  return {
    runId: null,
    caseId: null,
    status: 'idle',
    connection: 'closed',
    events: new Map(),
    eventsSeen: 0,
    feed: new Map(),
    pinnedEvents: new Map(),
    metricSeries: new Map(),
    density: new Map(),
    configChanges: new Map(),
    tsMin: null,
    tsMax: null,
    anomalies: new Map(),
    hypotheses: new Map(),
    blastRadius: new Map(),
    counterfactuals: new Map(),
    twinStarted: new Map(),
    twinResults: new Map(),
    challengerAttacks: new Map(),
    agentSteps: {},
    agentDone: {},
    remediation: new Map(),
    narrationChunks: [],
    narration: '',
    tierChanges: [],
    doneInfo: null,
    errorInfo: null,
    unknownEvents: 0,
  }
}

/**
 * Append to a capped, id-keyed ring buffer. delete-then-set keeps the newest
 * occurrence at the tail on a re-emit, so a replayed event moves rather than
 * duplicates.
 */
function pushCapped<K, V>(buffer: Map<K, V>, key: K, value: V, limit: number): Map<K, V> {
  const next = new Map(buffer)
  next.delete(key)
  next.set(key, value)
  while (next.size > limit) {
    const oldest = next.keys().next()
    if (oldest.done) break
    next.delete(oldest.value)
  }
  return next
}

/** Any non-terminal traffic means the pipeline is live. */
function streaming(status: RunStatus): RunStatus {
  return status === 'done' || status === 'error' ? status : 'streaming'
}

/**
 * Copy any cited event out of the evicting ring buffer into the permanent
 * pinned map. Returns the original map when nothing was found, so the common
 * case costs no allocation.
 */
function pinCited(state: RunState, ids: readonly EventId[]): Map<EventId, EventEnvelope> {
  let next: Map<EventId, EventEnvelope> | null = null
  for (const id of ids) {
    if (state.pinnedEvents.has(id)) continue
    const ev = state.events.get(id)
    if (!ev) continue
    next ??= new Map(state.pinnedEvents)
    next.set(id, ev)
  }
  return next ?? state.pinnedEvents
}

/**
 * Append a metric sample, decimating rather than dropping when the cap is hit:
 * keeping every other point halves the resolution but preserves the whole time
 * window, which is what a sparkline is for. Dropping the oldest would silently
 * turn a 30-minute chart into a 30-second one.
 */
function pushMetricPoint(
  series: Map<string, MetricSeries>,
  ev: EventEnvelope,
  payload: { name: string; value: number; unit?: string },
): Map<string, MetricSeries> {
  const key = metricKey(ev.component_id, payload.name)
  const prior = series.get(key)
  let points = [...(prior?.points ?? []), { ts: ev.ts, value: payload.value }]
  if (points.length > METRIC_SERIES_CAP) {
    points = points.filter((_, i) => i % 2 === 0)
  }
  const next = new Map(series)
  next.set(key, {
    component_id: ev.component_id,
    name: payload.name,
    ...(payload.unit === undefined ? {} : { unit: payload.unit }),
    points,
  })
  return next
}

/**
 * The single reducer. Pure: returns a new state, never mutates `state`.
 * An unrecognized event is counted and otherwise ignored — never an error.
 */
export function applySseMessage(state: RunState, msg: SseMessage): RunState {
  switch (msg.event) {
    case 'event_ingested': {
      const ev = msg.data
      const bucket = Math.floor(ev.ts / DENSITY_BUCKET_S)
      const density = new Map(state.density)
      density.set(bucket, (density.get(bucket) ?? 0) + 1)

      return {
        ...state,
        status: streaming(state.status),
        events: pushCapped(state.events, ev.event_id, ev, EVENT_BUFFER_LIMIT),
        feed: pushCapped(
          state.feed,
          `event:${ev.event_id}`,
          { kind: 'event', event: ev },
          FEED_BUFFER_LIMIT,
        ),
        metricSeries:
          ev.payload.kind === 'metric'
            ? pushMetricPoint(state.metricSeries, ev, ev.payload)
            : state.metricSeries,
        configChanges:
          ev.payload.kind === 'config'
            ? new Map(state.configChanges).set(ev.event_id, ev)
            : state.configChanges,
        density,
        // Cited-event backfill is flushed AFTER the replay (app.py:190-198), so
        // arrivals are not ts-sorted — min/max, never first/last.
        tsMin: state.tsMin === null ? ev.ts : Math.min(state.tsMin, ev.ts),
        tsMax: state.tsMax === null ? ev.ts : Math.max(state.tsMax, ev.ts),
        eventsSeen: state.eventsSeen + 1,
        caseId: state.caseId ?? ev.case_id,
      }
    }

    case 'anomaly_detected':
      return {
        ...state,
        status: streaming(state.status),
        anomalies: new Map(state.anomalies).set(msg.data.anomaly_id, msg.data),
        pinnedEvents: pinCited(state, msg.data.evidence_event_ids),
        feed: pushCapped(
          state.feed,
          `anomaly:${msg.data.anomaly_id}`,
          { kind: 'anomaly', anomaly: msg.data },
          FEED_BUFFER_LIMIT,
        ),
      }

    case 'blast_radius':
      return {
        ...state,
        status: streaming(state.status),
        blastRadius: new Map(state.blastRadius).set(msg.data.component_id, msg.data),
      }

    case 'hypothesis_ranked': {
      // FULL-OBJECT UPSERT: replace wholesale. Merging would let a stale tier
      // or score survive a re-rank.
      const cited = [...msg.data.cited_evidence_ids]
      if (msg.data.trigger_event_id) cited.push(msg.data.trigger_event_id)
      return {
        ...state,
        status: streaming(state.status),
        caseId: state.caseId ?? msg.data.case_id,
        hypotheses: new Map(state.hypotheses).set(msg.data.hypothesis_id, msg.data),
        pinnedEvents: pinCited(state, cited),
      }
    }

    case 'tier_changed': {
      const hypotheses = new Map(state.hypotheses)
      const existing = hypotheses.get(msg.data.hypothesis_id)
      if (existing) {
        hypotheses.set(msg.data.hypothesis_id, {
          ...existing,
          tier: msg.data.tier,
          tier_reason: msg.data.tier_reason,
        })
      }
      // If the hypothesis is unknown we log the change but do NOT fabricate a
      // hypothesis from it — a tier is not enough to build one.
      return {
        ...state,
        status: streaming(state.status),
        hypotheses,
        tierChanges: [...state.tierChanges, msg.data],
      }
    }

    case 'counterfactual_result':
      return {
        ...state,
        status: streaming(state.status),
        counterfactuals: new Map(state.counterfactuals).set(msg.data.hypothesis_id, msg.data),
      }

    case 'twin_started':
      return {
        ...state,
        status: streaming(state.status),
        twinStarted: new Map(state.twinStarted).set(msg.data.hypothesis_id, msg.data),
      }

    case 'twin_result':
      return {
        ...state,
        status: streaming(state.status),
        twinResults: new Map(state.twinResults).set(msg.data.hypothesis_id, msg.data),
      }

    case 'challenger_attack': {
      const attacks = new Map(state.challengerAttacks)
      const prior = attacks.get(msg.data.hypothesis_id) ?? []
      attacks.set(msg.data.hypothesis_id, [...prior, msg.data])
      return { ...state, status: streaming(state.status), challengerAttacks: attacks }
    }

    case 'agent_step': {
      const prior = state.agentSteps[msg.data.agent] ?? []
      return {
        ...state,
        status: streaming(state.status),
        agentSteps: { ...state.agentSteps, [msg.data.agent]: [...prior, msg.data] },
      }
    }

    case 'agent_done':
      return {
        ...state,
        status: streaming(state.status),
        agentDone: { ...state.agentDone, [msg.data.agent]: msg.data },
      }

    case 'remediation_result':
      return {
        ...state,
        status: streaming(state.status),
        remediation: new Map(state.remediation).set(msg.data.hypothesis_id, msg.data),
      }

    case 'narration_chunk':
      return {
        ...state,
        status: streaming(state.status),
        narrationChunks: [...state.narrationChunks, msg.data],
        narration: state.narration + msg.data.text,
      }

    case 'pipeline_done':
      return { ...state, status: 'done', runId: state.runId ?? msg.data.run_id, doneInfo: msg.data }

    case 'pipeline_error':
      return {
        ...state,
        status: 'error',
        runId: state.runId ?? msg.data.run_id,
        errorInfo: msg.data,
      }

    default:
      // Forward compat: a future backend may emit events this build predates.
      return { ...state, unknownEvents: state.unknownEvents + 1 }
  }
}

export function applySseMessages(state: RunState, msgs: SseMessage[]): RunState {
  return msgs.reduce(applySseMessage, state)
}

// ─────────────────────────────────────────────────────────────────────────────
// Store
// ─────────────────────────────────────────────────────────────────────────────

export interface RunActions {
  dispatch: (msg: SseMessage) => void
  /** Wipe stream-derived state, keeping run identity. Call when the stream (re)opens. */
  reset: () => void
  /** Full teardown, including run identity. */
  clear: () => void
  attach: (runId: string, caseId?: string) => void
  setConnection: (connection: StreamStatus) => void
}

export type RunStore = RunState & RunActions

export const runStore = createStore<RunStore>()((set) => ({
  ...createInitialRunState(),

  dispatch: (msg) => set((state) => applySseMessage(state, msg)),

  reset: () =>
    set((state) => ({
      ...createInitialRunState(),
      runId: state.runId,
      caseId: state.caseId,
      status: state.status === 'idle' ? 'connecting' : state.status,
      connection: state.connection,
    })),

  clear: () => set(createInitialRunState()),

  attach: (runId, caseId) =>
    set(() => ({
      ...createInitialRunState(),
      runId,
      caseId: caseId ?? runId, // run_id === case_id backend-side
      status: 'connecting',
    })),

  setConnection: (connection) =>
    set((state) => ({
      connection,
      status:
        connection === 'error' && state.status !== 'done' && state.status !== 'error'
          ? 'error'
          : state.status,
    })),
}))

// ─────────────────────────────────────────────────────────────────────────────
// Selectors — the UI renders exactly what the backend sent, in backend order.
//
// SUBSCRIPTION SAFETY: these derive fresh arrays/objects, so they must NOT be
// passed straight to useRunStore(). zustand v5 is built on useSyncExternalStore,
// which compares snapshots with Object.is — a selector that allocates on every
// call never compares equal and re-renders forever ("Maximum update depth
// exceeded"). Subscribe to a stable slice (a Map, a primitive) and run these
// inside a useMemo keyed on it. src/store/useRunStore.ts has the hooks that do
// exactly that; prefer those in components and keep these for tests and derived
// logic.
// ─────────────────────────────────────────────────────────────────────────────

/** Hypotheses in backend `rank` order. Never re-sort by score. */
export function rankHypotheses(
  hypotheses: Map<HypothesisId, RankedHypothesis>,
): RankedHypothesis[] {
  return [...hypotheses.values()].sort((a, b) => a.rank - b.rank)
}

export function selectRankedHypotheses(state: RunState): RankedHypothesis[] {
  return rankHypotheses(state.hypotheses)
}

export function selectTopHypothesis(state: RunState): RankedHypothesis | null {
  return selectRankedHypotheses(state)[0] ?? null
}

/** Newest last. */
export function selectEvents(state: RunState): EventEnvelope[] {
  return [...state.events.values()]
}

/**
 * The presenter's stage indicator.
 *
 * The API has NO pipeline-stage field — `pipeline_error.stage` is only ever
 * "replay" or "pipeline", and no event carries a stage name. So this is inferred
 * from which event types have arrived, and it is display state only: it never
 * feeds a tier, score or rank (rule 3 forbids deriving those, and this derives
 * none of them). It mirrors backend/pipeline.py's real order:
 * detect → localize → score → INVESTIGATOR → rescore → CHALLENGER → NARRATOR.
 */
export const PIPELINE_STAGES = [
  'DETECT',
  'LOCALIZE',
  'RANK',
  'INVESTIGATE',
  'VERIFY',
  'NARRATE',
] as const

export type PipelineStage = (typeof PIPELINE_STAGES)[number]

/**
 * The furthest stage reached, or null before any traffic.
 *
 * Returns a plain string so it is safe to subscribe to directly: the status bar
 * then re-renders when the stage changes, not on every one of a run's ~186k
 * events.
 */
export function selectActiveStage(state: RunState): PipelineStage | null {
  if (state.status === 'done') return 'NARRATE'

  let active: PipelineStage | null = null
  const reach = (stage: PipelineStage, condition: boolean) => {
    if (condition) active = stage
  }

  reach('DETECT', state.eventsSeen > 0 || state.anomalies.size > 0)
  reach('LOCALIZE', state.blastRadius.size > 0)
  reach('RANK', state.hypotheses.size > 0)
  reach('INVESTIGATE', Object.keys(state.agentSteps).length > 0)
  reach(
    'VERIFY',
    state.counterfactuals.size > 0 ||
      state.twinStarted.size > 0 ||
      state.twinResults.size > 0 ||
      state.challengerAttacks.size > 0,
  )
  reach('NARRATE', state.narrationChunks.length > 0)

  return active
}

/**
 * The stages to paint as reached: everything up to and including `active`.
 * A prefix, deliberately — the indicator is a progress bar, and the backend
 * pipeline is sequential, so a later stage implies the earlier ones ran.
 */
export function stagesUpTo(active: PipelineStage | null): PipelineStage[] {
  if (!active) return []
  return PIPELINE_STAGES.slice(0, PIPELINE_STAGES.indexOf(active) + 1)
}

export function selectAnomalies(state: RunState): AnomalyEvent[] {
  return [...state.anomalies.values()]
}

/** Events and anomalies interleaved, arrival order, newest last. */
export function selectFeed(state: RunState): FeedItem[] {
  return [...state.feed.values()]
}

export interface CaseWindow {
  start: number
  end: number
}

/**
 * The case's time window. No endpoint exposes one, so it is derived: anomaly
 * windows first (they arrive complete via GET /run/{id}/anomalies and survive a
 * capped event stream), widened by any event timestamps we've seen.
 * LedgerRecord.ts_range is deliberately NOT used — nearly every production
 * writer passes (0.0, 0.0).
 */
export function selectCaseWindow(state: RunState): CaseWindow | null {
  let start: number | null = state.tsMin
  let end: number | null = state.tsMax

  for (const a of state.anomalies.values()) {
    start = start === null ? a.window.start : Math.min(start, a.window.start)
    end = end === null ? a.window.end : Math.max(end, a.window.end)
  }
  if (start === null || end === null) return null
  return { start, end: end > start ? end : start + 1 }
}

/** The replay playhead: the latest event time seen. */
export function selectPlayhead(state: RunState): number | null {
  return state.tsMax
}

export function selectAnomaliesFor(state: RunState, component: ComponentId): AnomalyEvent[] {
  return [...state.anomalies.values()].filter((a) => a.component_id === component)
}

/** Components with at least one anomaly — the amber set. */
export function selectAnomalousComponents(state: RunState): Set<ComponentId> {
  const out = new Set<ComponentId>()
  for (const a of state.anomalies.values()) out.add(a.component_id)
  return out
}

export function selectSeriesFor(state: RunState, component: ComponentId): MetricSeries[] {
  return [...state.metricSeries.values()]
    .filter((s) => s.component_id === component)
    .map((s) => ({ ...s, points: [...s.points].sort((a, b) => a.ts - b.ts) }))
}

export function selectConfigChanges(state: RunState): EventEnvelope[] {
  return [...state.configChanges.values()].sort((a, b) => a.ts - b.ts)
}

export function selectAgentNames(state: RunState): string[] {
  return Object.keys(state.agentSteps)
}

export function selectIsTerminal(state: RunState): boolean {
  return state.status === 'done' || state.status === 'error'
}
