import { describe, expect, it } from 'vitest'
import {
  REDUNDANT_PCT,
  causalEdges,
  clearedComponents,
  indexGraph,
  isInstrumented,
  shortestPath,
  uninstrumentedNodes,
} from '@/lib/graph'
import { downsample, MAX_POINTS } from '@/components/MetricSparkline'
import { mockTopology } from '@/mocks/topology'
import {
  applySseMessages,
  createInitialRunState,
  selectCaseWindow,
  selectConfigChanges,
  selectSeriesFor,
  METRIC_SERIES_CAP,
  metricKey,
} from '@/store/runStore'
import { loadMockSseMessages, MOCK_RECORDINGS } from '@/mocks/mockStream'
import { selectRankedHypotheses } from '@/store/runStore'
import type { MetricPoint } from '@/store/runStore'
import type { RankedHypothesis } from '@/types/hypothesis'
import type { SseMessage } from '@/types/api'

const topology = mockTopology('clean_cascade-01')
const index = indexGraph(topology)

describe('topology shape', () => {
  it('mirrors the real backend: no service_type, empty graph dict, calls edges', () => {
    // fixtures/sample_topology.json invents all three; the backend writes none.
    expect(topology.graph).toEqual({})
    expect(topology.nodes.every((n) => !('service_type' in n))).toBe(true)
    expect(topology.links.every((l) => l.relation === 'calls')).toBe(true)
  })

  it('omits `instrumented` entirely on a real case, as RE2-SS topologies do', () => {
    const real = mockTopology('catalogue_cpu-1')
    expect(real.nodes.every((n) => !('instrumented' in n))).toBe(true)
    // Absent must read as instrumented — otherwise every real node renders hollow.
    expect(isInstrumented(real, 'catalogue')).toBe(true)
    expect(uninstrumentedNodes(real).size).toBe(0)
  })

  it('marks nodes uninstrumented only for the missing_telemetry scenario', () => {
    expect(uninstrumentedNodes(topology).size).toBe(0)
    const missing = mockTopology('missing_telemetry-01')
    expect([...uninstrumentedNodes(missing)].sort()).toEqual(['carts-db', 'queue-master'])
    expect(isInstrumented(missing, 'carts-db')).toBe(false)
    expect(isInstrumented(missing, 'catalogue')).toBe(true)
  })
})

describe('paths', () => {
  it('follows the call direction: front-end calls catalogue calls catalogue-db', () => {
    expect(shortestPath(index, 'front-end', 'catalogue-db')).toEqual([
      'front-end',
      'catalogue',
      'catalogue-db',
    ])
  })

  it('is directed — a database does not call its caller', () => {
    expect(shortestPath(index, 'catalogue-db', 'front-end')).toEqual([])
  })

  it('returns [] for unreachable pairs', () => {
    expect(shortestPath(index, 'payment', 'catalogue')).toEqual([])
  })
})

describe('causal edges', () => {
  const hypothesis = {
    suspect_component: 'catalogue-db',
    predicted_symptoms: [
      { component_id: 'front-end', expectation: 'elevated 5xx', observed: true },
      { component_id: 'catalogue', expectation: 'connection refused', observed: true },
    ],
  }

  it('draws failure propagating AGAINST the call edges, suspect → symptom', () => {
    const edges = causalEdges(index, hypothesis)
    // catalogue-db breaks -> catalogue suffers -> front-end suffers.
    expect(edges).toEqual([
      { from: 'catalogue-db', to: 'catalogue' },
      { from: 'catalogue', to: 'front-end' },
    ])
  })

  it('dedupes edges shared by two symptom paths', () => {
    const edges = causalEdges(index, hypothesis)
    const ids = edges.map((e) => `${e.from}->${e.to}`)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('ignores a symptom with no path to the suspect', () => {
    const edges = causalEdges(index, {
      suspect_component: 'catalogue-db',
      predicted_symptoms: [{ component_id: 'payment', expectation: 'x', observed: null }],
    })
    expect(edges).toEqual([])
  })

  it('keeps uninstrumented symptoms on the path — no data is not no path', () => {
    const edges = causalEdges(index, {
      suspect_component: 'catalogue-db',
      predicted_symptoms: [{ component_id: 'front-end', expectation: 'x', observed: null }],
    })
    expect(edges.length).toBeGreaterThan(0)
  })
})

describe('cleared components', () => {
  const base: RankedHypothesis = {
    hypothesis_id: 'hyp-payment-01',
    case_id: 'c',
    rank: 2,
    suspect_component: 'payment',
    statement: '',
    score: 0.2,
    score_breakdown: { coverage: 0, topo_consistency: 0, precedence: 0, corroboration: 0, pagerank: 0 },
    tier: 'CORRELATED',
    tier_reason: '',
    cited_evidence_ids: [],
    predicted_symptoms: [],
    counterfactual: { removed: true, anomalies_still_explained_pct: 100 },
    twin: null,
    challenger: null,
    trigger_event_id: null,
    fault_type_guess: null,
  }

  it('clears a component a bought counterfactual found redundant', () => {
    expect(clearedComponents([base]).map((c) => c.component)).toEqual(['payment'])
  })

  it('NEVER clears on the ranking-floor proxy (removed === false)', () => {
    // scorer.py fills counterfactual with a proxy and removed:false. Reading the
    // pct then would clear components nobody ever tested.
    const proxy = { ...base, counterfactual: { removed: false, anomalies_still_explained_pct: 100 } }
    expect(clearedComponents([proxy])).toEqual([])
  })

  it('respects the backend threshold of 70', () => {
    expect(REDUNDANT_PCT).toBe(70)
    const at = { ...base, counterfactual: { removed: true, anomalies_still_explained_pct: 70 } }
    const below = { ...base, counterfactual: { removed: true, anomalies_still_explained_pct: 69.9 } }
    expect(clearedComponents([at])).toHaveLength(1)
    expect(clearedComponents([below])).toHaveLength(0)
  })

  it('does not clear a load-bearing suspect', () => {
    const loadBearing = {
      ...base,
      suspect_component: 'catalogue',
      counterfactual: { removed: true, anomalies_still_explained_pct: 16.7 },
    }
    expect(clearedComponents([loadBearing])).toEqual([])
  })
})

describe('red herring recording', () => {
  const state = applySseMessages(createInitialRunState(), loadMockSseMessages(MOCK_RECORDINGS.redherring))

  it('ends with the real cause ranked 1 and the red herring demoted', () => {
    const ranked = selectRankedHypotheses(state)
    expect(ranked[0]!.suspect_component).toBe('catalogue')
    expect(ranked[0]!.tier).toBe('CONFIRMED')
    expect(ranked[1]!.suspect_component).toBe('payment')
  })

  it('clears the red herring — THE visual', () => {
    const cleared = clearedComponents(selectRankedHypotheses(state))
    expect(cleared.map((c) => c.component)).toEqual(['payment'])
    expect(cleared[0]!.anomalies_still_explained_pct).toBe(100)
  })

  it('never uses the word "innocent" — that is ground truth, and /eval-only', () => {
    const blob = JSON.stringify(loadMockSseMessages(MOCK_RECORDINGS.redherring))
    expect(blob.toLowerCase()).not.toContain('innocent')
    expect(blob).not.toContain('fault_service')
    expect(blob).not.toContain('ground_truth')
  })

  it('the happy path clears front-end, as the real clean_cascade ledger does', () => {
    const happy = applySseMessages(createInitialRunState(), loadMockSseMessages())
    expect(clearedComponents(selectRankedHypotheses(happy)).map((c) => c.component)).toEqual([
      'front-end',
    ])
  })
})

describe('metric series', () => {
  const metric = (n: number, value: number): SseMessage => ({
    event: 'event_ingested',
    data: {
      event_id: `metric-catalogue-${String(n).padStart(6, '0')}`,
      case_id: 'c',
      source: 'metric',
      component_id: 'catalogue',
      ts: 1700000000 + n,
      payload: { kind: 'metric', name: 'cpu', value, unit: 'cores' },
    },
  })

  it('accumulates points per component and metric', () => {
    const state = applySseMessages(createInitialRunState(), [metric(1, 0.1), metric(2, 0.9)])
    const series = state.metricSeries.get(metricKey('catalogue', 'cpu'))!
    expect(series.points).toEqual([
      { ts: 1700000001, value: 0.1 },
      { ts: 1700000002, value: 0.9 },
    ])
    expect(series.unit).toBe('cores')
  })

  it('decimates at the cap instead of dropping the oldest', () => {
    const msgs = Array.from({ length: METRIC_SERIES_CAP + 1 }, (_, i) => metric(i + 1, i))
    const state = applySseMessages(createInitialRunState(), msgs)
    const series = state.metricSeries.get(metricKey('catalogue', 'cpu'))!

    expect(series.points.length).toBeLessThanOrEqual(METRIC_SERIES_CAP)
    // The whole window survives — dropping the oldest would lose the start.
    expect(series.points[0]!.ts).toBe(1700000001)
    expect(series.points.at(-1)!.ts).toBe(1700000000 + METRIC_SERIES_CAP + 1)
  })

  it('sorts on read, because cited-event backfill arrives out of ts order', () => {
    // app.py:190-198 flushes dropped-but-cited events AFTER the replay.
    const state = applySseMessages(createInitialRunState(), [metric(9, 0.9), metric(2, 0.2)])
    const [series] = selectSeriesFor(state, 'catalogue')
    expect(series!.points.map((p) => p.ts)).toEqual([1700000002, 1700000009])
  })

  it('builds series from the mock run', () => {
    const state = applySseMessages(createInitialRunState(), loadMockSseMessages(MOCK_RECORDINGS.redherring))
    const series = selectSeriesFor(state, 'catalogue')
    expect(series.map((s) => s.name).sort()).toEqual(['cpu', 'latency-90'])
    expect(series.find((s) => s.name === 'cpu')!.points).toHaveLength(3)
  })
})

describe('case window and timeline', () => {
  it('is null before anything arrives — no endpoint provides one', () => {
    expect(selectCaseWindow(createInitialRunState())).toBeNull()
  })

  it('spans the union of anomaly windows and event timestamps', () => {
    const state = applySseMessages(createInitialRunState(), loadMockSseMessages())
    const window = selectCaseWindow(state)!
    // Earliest event is the topology edge at t=1700000000; latest anomaly window
    // ends at 1700000069.
    expect(window.start).toBe(1700000000)
    expect(window.end).toBe(1700000069)
  })

  it('collects config changes for the timeline diamonds', () => {
    const state = applySseMessages(createInitialRunState(), loadMockSseMessages())
    const configs = selectConfigChanges(state)
    expect(configs.map((c) => c.component_id)).toEqual(['catalogue', 'catalogue-db'])
    // Sorted by ts: the gc.mode change precedes the max_connections push.
    expect(configs[0]!.ts).toBeLessThan(configs[1]!.ts)
  })

  it('tracks the playhead as the latest event time', () => {
    const state = applySseMessages(createInitialRunState(), loadMockSseMessages())
    expect(state.tsMax).toBe(1700000041)
  })
})

describe('sparkline downsampling', () => {
  const series = (n: number): MetricPoint[] =>
    Array.from({ length: n }, (_, i) => ({ ts: i, value: Math.sin(i / 10) }))

  it('leaves a short series alone', () => {
    expect(downsample(series(50))).toHaveLength(50)
  })

  it('never renders more than 200 points', () => {
    expect(downsample(series(20_000)).length).toBeLessThanOrEqual(MAX_POINTS)
  })

  it('keeps the first and last point', () => {
    const points = series(5_000)
    const out = downsample(points)
    expect(out[0]).toEqual(points[0])
    expect(out.at(-1)).toEqual(points.at(-1))
  })

  it('keeps a lone spike that a stride sampler would drop', () => {
    const points = series(2_000)
    points[977] = { ts: 977, value: 999 }
    const out = downsample(points)
    expect(out.some((p) => p.value === 999)).toBe(true)
  })

  it('stays in ts order', () => {
    const out = downsample(series(3_000))
    for (let i = 1; i < out.length; i += 1) {
      expect(out[i]!.ts).toBeGreaterThan(out[i - 1]!.ts)
    }
  })
})
