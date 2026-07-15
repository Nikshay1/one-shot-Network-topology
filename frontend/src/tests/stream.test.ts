import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openMockRunStream } from '@/mocks/mockStream'
import { mockApi } from '@/mocks'
import { nextBackoffMs, DEFAULT_BACKOFF } from '@/api/stream'
import { applySseMessages, createInitialRunState, selectRankedHypotheses } from '@/store/runStore'
import type { SseMessage } from '@/types/api'
import type { StreamStatus } from '@/api/stream'

describe('mock stream player', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  interface Capture {
    messages: SseMessage[]
    statuses: StreamStatus[]
    unknown: string[]
    opens: number
  }

  function run(runId: string) {
    const cap: Capture = { messages: [], statuses: [], unknown: [], opens: 0 }
    const handle = openMockRunStream({
      runId,
      onMessage: (m) => cap.messages.push(m),
      onStatus: (s) => cap.statuses.push(s),
      onUnknown: (n) => cap.unknown.push(n),
      onOpen: () => (cap.opens += 1),
    })
    return { cap, handle }
  }

  it('replays the happy path into a finished run and closes itself', async () => {
    const { cap, handle } = run('case-001')
    await vi.advanceTimersByTimeAsync(60_000)

    expect(handle.status()).toBe('done')
    expect(cap.opens).toBe(1)
    expect(cap.statuses[0]).toBe('open')
    expect(cap.statuses.at(-1)).toBe('done')

    // The unknown event type is reported but never dispatched.
    expect(cap.unknown).toEqual(['causal_graph_ready'])
    expect(cap.messages.some((m) => (m.event as string) === 'causal_graph_ready')).toBe(false)

    const state = applySseMessages(createInitialRunState(), cap.messages)
    expect(state.status).toBe('done')
    expect(selectRankedHypotheses(state)[0]!.suspect_component).toBe('catalogue-db')
    expect(state.unknownEvents).toBe(0)
  })

  it('stops delivering after a terminal event even if time keeps passing', async () => {
    const { cap } = run('case-001')
    await vi.advanceTimersByTimeAsync(60_000)
    const settled = cap.messages.length

    await vi.advanceTimersByTimeAsync(60_000)
    expect(cap.messages.length).toBe(settled)
  })

  it('replays the failure path for a -error runId', async () => {
    const { cap, handle } = run('case-001-error')
    await vi.advanceTimersByTimeAsync(60_000)

    expect(handle.status()).toBe('error')
    expect(cap.messages.at(-1)!.event).toBe('pipeline_error')
  })

  it('close() halts replay mid-flight', async () => {
    const { cap, handle } = run('case-001')
    await vi.advanceTimersByTimeAsync(500)
    const delivered = cap.messages.length
    expect(delivered).toBeGreaterThan(0)

    handle.close()
    await vi.advanceTimersByTimeAsync(60_000)

    expect(cap.messages.length).toBe(delivered)
    expect(handle.status()).toBe('closed')
    expect(cap.statuses.at(-1)).toBe('closed')
  })

  it('paces the replay inside the 5–20 msg/s band', async () => {
    const { cap } = run('case-001')
    // 1s of replay should land within [5, 20] messages, allowing for the
    // connect delay and jitter.
    await vi.advanceTimersByTimeAsync(1_000)
    expect(cap.messages.length).toBeGreaterThanOrEqual(5)
    expect(cap.messages.length).toBeLessThanOrEqual(20)
  })
})

describe('reconnect backoff', () => {
  it('grows exponentially and caps', () => {
    const attempts = [0, 1, 2, 3, 10].map((n) => nextBackoffMs(n))
    expect(attempts[0]).toBeGreaterThanOrEqual(DEFAULT_BACKOFF.initialMs)
    expect(attempts[1]!).toBeGreaterThan(attempts[0]!)
    expect(attempts[2]!).toBeGreaterThan(attempts[1]!)
    // Capped (plus jitter) rather than growing without bound.
    const ceiling = DEFAULT_BACKOFF.maxMs * (1 + DEFAULT_BACKOFF.jitter)
    for (const ms of attempts) expect(ms).toBeLessThanOrEqual(ceiling)
  })
})

describe('mock api', () => {
  it('serves cases, topology and a done verdict', async () => {
    const cases = await mockApi.cases()
    expect(cases.length).toBeGreaterThan(0)
    // Exactly the four fields /cases returns — no invented kind/system/duration.
    expect(Object.keys(cases[0]!).sort()).toEqual([
      'case_id',
      'n_components',
      'n_events',
      'title',
    ])

    const topo = await mockApi.topology('clean_cascade-01')
    expect(topo.directed).toBe(true)
    expect(topo.nodes.length).toBe(15)

    const verdict = await mockApi.verdict('case-001')
    expect(verdict.done).toBe(true)
    expect(verdict.hypotheses).toHaveLength(3)
  })

  it('filters the ledger the way the query params do', async () => {
    const all = await mockApi.ledger({})
    const byHyp = await mockApi.ledger({ hypothesis_id: 'hyp-catalogue_db-01' })
    const byComponent = await mockApi.ledger({ component_id: 'catalogue-db' })

    expect(byHyp.length).toBeGreaterThan(0)
    expect(byHyp.length).toBeLessThan(all.length)
    expect(byHyp.every((r) => r.hypothesis_id === 'hyp-catalogue_db-01')).toBe(true)
    expect(byComponent.every((r) => r.component_ids.includes('catalogue-db'))).toBe(true)
  })

  it('parses transcripts from NDJSON, ending in a single result line', async () => {
    const lines = (await mockApi.transcript('investigator'))!
    expect(lines.length).toBeGreaterThan(1)
    expect(lines.filter((l) => l.type === 'result')).toHaveLength(1)
    expect(lines.at(-1)!.type).toBe('result')

    // A transcript can legitimately end in budget_exhausted with a null final_text.
    const challenger = (await mockApi.transcript('challenger'))!
    const last = challenger.at(-1)!
    expect(last.type === 'result' && last.status).toBe('budget_exhausted')
    expect(last.type === 'result' && last.final_text).toBeNull()
  })

  it('serves a benchmark with ground truth redacted', async () => {
    const bench = await mockApi.benchmark()
    expect(bench.redacted).toEqual(['truth', 'rank_of_truth', 'false_blame'])
    for (const row of bench.runs) {
      expect(row).not.toHaveProperty('truth')
      expect(row).not.toHaveProperty('rank_of_truth')
      expect(row).not.toHaveProperty('false_blame')
    }
    // Aggregate metrics survive redaction by design.
    expect(bench.metrics['synthetic:agentic']!['precision@1']).toBeGreaterThan(0)
  })

  it('narration matches the streamed chunks', async () => {
    const { chunks } = await mockApi.narration('case-001')
    expect(chunks.length).toBeGreaterThan(0)
    // Chunks are paragraphs, rejoined with the blank line the backend split on.
    expect(chunks.map((c) => c.text).join('\n\n').startsWith('# Incident report')).toBe(true)
    // No chunk carries a blank line — that is the invariant of split("\n\n").
    expect(chunks.every((c) => !c.text.includes('\n\n'))).toBe(true)
  })

  it('does not hand out the fixture object itself', async () => {
    const a = await mockApi.cases()
    a[0]!.title = 'mutated'
    const b = await mockApi.cases()
    expect(b[0]!.title).not.toBe('mutated')
  })
})
