import { describe, expect, it, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { act } from 'react'

vi.mock('cytoscape', async () => {
  const { makeCytoscapeMock } = await import('./cytoscapeMock')
  return { default: makeCytoscapeMock() }
})

import { AgentsView } from '@/pages/AgentsView'
import { ReportView } from '@/pages/ReportView'
import { AGENT_BUDGET, costOf, parseBudgetTrip, spendFor } from '@/lib/budget'
import { tokenizeCitations, citationsIn } from '@/lib/citations'
import { instrumentRecommendation, TWIN_MATCH_THETA, TWIN_PARTIAL_THETA } from '@/lib/twin'
import { runStore, applySseMessages, createInitialRunState } from '@/store/runStore'
import { loadMockSseMessages, MOCK_RECORDINGS } from '@/mocks/mockStream'
import { mockApi } from '@/mocks'
import ledgerJson from '@/mocks/fixtures/sample_ledger.json'
import type { LedgerRecord } from '@/types/ledger'

function playRun(caseId: string, raw: string) {
  const { attach, dispatch } = runStore.getState()
  act(() => {
    attach(caseId)
    for (const msg of loadMockSseMessages(raw)) dispatch(msg)
  })
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 300))
  })
}

beforeEach(() => {
  runStore.getState().clear()
})

// ─────────────────────────────────────────────────────────────────────────────
// Budget — mirrored constants
// ─────────────────────────────────────────────────────────────────────────────

describe('budget accounting', () => {
  it('costs only the three expensive tools', () => {
    // backend/agents/tools.py:381-395
    expect(costOf('run_twin')).toBe(2)
    expect(costOf('run_counterfactual')).toBe(1)
    expect(costOf('rehearse_fix')).toBe(1)
    for (const free of ['get_anomalies', 'check_path', 'file_finding', 'get_events']) {
      expect(costOf(free)).toBe(0)
    }
    // The OFFLINE replay path emits tool: null.
    expect(costOf(null)).toBe(0)
  })

  it('reconstructs spend from the step stream', () => {
    const steps = [
      { tool: 'get_anomalies' },
      { tool: 'run_counterfactual' },
      { tool: 'run_twin' },
      { tool: 'file_finding' },
    ]
    expect(spendFor(steps)).toEqual({ calls: 4, points: 3 })
  })

  it('mirrors the investigator ceiling that autopilot_spend() computes', () => {
    // 5 counterfactuals × 1 + 1 twin × 2 = 7, so agent and autopilot may spend
    // exactly the same. Derived at import backend-side; a literal here.
    expect(AGENT_BUDGET.investigator.maxCostPoints).toBe(
      5 * costOf('run_counterfactual') + costOf('run_twin'),
    )
    // The challenger may spend nothing at all.
    expect(AGENT_BUDGET.challenger.maxCostPoints).toBe(0)
  })

  it('reads the limit out of a budget_exhausted summary — the one place it ships', () => {
    expect(parseBudgetTrip('budget exceeded: max_cost_points (limit=7, attempted=9)')).toEqual({
      reason: 'max_cost_points',
      limit: '7',
    })
    expect(parseBudgetTrip('something else entirely')).toBeNull()
    expect(parseBudgetTrip(null)).toBeNull()
  })

  it('fills the meter from the mock investigator run', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <AgentsView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    // The investigator ran 2 counterfactuals (1pt each) and 1 twin (2pts) = 4.
    expect(screen.getByText('4/7')).toBeInTheDocument()
    expect(screen.getByText(/Limits mirrored from investigator.py/)).toBeInTheDocument()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Transcript
// ─────────────────────────────────────────────────────────────────────────────

describe('agent transcript', () => {
  it('marks expensive calls with a SPENT badge and leaves free calls unmarked', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <AgentsView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    const twinRow = screen.getAllByTestId('transcript-row').find((r) => r.dataset.tool === 'run_twin')!
    expect(within(twinRow).getByText('SPENT 2/2 pts')).toBeInTheDocument()

    const freeRow = screen
      .getAllByTestId('transcript-row')
      .find((r) => r.dataset.tool === 'get_anomalies')!
    expect(within(freeRow).queryByText(/SPENT/)).not.toBeInTheDocument()
  })

  it('renders a filed fact inline — the only way an agent mutates anything', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <AgentsView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    const filed = screen
      .getAllByTestId('transcript-row')
      .find((r) => r.dataset.tool === 'file_finding')!
    expect(within(filed).getByText('filed to ledger')).toBeInTheDocument()
    expect(within(filed).getByText('fact-catalogue_db-0010')).toBeInTheDocument()
  })

  it('shows the agent_done banner with its status', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <AgentsView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()
    expect(screen.getAllByText('completed').length).toBeGreaterThan(0)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Challenger
// ─────────────────────────────────────────────────────────────────────────────

describe('challenger panel', () => {
  it('shows an upheld attack with its contradicting event', async () => {
    playRun('red_herring_config-01', MOCK_RECORDINGS.redherring)
    render(
      <MemoryRouter initialEntries={['/?agent=challenger']}>
        <AgentsView runId="red_herring_config-01" />
      </MemoryRouter>,
    )
    await settle()

    const card = await screen.findByTestId('attack-card')
    expect(within(card).getByText('UPHELD')).toBeInTheDocument()
    expect(within(card).getByText('The payment config push caused the 5xx surge.')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'metric-payment-000008' })).toBeInTheDocument()
    // The penalty is a real constant, but it rescales the breakdown rather than
    // subtracting a segment.
    expect(within(card).getByText(/−0.1 to score, breakdown rescaled/)).toBeInTheDocument()
  })

  it('says the challenger tried and failed, rather than showing nothing', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter initialEntries={['/?agent=challenger']}>
        <AgentsView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    expect(await screen.findByText('No attack survived validation.')).toBeInTheDocument()
    expect(screen.getByText(/Proposed 2 attacks against hyp-catalogue_db-01/)).toBeInTheDocument()
    // And it is explicit that dismissed attacks cannot be listed.
    expect(screen.getByText(/Rejected attacks are discarded by the backend/)).toBeInTheDocument()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Twin
// ─────────────────────────────────────────────────────────────────────────────

describe('twin compare', () => {
  it('mirrors the backend thresholds', () => {
    // backend/rank/constants.py:28-30
    expect(TWIN_MATCH_THETA).toBe(0.8)
    expect(TWIN_PARTIAL_THETA).toBe(0.5)
  })

  it('writes the instrument recommendation the backend computes and drops', () => {
    // compare.py:80 composes this string into `recommendations`, which twin()
    // never returns — missing_evidence is bare component ids.
    expect(instrumentRecommendation('orders-db')).toBe(
      'instrument orders-db to verify the simulated symptom',
    )
  })

  it('renders the verdict stamp and dial for the top hypothesis', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter initialEntries={['/?agent=twin']}>
        <AgentsView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    expect((await screen.findByTestId('twin-verdict')).textContent).toBe('match')
    expect(screen.getByText('0.86')).toBeInTheDocument()
    expect(screen.getByText('θ0.8')).toBeInTheDocument()
  })

  it('takes missing_evidence from the SSE event, which rescore empties on the verdict', async () => {
    // In agentic mode rescore rebuilds hypothesis.twin by regexing the ledger and
    // hardcodes missing_evidence: []. The twin_result event still carries it.
    const state = applySseMessages(createInitialRunState(), loadMockSseMessages(MOCK_RECORDINGS.happy))
    const hypothesisTwin = [...state.hypotheses.values()].find(
      (h) => h.suspect_component === 'catalogue-db',
    )!.twin!
    expect(hypothesisTwin.missing_evidence).toEqual([])
    expect(state.twinResults.get('hyp-catalogue_db-01')!.missing_evidence).toEqual(['orders-db'])

    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter initialEntries={['/?agent=twin']}>
        <AgentsView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    expect(
      await screen.findByText('instrument orders-db to verify the simulated symptom'),
    ).toBeInTheDocument()
  })

  it('says why there is no simulated column instead of faking one', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter initialEntries={['/?agent=twin']}>
        <AgentsView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()
    expect(await screen.findByText(/There is no simulated column/)).toBeInTheDocument()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Remediation
// ─────────────────────────────────────────────────────────────────────────────

describe('remediation panel', () => {
  it('crowns the recommendation and lists the alternatives', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter initialEntries={['/?agent=remediation']}>
        <AgentsView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    const cards = await screen.findAllByTestId('rehearsal-card')
    expect(cards.length).toBeGreaterThan(1)
    expect(within(cards[0]!).getByText('recommended')).toBeInTheDocument()
    expect(within(cards[0]!).getByText('revert max_connections 50->200 on catalogue-db')).toBeInTheDocument()
    expect(within(cards[0]!).getByText('83%')).toBeInTheDocument()
    // Side effects of an alternative are surfaced, not hidden. (Also appears in
    // the transcript's result_summary above, hence getAll.)
    expect(screen.getAllByText(/drops in-flight connections/).length).toBeGreaterThan(0)
  })

  it('renders an uncertain caveat verbatim, including the human-review case', async () => {
    const report = await mockApi.remediation('clean_cascade-01')
    expect(report).not.toBeNull()
    const { RemediationPanel } = await import('@/components/RemediationPanel')

    render(
      <RemediationPanel
        report={{
          ...report!,
          status: 'uncertain',
          recommended: null,
          // "uncertain" keeps EVERY rehearsal in alternatives, unlike "ok".
          alternatives: report!.rehearsals,
          caveat:
            'fix uncertain — no rehearsed remedy cleared more than 50% of the simulated symptoms; recommend human review (best: restart catalogue-db at 47%)',
        }}
      />,
    )

    const caveat = screen.getByTestId('remediation-caveat')
    expect(caveat).toHaveAttribute('role', 'alert')
    expect(caveat.textContent).toContain('recommend human review')
    expect(screen.queryByText('recommended')).not.toBeInTheDocument()
    expect(screen.getByText('Rehearsed, none recommended')).toBeInTheDocument()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Report + citations
// ─────────────────────────────────────────────────────────────────────────────

describe('citations', () => {
  it('matches fact ids only — the narrator cannot cite an event id', () => {
    expect(citationsIn('cleared [fact-catalogue_db-0008] and [fact-payment-0005]')).toEqual([
      'fact-catalogue_db-0008',
      'fact-payment-0005',
    ])
    // Its only tool is query_evidence_ledger, so it never sees an event id.
    expect(citationsIn('see [metric-catalogue-000001]')).toEqual([])
  })

  it('splits text around citations without losing any', () => {
    const tokens = tokenizeCitations('a [fact-x-0001] b [fact-y-0002]')
    expect(tokens).toEqual([
      { kind: 'text', value: 'a ' },
      { kind: 'citation', factId: 'fact-x-0001' },
      { kind: 'text', value: ' b ' },
      { kind: 'citation', factId: 'fact-y-0002' },
    ])
  })

  it('is not stateful across calls', () => {
    // A /g regex carries lastIndex; sharing one would drop every other match.
    const text = 'x [fact-a-0001] y'
    expect(tokenizeCitations(text)).toEqual(tokenizeCitations(text))
  })

  it('every citation in the mock narration resolves against the mock ledger', () => {
    const state = applySseMessages(createInitialRunState(), loadMockSseMessages(MOCK_RECORDINGS.happy))
    const ledger = new Set((ledgerJson as LedgerRecord[]).map((f) => f.fact_id))
    const cites = citationsIn(state.narration)
    expect(cites.length).toBeGreaterThan(0)
    expect(cites.filter((c) => !ledger.has(c))).toEqual([])
  })
})

describe('report view', () => {
  it('renders the narration markdown with resolvable citation chips', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <ReportView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    expect(screen.getByTestId('report-markdown')).toBeInTheDocument()
    // Markdown structure, not raw text.
    expect(screen.getByRole('heading', { name: 'Verdict' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'What we ruled out and why' })).toBeInTheDocument()

    const chip = screen.getAllByRole('button', { name: 'fact-catalogue_db-0008' })[0]!
    await userEvent.click(chip)
    const popover = await screen.findByRole('dialog', { name: /fact-catalogue_db-0008/ })
    expect(within(popover).getByText(/Removing catalogue-db leaves only 16.7%/)).toBeInTheDocument()
    expect(within(popover).getByText('counterfactual_result')).toBeInTheDocument()
  })

  it('warns when a citation does not resolve', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    act(() => {
      runStore.getState().dispatch({
        event: 'narration_chunk',
        data: { ts: 0, text: '\n\nA claim citing nothing real [fact-ghost-9999].\n' },
      })
    })
    render(
      <MemoryRouter>
        <ReportView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('does not resolve'),
    )
    expect(screen.getByRole('alert').textContent).toContain('fact-ghost-9999')
  })

  it('offers the PDF download', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <ReportView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()
    expect(screen.getByText('Download audit report (PDF)')).toBeInTheDocument()
  })
})
