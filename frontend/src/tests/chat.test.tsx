/**
 * The chat tab.
 *
 * The nav swap is tested here because nothing tested the nav at all: Benchmark was
 * removed from it and all 169 tests stayed green, which means the header was
 * unverified. /benchmark is still routed, and that is asserted too — "unlinked"
 * must not quietly become "deleted".
 */
import { describe, expect, it, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { act } from 'react'

vi.mock('cytoscape', async () => {
  const { makeCytoscapeMock } = await import('./cytoscapeMock')
  return { default: makeCytoscapeMock() }
})

import App from '@/App'
import { ChatView } from '@/pages/ChatView'
import { runStore } from '@/store/runStore'
import { mockApi } from '@/mocks'

function withRouter(ui: React.ReactNode, path = '/') {
  return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>)
}

beforeEach(() => {
  runStore.getState().clear()
})

// mockApi is a module singleton and a `...Once` impl outlives a test that never
// consumed it — an unrestored spy then answers a LATER test's call, which is how a
// rejected value from one test surfaced as a failure three tests away.
afterEach(() => {
  vi.restoreAllMocks()
})

describe('the nav', () => {
  it('links Chat and no longer links Benchmark', () => {
    withRouter(<App />)
    const nav = screen.getByRole('navigation')
    expect(nav).toHaveTextContent('Chat')
    expect(nav).not.toHaveTextContent('Benchmark')
  })

  it('still routes /benchmark — unlinked is not deleted', async () => {
    withRouter(<App />, '/benchmark')
    // Its own <h1>. The catch-all redirects unknown paths to Console, so rendering
    // this proves the route still resolves rather than falling through.
    expect(await screen.findByRole('heading', { name: 'Benchmark', level: 1 })).toBeTruthy()
  })
})

describe('ChatView', () => {
  it('explains itself instead of 404ing when there is no run', () => {
    withRouter(<ChatView />)
    expect(screen.getByText(/Nothing to talk about yet/)).toBeTruthy()
    expect(screen.getByRole('link', { name: /Pick a case/ })).toBeTruthy()
  })

  it('answers a question with citation chips for ids that resolve', async () => {
    act(() => {
      runStore.getState().attach('clean_cascade-01')
    })
    const user = userEvent.setup()
    withRouter(<ChatView />)

    await user.click(screen.getByText('What should I do to fix this?'))

    await waitFor(() => expect(screen.queryByText(/retrieving evidence/)).toBeNull(), {
      timeout: 3000,
    })
    // A fact id from the ledger fixture, rendered as a chip rather than raw text.
    const chips = await screen.findAllByTitle(/fact-.*evidence ledger/)
    expect(chips.length).toBeGreaterThan(0)
  })

  it('shows the mode, because a 200 is not proof a model spoke', async () => {
    act(() => {
      runStore.getState().attach('clean_cascade-01')
    })
    const user = userEvent.setup()
    withRouter(<ChatView />)
    await user.click(screen.getByText('What should I do to fix this?'))

    // Mock mode has no model — the honest badge is `deterministic`, and hiding it
    // would let a demo imply an LLM answered when none did.
    const badge = await screen.findByTitle(/No model answered/)
    expect(badge).toHaveTextContent('deterministic')
  })

  it('surfaces stripped claims rather than silently showing a shorter answer', async () => {
    act(() => {
      runStore.getState().attach('clean_cascade-01')
    })
    vi.spyOn(mockApi, 'chat').mockResolvedValueOnce({
      answer: 'catalogue is the suspect [fact-catalogue-0002].',
      mode: 'llm',
      citations: ['fact-catalogue-0002'],
      stripped: ['fact-dns-9999'],
      citations_valid: false,
      retrieved: [],
      usd: 0.0003,
      attempts: 2,
    })

    const user = userEvent.setup()
    withRouter(<ChatView />)
    await user.click(screen.getByText('What should I do to fix this?'))

    expect(await screen.findByText(/1 claim\(s\) stripped/)).toBeTruthy()
  })

  it('says the run has no verdict yet rather than showing a raw error', async () => {
    act(() => {
      runStore.getState().attach('clean_cascade-01')
    })
    const { ApiError } = await import('@/api/client')
    vi.spyOn(mockApi, 'chat').mockRejectedValueOnce(
      new ApiError(404, { error: 'run has not produced a verdict yet' }),
    )

    const user = userEvent.setup()
    withRouter(<ChatView />)
    await user.click(screen.getByText('What should I do to fix this?'))

    expect(await screen.findByText(/has not produced a verdict yet/)).toBeTruthy()
  })
})

describe('the mock chat backend', () => {
  it('only ever cites fact ids the ledger fixture really holds', async () => {
    const res = await mockApi.chat({ question: 'what is the evidence for catalogue?' })
    const ledger = await mockApi.ledger({})
    const known = new Set(ledger.map((r) => r.fact_id))
    for (const id of res.citations) expect(known.has(id)).toBe(true)
  })

  it('answers deterministically — the state a demo with no key is actually in', async () => {
    const res = await mockApi.chat({ question: 'what should I do?' })
    expect(res.mode).toBe('deterministic')
    expect(res.usd).toBe(0)
  })
})
