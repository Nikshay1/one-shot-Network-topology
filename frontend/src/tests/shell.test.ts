import { describe, expect, it } from 'vitest'
import {
  SCENARIO_TYPES,
  DEMO_PRESET,
  demoButtons,
  identifyCase,
} from '@/demo/scenarios'
import {
  PIPELINE_STAGES,
  applySseMessages,
  createInitialRunState,
  selectActiveStage,
  selectFeed,
  stagesUpTo,
  FEED_BUFFER_LIMIT,
} from '@/store/runStore'
import { parseView, RUN_VIEWS } from '@/pages/RunPage'
import { summarizePayload, formatClock } from '@/lib/format'
import { loadMockSseMessages, MOCK_RECORDINGS } from '@/mocks/mockStream'
import mockCases from '@/mocks/fixtures/mock_cases.json'
import type { CaseSummary, SseMessage } from '@/types/api'

describe('case identification', () => {
  it('reads kind and variant out of the case_id', () => {
    expect(identifyCase('clean_cascade-01')).toEqual({
      kind: 'synthetic',
      scenarioType: 'clean_cascade',
      variantNumber: 1,
    })
    expect(identifyCase('red_herring_config-05')).toEqual({
      kind: 'synthetic',
      scenarioType: 'red_herring_config',
      variantNumber: 5,
    })
  })

  it('treats an unrecognized id as a real case rather than guessing', () => {
    expect(identifyCase('catalogue_cpu-1').kind).toBe('real')
    expect(identifyCase('catalogue_cpu-1').scenarioType).toBeNull()
  })

  it('does not confuse a prefix that merely starts with a type name', () => {
    // "ambiguous_thing-01" is not the "ambiguous" scenario.
    expect(identifyCase('ambiguous_thing-01').kind).toBe('real')
    // but the bare type name is.
    expect(identifyCase('ambiguous').scenarioType).toBe('ambiguous')
  })
})

describe('demo buttons', () => {
  const cases = mockCases as CaseSummary[]

  it('offers one button per scenario type present, numbered 1..7', () => {
    const demos = demoButtons(cases)
    expect(demos).toHaveLength(7)
    expect(demos.map((d) => d.n)).toEqual([1, 2, 3, 4, 5, 6, 7])
    expect(demos.map((d) => d.scenarioType)).toEqual([...SCENARIO_TYPES])
  })

  it('binds each button to the lowest-numbered variant', () => {
    const demos = demoButtons(cases)
    expect(demos[0]!.caseId).toBe('clean_cascade-01')
    expect(demos.find((d) => d.scenarioType === 'ambiguous')!.caseId).toBe('ambiguous-01')
  })

  it('drops scenarios with no case rather than faking a button', () => {
    const only = cases.filter((c) => c.case_id.startsWith('alert_storm'))
    const demos = demoButtons(only)
    expect(demos).toHaveLength(1)
    expect(demos[0]!.n).toBe(1)
    expect(demoButtons([])).toEqual([])
  })

  it('uses the measured demo speed, not real-time', () => {
    // speed=0 never interleaves agent steps; speed=1 is not real-time.
    expect(DEMO_PRESET.speed).toBe(10)
  })

  it('exposes no ground truth', () => {
    const blob = JSON.stringify(demoButtons(cases))
    for (const field of ['fault_service', 'inject_time', 'ground_truth', 'truth']) {
      expect(blob).not.toContain(field)
    }
  })
})

describe('pipeline stage', () => {
  it('is null before any traffic', () => {
    expect(selectActiveStage(createInitialRunState())).toBeNull()
  })

  it('returns a stable primitive, so it is safe to subscribe to directly', () => {
    // Object.is equality is what useSyncExternalStore compares snapshots with;
    // a selector that allocates would loop forever. See useRunStore.ts.
    const state = applySseMessages(createInitialRunState(), [
      { event: 'narration_chunk', data: { ts: 0, text: 'x' } },
    ])
    expect(Object.is(selectActiveStage(state), selectActiveStage(state))).toBe(true)
  })

  it('advances through the stages as the matching events arrive', () => {
    let state = createInitialRunState()
    const at = () => selectActiveStage(state)

    state = applySseMessages(state, [
      {
        event: 'event_ingested',
        data: {
          event_id: 'metric-catalogue-000001',
          case_id: 'case-001',
          source: 'metric',
          component_id: 'catalogue',
          ts: 1700000030,
          payload: { kind: 'metric', name: 'cpu', value: 0.94 },
        },
      },
    ])
    expect(at()).toBe('DETECT')

    state = applySseMessages(state, [
      { event: 'blast_radius', data: { component_id: 'catalogue-db', radius: 1, affected: ['x'] } },
    ])
    expect(at()).toBe('LOCALIZE')

    state = applySseMessages(state, [
      {
        event: 'agent_step',
        data: { agent: 'investigator', tool: 'get_anomalies', args_summary: '', result_summary: '' },
      },
    ])
    // No hypotheses yet, so RANK was skipped — the furthest reached still wins.
    expect(at()).toBe('INVESTIGATE')

    state = applySseMessages(state, [
      { event: 'narration_chunk', data: { ts: 0, text: 'hello' } },
    ])
    expect(at()).toBe('NARRATE')
  })

  it('marks every stage reached once the pipeline is done', () => {
    const state = applySseMessages(createInitialRunState(), [
      { event: 'pipeline_done', data: { run_id: 'case-001', n_hypotheses: 3 } },
    ])
    expect(selectActiveStage(state)).toBe('NARRATE')
    expect(stagesUpTo(selectActiveStage(state))).toEqual([...PIPELINE_STAGES])
  })

  it('walks the full mock recording to NARRATE', () => {
    const state = applySseMessages(createInitialRunState(), loadMockSseMessages())
    expect(selectActiveStage(state)).toBe('NARRATE')
  })

  it('a failed run does not claim it narrated', () => {
    const state = applySseMessages(
      createInitialRunState(),
      loadMockSseMessages(MOCK_RECORDINGS.error),
    )
    expect(selectActiveStage(state)).toBe('LOCALIZE')
    expect(stagesUpTo(selectActiveStage(state))).not.toContain('NARRATE')
  })

  it('stagesUpTo is a prefix — the pipeline is sequential', () => {
    expect(stagesUpTo(null)).toEqual([])
    expect(stagesUpTo('RANK')).toEqual(['DETECT', 'LOCALIZE', 'RANK'])
  })
})

describe('incident feed', () => {
  const event = (n: number): SseMessage => ({
    event: 'event_ingested',
    data: {
      event_id: `metric-catalogue-${String(n).padStart(6, '0')}`,
      case_id: 'case-001',
      source: 'metric',
      component_id: 'catalogue',
      ts: 1700000000 + n,
      payload: { kind: 'metric', name: 'cpu', value: 0.1 },
    },
  })

  it('interleaves anomalies with events in arrival order', () => {
    const state = applySseMessages(createInitialRunState(), [
      event(1),
      {
        event: 'anomaly_detected',
        data: {
          anomaly_id: 'anom-catalogue-0002',
          case_id: 'case-001',
          source: 'metric',
          component_id: 'catalogue',
          window: { start: 1700000025, end: 1700000055 },
          score: 0.71,
          method: 'isolation_forest',
          evidence_event_ids: ['metric-catalogue-000001'],
          summary: 'cpu outlier',
        },
      },
      event(2),
    ])

    expect(selectFeed(state).map((i) => i.kind)).toEqual(['event', 'anomaly', 'event'])
  })

  it('caps and dedupes like the event buffer does', () => {
    const msgs = Array.from({ length: FEED_BUFFER_LIMIT + 50 }, (_, i) => event(i + 1))
    const state = applySseMessages(createInitialRunState(), [...msgs, event(1)])
    expect(state.feed.size).toBe(FEED_BUFFER_LIMIT)
  })

  it('populates the rail from the mock run', () => {
    const state = applySseMessages(createInitialRunState(), loadMockSseMessages())
    expect(state.anomalies.size).toBe(6)
    expect(selectFeed(state).some((i) => i.kind === 'anomaly')).toBe(true)
  })
})

describe('payload summaries', () => {
  it('renders one line per payload variant', () => {
    expect(summarizePayload({ kind: 'metric', name: 'cpu', value: 0.94, unit: 'cores' })).toBe(
      'cpu 0.94 cores',
    )
    expect(summarizePayload({ kind: 'metric', name: 'cpu', value: 0.94 })).toBe('cpu 0.94')
    expect(summarizePayload({ kind: 'alert', name: 'Elevated5xx', severity: 0.75, state: 'firing' })).toBe(
      'Elevated5xx firing (sev 0.75)',
    )
    expect(
      summarizePayload({ kind: 'topology', target_component_id: 'catalogue-db', relation: 'reads_from' }),
    ).toBe('reads_from → catalogue-db')
    expect(
      summarizePayload({ kind: 'config', key: 'max_connections', old_value: 200, new_value: 50, risky: true }),
    ).toBe('max_connections: 200 → 50 · risky')
  })

  it('handles a log with no level, which is the common case', () => {
    expect(summarizePayload({ kind: 'log', message: 'too many connections' })).toBe(
      'too many connections',
    )
    expect(summarizePayload({ kind: 'log', level: 'INFO', message: 'retrying' })).toBe(
      '[INFO] retrying',
    )
  })

  it('distinguishes an absent config value from a null one', () => {
    expect(summarizePayload({ kind: 'config', key: 'k', new_value: null })).toBe('k: ? → null')
  })

  it('formats a clock without throwing on epoch seconds', () => {
    expect(formatClock(1700000030)).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })
})

describe('run views', () => {
  it('are URL-addressable and default safely', () => {
    expect(RUN_VIEWS).toEqual(['incident', 'verdict', 'agents', 'report'])
    expect(parseView('verdict')).toBe('verdict')
    expect(parseView(null)).toBe('incident')
    expect(parseView('nonsense')).toBe('incident')
  })
})
