import { describe, expect, it, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { act } from 'react'
import { VerdictView } from '@/pages/VerdictView'
import { sumBreakdown } from '@/components/ScoreBreakdown'
import { buildEvidence } from '@/lib/evidence'
import {
  applySseMessages,
  createInitialRunState,
  runStore,
  selectRankedHypotheses,
} from '@/store/runStore'
import { loadMockSseMessages, MOCK_RECORDINGS } from '@/mocks/mockStream'
import type { RankedHypothesis } from '@/types/hypothesis'
import type { AnomalyEvent } from '@/types/anomaly'
import type { LedgerRecord } from '@/types/ledger'

function playRun(caseId: string, raw: string) {
  const { attach, dispatch } = runStore.getState()
  act(() => {
    attach(caseId)
    for (const msg of loadMockSseMessages(raw)) dispatch(msg)
  })
}

const view = (runId: string) =>
  render(
    <MemoryRouter>
      <VerdictView runId={runId} />
    </MemoryRouter>,
  )

beforeEach(() => {
  runStore.getState().clear()
})

// ─────────────────────────────────────────────────────────────────────────────
// The schema's arithmetic invariant
// ─────────────────────────────────────────────────────────────────────────────

describe('score breakdown', () => {
  it('every mock hypothesis satisfies Σ(terms) === score', () => {
    // The schema says score_breakdown values are pre-weighted contributions that
    // MUST sum to `score` — enforced in backend code, not expressible in JSON
    // Schema. If a recording violates it, the UI is rendering nonsense.
    for (const raw of Object.values(MOCK_RECORDINGS)) {
      for (const msg of loadMockSseMessages(raw)) {
        if (msg.event !== 'hypothesis_ranked') continue
        const h = msg.data
        expect(sumBreakdown(h.score_breakdown), h.hypothesis_id).toBeCloseTo(h.score, 2)
      }
    }
  })

  it('renders the backend score, and flags a broken invariant instead of hiding it', () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    view('clean_cascade-01')
    // 0.30 + 0.12 + 0.10 + 0.08 + 0.10 = 0.70
    expect(screen.getAllByText(/Σ = 0.70 = score 0.70/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/invariant broken/)).not.toBeInTheDocument()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Ranking and re-ranking
// ─────────────────────────────────────────────────────────────────────────────

describe('hypothesis list', () => {
  it('renders in backend rank order', () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    view('clean_cascade-01')

    const cards = screen.getAllByTestId('hypothesis-card')
    expect(cards.map((c) => c.dataset.rank)).toEqual(['1', '2', '3'])
    expect(cards[0]!.dataset.hypothesisId).toBe('hyp-catalogue_db-01')
  })

  it('re-orders when a rescore demotes the red herring — the demo moment', () => {
    // Play only up to the first ranking: payment leads on precedence alone.
    const msgs = loadMockSseMessages(MOCK_RECORDINGS.redherring)
    const firstRankEnd = msgs.findIndex((m) => m.event === 'agent_step')
    const { attach, dispatch } = runStore.getState()
    act(() => {
      attach('red_herring_config-01')
      for (const m of msgs.slice(0, firstRankEnd)) dispatch(m)
    })

    view('red_herring_config-01')
    expect(screen.getAllByTestId('hypothesis-card')[0]!.dataset.hypothesisId).toBe('hyp-payment-01')

    // Now the rest of the run: the counterfactual demotes payment.
    act(() => {
      for (const m of msgs.slice(firstRankEnd)) dispatch(m)
    })

    const cards = screen.getAllByTestId('hypothesis-card')
    expect(cards[0]!.dataset.hypothesisId).toBe('hyp-catalogue-02')
    expect(cards[1]!.dataset.hypothesisId).toBe('hyp-payment-01')
  })

  it('shows the tier and the backend tier_reason verbatim', () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    view('clean_cascade-01')

    const top = screen.getAllByTestId('hypothesis-card')[0]!
    expect(within(top).getByTestId('tier-pill').dataset.tier).toBe('CONFIRMED')
    expect(
      within(top).getByText(
        'Config change precedes all symptoms; metric, log, and alert modalities corroborate on the same component.',
      ),
    ).toBeInTheDocument()
  })

  it('says why there is no verdict rather than showing an empty list', () => {
    playRun('case-001-error', MOCK_RECORDINGS.error)
    view('case-001-error')
    expect(screen.getByText(/The run failed at stage pipeline/)).toBeInTheDocument()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Evidence columns
// ─────────────────────────────────────────────────────────────────────────────

describe('evidence', () => {
  const state = applySseMessages(createInitialRunState(), loadMockSseMessages(MOCK_RECORDINGS.happy))
  const ranked = selectRankedHypotheses(state)
  const anomalies = [...state.anomalies.values()]

  it('puts a matching twin and a load-bearing counterfactual under Confirmed', () => {
    const { confirmed } = buildEvidence({ hypothesis: ranked[0]!, anomalies })
    expect(confirmed.some((i) => i.text.includes('Twin reproduced the fault'))).toBe(true)
    expect(confirmed.some((i) => i.text.includes('load-bearing'))).toBe(true)
  })

  it('does NOT file a load-bearing counterfactual under "ruled out"', () => {
    // narrator.py's _EXONERATING_KINDS contains counterfactual_result, but that
    // kind is written for BOTH outcomes. Bucketing by kind would file the
    // strongest incriminating evidence in the system as exonerating.
    const top = ranked[0]!
    expect(top.counterfactual).toEqual({ removed: true, anomalies_still_explained_pct: 16.7 })
    const { missing } = buildEvidence({ hypothesis: top, anomalies })
    expect(missing.some((i) => i.text.includes('load-bearing'))).toBe(false)
  })

  it('puts a redundant counterfactual under Missing', () => {
    const frontEnd = ranked.find((h) => h.suspect_component === 'front-end')!
    const { missing } = buildEvidence({ hypothesis: frontEnd, anomalies })
    expect(missing.some((i) => i.text.includes('redundant, not load-bearing'))).toBe(true)
  })

  it('never treats an untested counterfactual proxy as evidence', () => {
    const proxy: RankedHypothesis = {
      ...ranked[0]!,
      counterfactual: { removed: false, anomalies_still_explained_pct: 100 },
    }
    const { missing, confirmed } = buildEvidence({ hypothesis: proxy, anomalies })
    expect(missing.some((i) => i.text.includes('never tested by removal'))).toBe(true)
    expect(confirmed.some((i) => i.text.includes('load-bearing'))).toBe(false)
    expect(missing.some((i) => i.text.includes('redundant'))).toBe(false)
  })

  it('treats observed:null as unobservable, not as absent', () => {
    const h: RankedHypothesis = {
      ...ranked[0]!,
      predicted_symptoms: [
        { component_id: 'carts-db', expectation: 'write errors', observed: null },
        { component_id: 'orders', expectation: 'tail latency', observed: false },
      ],
    }
    const { missing, correlated } = buildEvidence({ hypothesis: h, anomalies })
    expect(missing.some((i) => i.text.includes('carts-db is uninstrumented'))).toBe(true)
    // observed:false on an instrumented component IS a real signal, not a gap.
    expect(correlated.some((i) => i.text.includes('absence is real'))).toBe(true)
  })

  it('surfaces twin.missing_evidence verbatim when the hypothesis has any', () => {
    // Note what the recording models: in agentic mode rescore rebuilds the twin
    // block from the ledger text and hardcodes missing_evidence to [], so the
    // hypothesis carries none even though the twin found some. The twin_result
    // SSE event keeps them — see the Twin tab. Here we prove the pass-through
    // with an explicit value.
    const h: RankedHypothesis = {
      ...ranked[0]!,
      twin: { run: 'twin-catalogue-db', similarity: 0.86, verdict: 'match', missing_evidence: ['orders-db'] },
    }
    const { missing } = buildEvidence({ hypothesis: h, anomalies })
    expect(missing.some((i) => i.text === 'orders-db')).toBe(true)

    const asShipped = ranked.find((x) => x.suspect_component === 'catalogue')!
    expect(asShipped.twin!.missing_evidence).toEqual([])
  })

  it('joins coverage_gap facts on component, since they carry no hypothesis_id', () => {
    const gap: LedgerRecord = {
      fact_id: 'fact-orders-0099',
      case_id: 'case-001',
      kind: 'coverage_gap',
      statement: 'orders is uninstrumented; predicted symptom of catalogue-db cannot be observed.',
      component_ids: ['orders'],
      event_ids: [],
      modality: 'derived',
      ts_range: { start: 0, end: 0 },
      confidence: 1,
      hypothesis_id: null, // tiers.py accepts an id and never passes it
    }
    const { missing } = buildEvidence({
      hypothesis: ranked[0]!, // predicts a symptom on orders
      anomalies,
      coverageGaps: [gap, { ...gap, fact_id: 'fact-orders-0100' }], // duplicates expected
    })
    expect(missing.filter((i) => i.text === gap.statement)).toHaveLength(1)
  })

  it('ignores a coverage_gap for a component this hypothesis says nothing about', () => {
    const gap: LedgerRecord = {
      fact_id: 'fact-rabbitmq-0098',
      case_id: 'case-001',
      kind: 'coverage_gap',
      statement: 'rabbitmq is uninstrumented; predicted symptom of X cannot be observed.',
      component_ids: ['rabbitmq'],
      event_ids: [],
      modality: 'derived',
      ts_range: { start: 0, end: 0 },
      confidence: 1,
      hypothesis_id: null,
    }
    const { missing } = buildEvidence({ hypothesis: ranked[0]!, anomalies, coverageGaps: [gap] })
    expect(missing.some((i) => i.text === gap.statement)).toBe(false)
  })

  it('records an upheld challenger attack as a blocker', () => {
    const none: AnomalyEvent[] = []
    const upheld: RankedHypothesis = {
      ...ranked[0]!,
      challenger: {
        attacks: [
          { claim: 'it was the CPU', contradicting_event_id: 'metric-catalogue-000001', upheld: true },
        ],
      },
    }
    expect(
      buildEvidence({ hypothesis: upheld, anomalies: none }).missing.some((i) =>
        i.text.includes('UPHELD'),
      ),
    ).toBe(true)
  })

  it('still renders the upheld flag rather than assuming it', () => {
    // The backend discards rejected attacks before they reach the wire, so this
    // branch is unreachable in production — but the contract types `upheld` as a
    // bool, and rendering the flag we were given beats assuming it.
    const rejected: RankedHypothesis = {
      ...ranked[0]!,
      challenger: {
        attacks: [
          { claim: 'it was the CPU', contradicting_event_id: 'metric-catalogue-000001', upheld: false },
        ],
      },
    }
    expect(
      buildEvidence({ hypothesis: rejected, anomalies: [] }).confirmed.some((i) =>
        i.text.includes('rejected'),
      ),
    ).toBe(true)
  })

  it('renders the three columns', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    view('clean_cascade-01')

    const top = screen.getAllByTestId('hypothesis-card')[0]!
    await userEvent.click(within(top).getByRole('button', { name: 'Show evidence' }))

    expect(within(top).getByText('Confirmed evidence')).toBeInTheDocument()
    expect(within(top).getByText('Correlated signals')).toBeInTheDocument()
    expect(within(top).getByText('Missing evidence')).toBeInTheDocument()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Event chips
// ─────────────────────────────────────────────────────────────────────────────

describe('event chip popover', () => {
  it('opens the raw event pinned from the stream', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    view('clean_cascade-01')

    const top = screen.getAllByTestId('hypothesis-card')[0]!
    await userEvent.click(within(top).getByRole('button', { name: 'config-catalogue_db-000001' }))

    const popover = await screen.findByRole('dialog', { name: /config-catalogue_db-000001/ })
    expect(within(popover).getByText(/max_connections/)).toBeInTheDocument()
  })

  it('pins cited events so they survive the ring buffer', () => {
    // There is no endpoint to fetch an event by id, so a chip can only show what
    // the stream gave us — pinning is what makes it work at all.
    expect(state.pinnedEvents.has('config-catalogue_db-000001')).toBe(true)
    expect(state.pinnedEvents.has('metric-catalogue_db-000002')).toBe(true)
  })

  const state = applySseMessages(createInitialRunState(), loadMockSseMessages(MOCK_RECORDINGS.happy))

  it('says so when an event was never seen, rather than spinning', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    act(() => {
      runStore.getState().dispatch({
        event: 'hypothesis_ranked',
        data: {
          ...selectRankedHypotheses(runStore.getState())[0]!,
          hypothesis_id: 'hyp-ghost-09',
          rank: 9,
          cited_evidence_ids: ['metric-ghost-999999'],
          trigger_event_id: 'metric-ghost-999999',
        },
      })
    })
    view('clean_cascade-01')

    const card = screen.getAllByTestId('hypothesis-card').find((c) => c.dataset.rank === '9')!
    await userEvent.click(within(card).getByRole('button', { name: 'metric-ghost-999999' }))

    expect(await screen.findByText(/isn't held locally/)).toBeInTheDocument()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Counterfactual toggle
// ─────────────────────────────────────────────────────────────────────────────

describe('counterfactual toggle', () => {
  it('is disabled until the run completes', () => {
    const msgs = loadMockSseMessages(MOCK_RECORDINGS.happy)
    const { attach, dispatch } = runStore.getState()
    act(() => {
      attach('clean_cascade-01')
      for (const m of msgs.filter((x) => x.event !== 'pipeline_done')) dispatch(m)
    })
    view('clean_cascade-01')

    // Mid-run the endpoint reads rec.verdict (empty) and answers differently.
    expect(screen.getByRole('switch')).toBeDisabled()
    expect(screen.getByText('available once the run completes')).toBeInTheDocument()
  })

  it('shows the still-explained % side by side, and undo restores', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    view('clean_cascade-01')

    const toggle = screen.getByRole('switch')
    expect(toggle).toBeEnabled()
    await userEvent.click(toggle)

    await waitFor(() => expect(screen.getByText('16.7% still explained')).toBeInTheDocument())
    expect(screen.getByText('With')).toBeInTheDocument()
    expect(screen.getByText(/only 16.7% of the anomalies are still explained/)).toBeInTheDocument()

    await userEvent.click(toggle)
    await waitFor(() => expect(screen.queryByText('16.7% still explained')).not.toBeInTheDocument())
  })

  it('does not claim a re-ranked verdict, because the endpoint returns none', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    view('clean_cascade-01')

    await userEvent.click(screen.getByRole('switch'))
    await waitFor(() => expect(screen.getByText('16.7% still explained')).toBeInTheDocument())
    expect(screen.getByText(/ranks and scores above are unchanged on both sides/)).toBeInTheDocument()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Impact badge
// ─────────────────────────────────────────────────────────────────────────────

describe('impact badge', () => {
  it('reports the blast radius as a run-level union, not per component', () => {
    // `radius` is len(affected) and `affected` is the same global set for every
    // component (app.py:203-211), so a per-component N would print the same
    // number everywhere. The union is the honest reading.
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    view('clean_cascade-01')
    expect(screen.getByText('4 services in blast radius')).toBeInTheDocument()
  })

  it('never claims a session count — the API has no such concept', () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    view('clean_cascade-01')
    expect(screen.queryByText(/session/i)).not.toBeInTheDocument()
  })

  it('renders nothing before any blast_radius arrives', () => {
    act(() => {
      runStore.getState().attach('clean_cascade-01')
    })
    view('clean_cascade-01')
    expect(screen.queryByText(/blast radius/)).not.toBeInTheDocument()
  })
})
