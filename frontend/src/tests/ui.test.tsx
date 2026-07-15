/**
 * Render tests for the F2 checklist items that are otherwise only checkable by
 * eye: the demo buttons exist, the feed actually paints rows, the anomalies rail
 * populates, and the stage indicator advances.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { act } from 'react'

// App renders the topology graph, and cytoscape needs a canvas 2d context that
// jsdom does not have. The graph's own wiring is asserted in incident.test.tsx.
vi.mock('cytoscape', async () => {
  const { makeCytoscapeMock } = await import('./cytoscapeMock')
  return { default: makeCytoscapeMock() }
})

import App from '@/App'
import { Console } from '@/pages/Console'
import { LiveFeed } from '@/components/LiveFeed'
import { StatusBar } from '@/components/StatusBar'
import { runStore } from '@/store/runStore'
import { loadMockSseMessages, MOCK_RECORDINGS } from '@/mocks/mockStream'

function withRouter(ui: React.ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

/** Push the whole mock recording through the real store. */
function playMockRun(raw: string = MOCK_RECORDINGS.happy) {
  const { attach, dispatch } = runStore.getState()
  act(() => {
    attach('case-001')
    for (const msg of loadMockSseMessages(raw)) dispatch(msg)
  })
}

beforeEach(() => {
  runStore.getState().clear()
})

describe('Console', () => {
  it('shows a DEMO button per scenario, with the case it will fire', async () => {
    withRouter(<Console />)

    // /cases is async even in mock mode.
    const demo1 = await screen.findByText('DEMO 1')
    expect(demo1).toBeInTheDocument()

    for (let n = 1; n <= 7; n += 1) {
      expect(screen.getByText(`DEMO ${n}`)).toBeInTheDocument()
    }
    expect(screen.queryByText('DEMO 8')).not.toBeInTheDocument()

    // The label appears on the demo button and again on each matching case
    // card's badge, so scope the assertion to the button itself.
    const demoButton = demo1.closest('button')!
    expect(within(demoButton).getByText('Clean cascade')).toBeInTheDocument()
    expect(within(demoButton).getByText('clean_cascade-01')).toBeInTheDocument()
  })

  it('renders the case grid with kind badges and event counts', async () => {
    withRouter(<Console />)
    await screen.findByText('DEMO 1')

    expect(screen.getByText('27 total')).toBeInTheDocument()
    // A real case gets the real badge; scenario cases get synthetic.
    expect(screen.getAllByText('synthetic').length).toBeGreaterThan(0)
    expect(screen.getAllByText('real').length).toBe(2)
    // n_events is rendered, formatted.
    expect(screen.getByText('1,200')).toBeInTheDocument()
  })
})

describe('LiveFeed', () => {
  it('paints event rows and populates the anomalies rail', () => {
    playMockRun()
    withRouter(<LiveFeed />)

    const rail = screen.getByRole('complementary')
    expect(within(rail).getByText('Anomalies')).toBeInTheDocument()
    // All six fixture anomalies reach the rail.
    expect(within(rail).getByText('6')).toBeInTheDocument()
    expect(
      within(rail).getByText('catalogue-db p90 latency 812ms is 9.4 MAD above baseline.'),
    ).toBeInTheDocument()
    // Method chip, in presenter English rather than the raw enum.
    expect(within(rail).getByText('MAD z-score')).toBeInTheDocument()
    expect(within(rail).queryByText('mad_zscore')).not.toBeInTheDocument()
  })

  it('renders payload summaries in the feed, not raw JSON', () => {
    playMockRun()
    withRouter(<LiveFeed />)

    // The virtualizer must actually have painted rows — the config push is the
    // demo's smoking gun and has to be legible.
    expect(screen.getByText('max_connections: 200 → 50 · risky')).toBeInTheDocument()
    expect(screen.getByText('too many connections')).toBeInTheDocument()
    // Component chips, so a row says which component it came from.
    expect(screen.getAllByText('catalogue-db').length).toBeGreaterThan(0)
  })

  it('says so when nothing has arrived yet', () => {
    runStore.getState().attach('case-001')
    withRouter(<LiveFeed />)
    expect(screen.getByText('Waiting for the stream…')).toBeInTheDocument()
    expect(screen.getByText('None detected yet.')).toBeInTheDocument()
  })
})

describe('StatusBar', () => {
  it('advances the stage indicator and shows the run id', () => {
    playMockRun()
    withRouter(<StatusBar />)

    expect(screen.getByText('case-001')).toBeInTheDocument()
    expect(screen.getByText('done')).toBeInTheDocument()

    const stages = screen.getByRole('list', { name: 'pipeline stage' })
    for (const stage of ['DETECT', 'LOCALIZE', 'RANK', 'INVESTIGATE', 'VERIFY', 'NARRATE']) {
      expect(within(stages).getByText(stage)).toBeInTheDocument()
    }
  })

  it('shows the OFFLINE badge in mock mode', () => {
    withRouter(<StatusBar />)
    // VITE_MOCK is unset under vitest, so this is the API-unreachable path;
    // either way the badge must be an honest statement of what is missing.
    expect(screen.getByText(/^OFFLINE ·/)).toBeInTheDocument()
  })

  it('surfaces a failed pipeline rather than looking idle', () => {
    playMockRun(MOCK_RECORDINGS.error)
    withRouter(<StatusBar />)
    expect(screen.getByText('error')).toBeInTheDocument()
  })
})

/**
 * The whole app, driven by the real stream in mock mode — the closest thing to
 * the manual browser checklist that can run in CI.
 *
 * It also guards the bug that shipped in F1: passing an allocating selector to
 * useRunStore made useSyncExternalStore re-render forever ("Maximum update depth
 * exceeded"). Nothing caught it, because nothing rendered the app.
 */
describe('App end to end (mock mode)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const renderAt = (path: string) =>
    render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    )

  it('streams a run to completion without a single React error', async () => {
    const errors: unknown[] = []
    const spy = vi.spyOn(console, 'error').mockImplementation((...args) => errors.push(args[0]))

    renderAt('/run/case-001?view=incident')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })

    // The run reached the end and the stage indicator followed it there.
    expect(runStore.getState().status).toBe('done')
    expect(screen.getByText('done')).toBeInTheDocument()

    // The incident view came up with the graph and its timeline.
    expect(screen.getByTestId('topology-graph')).toBeInTheDocument()
    expect(screen.getByText(/derived from anomaly windows/)).toBeInTheDocument()
    // The rank-1 suspect is named, using the backend's tier.
    expect(screen.getByText(/suspect: catalogue-db · CONFIRMED/)).toBeInTheDocument()

    expect(errors).toEqual([])
    spy.mockRestore()
  })

  it('renders the view named in the URL, not a remembered click path', async () => {
    renderAt('/run/case-001?view=verdict')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })

    expect(screen.getByRole('tab', { name: 'Verdict' })).toHaveAttribute('data-state', 'active')
    // The verdict view is live as of F4; before any ranking it explains itself.
    expect(screen.getByText(/No hypotheses ranked yet|hypothes/i)).toBeInTheDocument()
  })

  it('falls back to the incident view for an unknown ?view', async () => {
    renderAt('/run/case-001?view=nonsense')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(screen.getByRole('tab', { name: 'Incident' })).toHaveAttribute('data-state', 'active')
  })
})
