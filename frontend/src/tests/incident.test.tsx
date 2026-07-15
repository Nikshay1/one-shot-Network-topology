/**
 * The graph's wiring: does the live run light the right nodes and edges?
 *
 * Cytoscape draws to a canvas jsdom can't provide, so the core is mocked and we
 * assert on the data it was told to paint. That is the whole of what
 * TopologyGraph decides; the rest is the stylesheet.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { act } from 'react'

vi.mock('cytoscape', async () => {
  const { makeCytoscapeMock } = await import('./cytoscapeMock')
  return { default: makeCytoscapeMock() }
})

import { IncidentView } from '@/pages/IncidentView'
import { Timeline } from '@/components/Timeline'
import { runStore } from '@/store/runStore'
import { loadMockSseMessages, MOCK_RECORDINGS } from '@/mocks/mockStream'
import { causalEdgeIds, lastCy, nodeData, resetCytoscapeMock } from './cytoscapeMock'

function playRun(caseId: string, raw: string) {
  const { attach, dispatch } = runStore.getState()
  act(() => {
    attach(caseId)
    for (const msg of loadMockSseMessages(raw)) dispatch(msg)
  })
}

/**
 * Let useTopology's async fetch land, THEN let the rAF paint run.
 *
 * Two separate act() calls on purpose: the graph is only built once the
 * topology resolves, and the paint it schedules is a frame later. Awaiting both
 * inside one act() flushes the effects together and the assertions land before
 * the paint frame.
 */
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 300))
  })
  await act(async () => {
    await new Promise((r) => requestAnimationFrame(() => r(null)))
  })
}

beforeEach(() => {
  runStore.getState().clear()
  resetCytoscapeMock()
})

describe('TopologyGraph paint', () => {
  it('renders the sock-shop topology', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <IncidentView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    expect(lastCy).not.toBeNull()
    expect(lastCy!._nodes).toHaveLength(15)
    expect(lastCy!._edges).toHaveLength(16)
  })

  it('lights anomalous amber, the rank-1 suspect red, and clears the redundant one', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <IncidentView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    // catalogue-db is the rank-1 suspect.
    expect(nodeData('catalogue-db')).toMatchObject({ suspect: true, anomalous: true })
    // catalogue has anomalies but is not the suspect.
    expect(nodeData('catalogue')).toMatchObject({ suspect: false, anomalous: true })
    // front-end's counterfactual came back redundant (100%) — cleared.
    expect(nodeData('front-end')).toMatchObject({ cleared: true })
    // payment was never implicated at all.
    expect(nodeData('payment')).toMatchObject({ suspect: false, anomalous: false, cleared: false })
  })

  it('draws the causal path along the real dependency chain', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <IncidentView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    // Failure runs catalogue-db -> catalogue -> front-end, so the CALL edges
    // front-end->catalogue and catalogue->catalogue-db are the ones lit.
    expect(causalEdgeIds().sort()).toEqual([
      'catalogue->catalogue-db',
      'front-end->catalogue',
    ])
  })

  it('shades the blast radius', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <IncidentView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    // blast_radius named catalogue-db -> [catalogue, front-end, orders].
    expect(nodeData('orders')).toMatchObject({ inBlast: true })
    expect(nodeData('user-db')).toMatchObject({ inBlast: false })
  })

  it('marks uninstrumented nodes hollow only for missing_telemetry', async () => {
    playRun('missing_telemetry-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <IncidentView runId="missing_telemetry-01" />
      </MemoryRouter>,
    )
    await settle()

    expect(nodeData('carts-db')).toMatchObject({ uninstrumented: true })
    expect(nodeData('catalogue')).toMatchObject({ uninstrumented: false })
  })

  it('treats a real case with no `instrumented` key as fully instrumented', async () => {
    playRun('catalogue_cpu-1', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <IncidentView runId="catalogue_cpu-1" />
      </MemoryRouter>,
    )
    await settle()

    // The regression this guards: reading the absent field as false would render
    // every node of every real case hollow.
    expect(lastCy!._nodes.every((n) => n._data.uninstrumented === false)).toBe(true)
  })

  it('clears the red herring and reds the real cause', async () => {
    playRun('red_herring_config-01', MOCK_RECORDINGS.redherring)
    render(
      <MemoryRouter>
        <IncidentView runId="red_herring_config-01" />
      </MemoryRouter>,
    )
    await settle()

    expect(nodeData('catalogue')).toMatchObject({ suspect: true })
    expect(nodeData('payment')).toMatchObject({ cleared: true, suspect: false })
  })
})

describe('component drawer', () => {
  it('opens from the URL and shows anomalies, metrics and ledger facts', async () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter initialEntries={['/run/clean_cascade-01?component=catalogue-db']}>
        <IncidentView runId="clean_cascade-01" />
      </MemoryRouter>,
    )
    await settle()

    const drawer = await screen.findByLabelText('details for catalogue-db')
    expect(within(drawer).getByText(/2 anomalies/)).toBeInTheDocument()
    expect(
      within(drawer).getByText('catalogue-db p90 latency 812ms is 9.4 MAD above baseline.'),
    ).toBeInTheDocument()
    // Metric series accumulated from the SSE stream.
    expect(within(drawer).getByText(/latency-90/)).toBeInTheDocument()
    // Ledger facts fetched for this component.
    expect(await within(drawer).findByText(/anomaly_observed/)).toBeInTheDocument()
  })

  it('explains an uninstrumented component instead of implying it is healthy', async () => {
    playRun('missing_telemetry-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter initialEntries={['/run/missing_telemetry-01?component=carts-db']}>
        <IncidentView runId="missing_telemetry-01" />
      </MemoryRouter>,
    )
    await settle()

    const drawer = await screen.findByLabelText('details for carts-db')
    expect(within(drawer).getByText('uninstrumented')).toBeInTheDocument()
    expect(within(drawer).getByText(/evidence of nothing/)).toBeInTheDocument()
  })
})

describe('Timeline', () => {
  it('waits for events rather than inventing a window', () => {
    render(
      <MemoryRouter>
        <Timeline onSelectComponent={() => {}} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/Timeline appears once events arrive/)).toBeInTheDocument()
  })

  it('renders clickable config diamonds and the derived window', () => {
    playRun('clean_cascade-01', MOCK_RECORDINGS.happy)
    render(
      <MemoryRouter>
        <Timeline onSelectComponent={() => {}} />
      </MemoryRouter>,
    )

    expect(screen.getByText(/derived from anomaly windows/)).toBeInTheDocument()
    expect(
      screen.getByLabelText('config change on catalogue-db: max_connections: 200 → 50 · risky'),
    ).toBeInTheDocument()
  })
})
