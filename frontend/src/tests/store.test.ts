import { describe, expect, it } from 'vitest'
import {
  EVENT_BUFFER_LIMIT,
  applySseMessage,
  applySseMessages,
  createInitialRunState,
  runStore,
  selectEvents,
  selectRankedHypotheses,
} from '@/store/runStore'
import { toSseMessage } from '@/api/sseParse'
import {
  MOCK_RECORDINGS,
  loadMockSseMessages,
  loadMockSseRecords,
  scenarioForRun,
} from '@/mocks/mockStream'
import { SSE_EVENT_NAMES, isTerminalSseEvent } from '@/types/api'
import type { RankedHypothesis, SseMessage } from '@/types/api'
import type { EventEnvelope } from '@/types/events'

// ─────────────────────────────────────────────────────────────────────────────
// Builders
// ─────────────────────────────────────────────────────────────────────────────

function hypothesis(overrides: Partial<RankedHypothesis> = {}): RankedHypothesis {
  return {
    hypothesis_id: 'hyp-catalogue_db-01',
    case_id: 'case-001',
    rank: 1,
    suspect_component: 'catalogue-db',
    statement: 'catalogue-db pool exhaustion cascaded upstream.',
    score: 0.52,
    score_breakdown: {
      coverage: 0.22,
      topo_consistency: 0.12,
      precedence: 0,
      corroboration: 0.08,
      pagerank: 0.1,
    },
    tier: 'CORRELATED',
    tier_reason: 'rank floor: no counterfactual or twin evidence yet',
    cited_evidence_ids: ['metric-catalogue_db-000002'],
    predicted_symptoms: [],
    counterfactual: { removed: false, anomalies_still_explained_pct: 100 },
    twin: null,
    challenger: null,
    trigger_event_id: null,
    fault_type_guess: null,
    ...overrides,
  }
}

function envelope(n: number): EventEnvelope {
  const id = String(n).padStart(6, '0')
  return {
    event_id: `metric-catalogue-${id}`,
    case_id: 'case-001',
    source: 'metric',
    component_id: 'catalogue',
    ts: 1700000000 + n,
    payload: { kind: 'metric', name: 'cpu', value: 0.5, unit: 'cores' },
  }
}

const ranked = (h: RankedHypothesis): SseMessage => ({ event: 'hypothesis_ranked', data: h })

// ─────────────────────────────────────────────────────────────────────────────
// hypothesis_ranked is a FULL-OBJECT UPSERT keyed by hypothesis_id
// ─────────────────────────────────────────────────────────────────────────────

describe('hypothesis upsert', () => {
  it('replaces the prior object wholesale rather than merging it', () => {
    const first = hypothesis({
      twin: { run: 'twin-0001', similarity: 0.86, verdict: 'match', missing_evidence: [] },
      fault_type_guess: 'cpu',
      cited_evidence_ids: ['metric-catalogue_db-000002', 'alert-catalogue_db-000001'],
    })
    const second = hypothesis({
      score: 0.7,
      tier: 'CONFIRMED',
      tier_reason: 'config change precedes all symptoms',
      twin: null,
      fault_type_guess: null,
      cited_evidence_ids: ['config-catalogue_db-000001'],
    })

    const state = applySseMessages(createInitialRunState(), [ranked(first), ranked(second)])

    expect(state.hypotheses.size).toBe(1)
    const stored = state.hypotheses.get('hyp-catalogue_db-01')!
    expect(stored).toEqual(second)
    // The giveaway for a merge: fields the re-emit cleared would survive.
    expect(stored.twin).toBeNull()
    expect(stored.fault_type_guess).toBeNull()
    expect(stored.cited_evidence_ids).toEqual(['config-catalogue_db-000001'])
  })

  it('keys by hypothesis_id, so replay-from-zero is idempotent', () => {
    const a = hypothesis()
    const b = hypothesis({ hypothesis_id: 'hyp-catalogue-02', rank: 2 })
    const once = applySseMessages(createInitialRunState(), [ranked(a), ranked(b)])
    const twice = applySseMessages(once, [ranked(a), ranked(b)])

    expect(twice.hypotheses.size).toBe(2)
    expect(selectRankedHypotheses(twice).map((h) => h.hypothesis_id)).toEqual([
      'hyp-catalogue_db-01',
      'hyp-catalogue-02',
    ])
  })

  it('orders by the backend rank field, never by score', () => {
    const state = applySseMessages(createInitialRunState(), [
      ranked(hypothesis({ hypothesis_id: 'hyp-front_end-03', rank: 3, score: 0.9 })),
      ranked(hypothesis({ hypothesis_id: 'hyp-catalogue_db-01', rank: 1, score: 0.1 })),
      ranked(hypothesis({ hypothesis_id: 'hyp-catalogue-02', rank: 2, score: 0.5 })),
    ])
    expect(selectRankedHypotheses(state).map((h) => h.rank)).toEqual([1, 2, 3])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Tiers are assigned by the backend alone
// ─────────────────────────────────────────────────────────────────────────────

describe('tiers', () => {
  it('applies tier_changed to an existing hypothesis', () => {
    const state = applySseMessages(createInitialRunState(), [
      ranked(hypothesis()),
      {
        event: 'tier_changed',
        data: {
          hypothesis_id: 'hyp-catalogue_db-01',
          tier: 'CONFIRMED',
          tier_reason: 'config change precedes all symptoms',
        },
      },
    ])

    const stored = state.hypotheses.get('hyp-catalogue_db-01')!
    expect(stored.tier).toBe('CONFIRMED')
    expect(stored.tier_reason).toBe('config change precedes all symptoms')
    expect(state.tierChanges).toHaveLength(1)
  })

  it('changes tier ONLY via tier_changed and hypothesis_ranked', () => {
    const base = applySseMessage(createInitialRunState(), ranked(hypothesis()))
    expect(base.hypotheses.get('hyp-catalogue_db-01')!.tier).toBe('CORRELATED')

    // Everything that might tempt a UI into "promoting" a hypothesis itself:
    // a confirming twin, a decisive counterfactual, a refuted attack.
    const noisy = applySseMessages(base, [
      {
        event: 'twin_result',
        data: {
          hypothesis_id: 'hyp-catalogue_db-01',
          run: 'twin-0001',
          similarity: 0.99,
          verdict: 'match',
          missing_evidence: [],
        },
      },
      {
        event: 'counterfactual_result',
        data: {
          hypothesis_id: 'hyp-catalogue_db-01',
          removed: 'catalogue-db',
          anomalies_still_explained_pct: 0,
        },
      },
      {
        event: 'challenger_attack',
        data: {
          hypothesis_id: 'hyp-catalogue_db-01',
          claim: 'it was the CPU',
          contradicting_event_id: 'metric-catalogue-000001',
          upheld: false,
        },
      },
      {
        event: 'remediation_result',
        data: {
          hypothesis_id: 'hyp-catalogue_db-01',
          remedy: 'revert max_connections',
          symptoms_cleared_pct: 100,
          sim_time_to_recover_s: 42,
        },
      },
    ])

    expect(noisy.hypotheses.get('hyp-catalogue_db-01')!.tier).toBe('CORRELATED')
    expect(noisy.tierChanges).toHaveLength(0)
  })

  it('logs a tier_changed for an unknown hypothesis without fabricating one', () => {
    const state = applySseMessage(createInitialRunState(), {
      event: 'tier_changed',
      data: { hypothesis_id: 'hyp-ghost-99', tier: 'CONFIRMED', tier_reason: 'arrived early' },
    })
    expect(state.hypotheses.size).toBe(0)
    expect(state.tierChanges).toHaveLength(1)
  })

  it('lets a hypothesis_ranked re-emit override an earlier tier_changed', () => {
    const state = applySseMessages(createInitialRunState(), [
      ranked(hypothesis()),
      {
        event: 'tier_changed',
        data: { hypothesis_id: 'hyp-catalogue_db-01', tier: 'CONFIRMED', tier_reason: 'promoted' },
      },
      ranked(hypothesis({ tier: 'MISSING_EVIDENCE', tier_reason: 'rescored after challenge' })),
    ])
    // Latest wins: the upsert is the newer truth in its entirety.
    expect(state.hypotheses.get('hyp-catalogue_db-01')!.tier).toBe('MISSING_EVIDENCE')
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Narration
// ─────────────────────────────────────────────────────────────────────────────

describe('narration', () => {
  it('concatenates chunk deltas into one markdown string', () => {
    const state = applySseMessages(createInitialRunState(), [
      { event: 'narration_chunk', data: { ts: 0, text: '## Verdict\n\n' } },
      { event: 'narration_chunk', data: { ts: 0, text: '**catalogue-db** is ' } },
      { event: 'narration_chunk', data: { ts: 0, text: 'the root cause.' } },
    ])

    expect(state.narration).toBe('## Verdict\n\n**catalogue-db** is the root cause.')
    expect(state.narrationChunks).toHaveLength(3)
  })

  it('starts empty and stays a string with no chunks', () => {
    expect(createInitialRunState().narration).toBe('')
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Forward compat
// ─────────────────────────────────────────────────────────────────────────────

describe('unknown events', () => {
  it('ignores an unknown SSE type without throwing', () => {
    const before = applySseMessage(createInitialRunState(), ranked(hypothesis()))
    const unknown = { event: 'causal_graph_ready', data: { nodes: 15 } } as unknown as SseMessage

    expect(() => applySseMessage(before, unknown)).not.toThrow()

    const after = applySseMessage(before, unknown)
    expect(after.unknownEvents).toBe(1)
    expect(after.hypotheses).toEqual(before.hypotheses)
    expect(after.status).toBe(before.status)
  })

  it('toSseMessage returns null for an unknown event name', () => {
    expect(toSseMessage('causal_graph_ready', '{"nodes":15}')).toBeNull()
  })

  it('toSseMessage returns null for unparseable data rather than throwing', () => {
    expect(toSseMessage('pipeline_done', 'not json')).toBeNull()
    expect(toSseMessage('pipeline_done', '"a bare string"')).toBeNull()
  })

  it('toSseMessage parses every known event name', () => {
    for (const name of SSE_EVENT_NAMES) {
      expect(toSseMessage(name, '{}')?.event).toBe(name)
    }
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Event ring buffer
// ─────────────────────────────────────────────────────────────────────────────

describe('event buffer', () => {
  it(`keeps only the newest ${EVENT_BUFFER_LIMIT} events but counts them all`, () => {
    const msgs: SseMessage[] = Array.from({ length: EVENT_BUFFER_LIMIT + 120 }, (_, i) => ({
      event: 'event_ingested',
      data: envelope(i + 1),
    }))
    const state = applySseMessages(createInitialRunState(), msgs)

    expect(state.events.size).toBe(EVENT_BUFFER_LIMIT)
    expect(state.eventsSeen).toBe(EVENT_BUFFER_LIMIT + 120)

    const kept = selectEvents(state)
    expect(kept[0]!.event_id).toBe('metric-catalogue-000121')
    expect(kept.at(-1)!.event_id).toBe('metric-catalogue-000620')
  })

  it('dedupes by event_id, so a re-delivered event does not double up', () => {
    const state = applySseMessages(createInitialRunState(), [
      { event: 'event_ingested', data: envelope(1) },
      { event: 'event_ingested', data: envelope(2) },
      { event: 'event_ingested', data: envelope(1) },
    ])
    expect(state.events.size).toBe(2)
    expect(state.eventsSeen).toBe(3)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Agents, terminal events, store instance
// ─────────────────────────────────────────────────────────────────────────────

describe('agents', () => {
  it('groups steps by agent name and records terminal status', () => {
    const state = applySseMessages(createInitialRunState(), [
      {
        event: 'agent_step',
        data: { agent: 'investigator', tool: 'get_anomalies', args_summary: '', result_summary: '6' },
      },
      {
        event: 'agent_step',
        data: { agent: 'challenger', tool: 'get_events', args_summary: '', result_summary: 'ok' },
      },
      {
        event: 'agent_step',
        data: { agent: 'investigator', tool: 'run_twin', args_summary: '', result_summary: 'match' },
      },
      { event: 'agent_done', data: { agent: 'challenger', status: 'budget_exhausted', summary: null } },
    ])

    expect(state.agentSteps.investigator).toHaveLength(2)
    expect(state.agentSteps.challenger).toHaveLength(1)
    expect(state.agentDone.challenger!.status).toBe('budget_exhausted')
    // `summary` is genuinely nullable on the wire.
    expect(state.agentDone.challenger!.summary).toBeNull()
  })

  it('tolerates a null tool (the OFFLINE replay path emits one)', () => {
    const state = applySseMessage(createInitialRunState(), {
      event: 'agent_step',
      data: { agent: 'investigator', tool: null, args_summary: '', result_summary: 'replayed' },
    })
    expect(state.agentSteps.investigator![0]!.tool).toBeNull()
  })
})

describe('terminal events', () => {
  it('pipeline_done sets status done', () => {
    const state = applySseMessage(createInitialRunState(), {
      event: 'pipeline_done',
      data: { run_id: 'case-001', n_hypotheses: 3 },
    })
    expect(state.status).toBe('done')
    expect(state.doneInfo).toEqual({ run_id: 'case-001', n_hypotheses: 3 })
  })

  it('pipeline_error sets status error and keeps the stage', () => {
    const state = applySseMessage(createInitialRunState(), {
      event: 'pipeline_error',
      data: { run_id: 'case-001', stage: 'pipeline', error: 'RuntimeError: boom' },
    })
    expect(state.status).toBe('error')
    expect(state.errorInfo!.stage).toBe('pipeline')
  })

  it('later traffic does not resurrect a finished run', () => {
    const done = applySseMessage(createInitialRunState(), {
      event: 'pipeline_done',
      data: { run_id: 'case-001', n_hypotheses: 3 },
    })
    const after = applySseMessage(done, ranked(hypothesis()))
    expect(after.status).toBe('done')
  })
})

describe('store instance', () => {
  it('dispatches, resets stream-derived state, and keeps run identity', () => {
    runStore.getState().clear()
    runStore.getState().attach('case-001')
    runStore.getState().dispatch(ranked(hypothesis()))
    expect(runStore.getState().hypotheses.size).toBe(1)

    // What the stream does on every (re)connect, because the server replays
    // the whole run from index 0.
    runStore.getState().reset()
    expect(runStore.getState().hypotheses.size).toBe(0)
    expect(runStore.getState().runId).toBe('case-001')
    expect(runStore.getState().caseId).toBe('case-001')

    runStore.getState().clear()
    expect(runStore.getState().runId).toBeNull()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// The mock recording — the demo depends on it being complete and terminating
// ─────────────────────────────────────────────────────────────────────────────

describe('mock sse sequence', () => {
  it('covers all 15 contract event types across the recordings', () => {
    // Deliberately several recordings: a run ends in EITHER pipeline_done OR
    // pipeline_error and the bus drops anything after a terminal event, so no
    // single run can legally contain both. challenger_attack lives only in the
    // red-herring run, because the challenger only ever attacks the rank-1
    // hypothesis and an upheld attack blocks CONFIRMED (tiers.py:157) — putting
    // one in the clean-cascade run would contradict its own tier.
    const seen = new Set(
      Object.values(MOCK_RECORDINGS).flatMap((raw) =>
        loadMockSseMessages(raw).map((m) => m.event),
      ),
    )
    expect([...SSE_EVENT_NAMES].filter((n) => !seen.has(n))).toEqual([])
    expect(seen.size).toBe(15)
  })

  it('never emits upheld:false — the backend discards rejected attacks', () => {
    // challenger.py:103-113 `continue`s past any attack that fails validation
    // and hardcodes upheld:True on the ones it keeps. A recording containing
    // upheld:false would be modelling a state the backend cannot produce.
    for (const raw of Object.values(MOCK_RECORDINGS)) {
      for (const msg of loadMockSseMessages(raw)) {
        if (msg.event === 'challenger_attack') expect(msg.data.upheld).toBe(true)
        if (msg.event === 'hypothesis_ranked') {
          for (const attack of msg.data.challenger?.attacks ?? []) {
            expect(attack.upheld).toBe(true)
          }
        }
      }
    }
  })

  it('has exactly one terminal event, last, in each recording', () => {
    for (const raw of Object.values(MOCK_RECORDINGS)) {
      const msgs = loadMockSseMessages(raw)
      const terminals = msgs.filter((m) => isTerminalSseEvent(m.event))
      expect(terminals).toHaveLength(1)
      expect(isTerminalSseEvent(msgs.at(-1)!.event)).toBe(true)
    }
  })

  it('ends the happy path in pipeline_done', () => {
    expect(loadMockSseMessages(MOCK_RECORDINGS.happy).at(-1)!.event).toBe('pipeline_done')
  })

  it('ends the failure path in pipeline_error, and the store surfaces it', () => {
    const msgs = loadMockSseMessages(MOCK_RECORDINGS.error)
    expect(msgs.at(-1)!.event).toBe('pipeline_error')

    const state = applySseMessages(createInitialRunState(), msgs)
    expect(state.status).toBe('error')
    expect(state.errorInfo!.stage).toBe('pipeline')
  })

  it('routes a -error runId to the failure recording', () => {
    expect(scenarioForRun('case-001')).toBe('happy')
    expect(scenarioForRun('case-001-error')).toBe('error')
  })

  it('contains an unknown event type, which is dropped rather than fatal', () => {
    const records = loadMockSseRecords()
    const messages = loadMockSseMessages()
    expect(records.length).toBeGreaterThan(messages.length)
  })

  it('replays into a coherent verdict', () => {
    const state = applySseMessages(createInitialRunState(), loadMockSseMessages())

    expect(state.status).toBe('done')
    expect(state.doneInfo!.n_hypotheses).toBe(3)
    expect(state.hypotheses.size).toBe(3)

    // The recording upserts hyp-catalogue_db-01 twice: rank floor, then the
    // post-investigation rescore. The final object must be the later one.
    const top = selectRankedHypotheses(state)[0]!
    expect(top.hypothesis_id).toBe('hyp-catalogue_db-01')
    expect(top.tier).toBe('CONFIRMED')
    expect(top.score).toBe(0.7)
    expect(top.twin!.verdict).toBe('match')

    expect(state.narration.startsWith('## Verdict')).toBe(true)
    expect(state.unknownEvents).toBe(0) // dropped at parse, never reaches the store
    expect(Object.keys(state.agentSteps).sort()).toEqual([
      'challenger',
      'investigator',
      'remediation',
    ])
  })

  it('honours ordering guarantee 1: cited events are ingested before the anomaly', () => {
    const ingested = new Set<string>()
    for (const msg of loadMockSseMessages()) {
      if (msg.event === 'event_ingested') ingested.add(msg.data.event_id)
      if (msg.event === 'anomaly_detected') {
        for (const id of msg.data.evidence_event_ids) {
          expect(ingested.has(id), `${id} cited by ${msg.data.anomaly_id} before ingest`).toBe(true)
        }
      }
    }
  })
})
