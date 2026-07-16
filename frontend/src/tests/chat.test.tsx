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
  it('lists the finished cases and selects one to talk to', async () => {
    withRouter(<ChatView />)
    // the picker is populated from GET /runs, not from the live run store
    expect(await screen.findByText(/1 finished case/)).toBeTruthy()
    const picker = await screen.findByRole('button', { name: /case-001/ })
    expect(picker).toHaveAttribute('aria-current', 'true') // auto-selected
    expect(await screen.findByText(/Grounded in/)).toBeTruthy()
  })

  it('says so plainly when nothing has finished yet', async () => {
    vi.spyOn(mockApi, 'runs').mockResolvedValueOnce([])
    withRouter(<ChatView />)
    expect(await screen.findByText(/No finished cases yet/)).toBeTruthy()
    expect(screen.getByRole('link', { name: /Pick a case/ })).toBeTruthy()
  })

  it('answers a question with citation chips for ids that resolve', async () => {
    const user = userEvent.setup()
    withRouter(<ChatView />)
    await user.click(await screen.findByText('What should I do to fix this?'))

    await waitFor(() => expect(screen.queryByText(/retrieving evidence/)).toBeNull(), {
      timeout: 3000,
    })
    // A fact id from the ledger fixture, rendered as a chip rather than raw text.
    const chips = await screen.findAllByTitle(/fact-.*evidence ledger/)
    expect(chips.length).toBeGreaterThan(0)
  })

  it('shows the mode, because a 200 is not proof a model spoke', async () => {
    const user = userEvent.setup()
    withRouter(<ChatView />)
    await user.click(await screen.findByText('What should I do to fix this?'))

    // Mock mode has no model — the honest badge is `deterministic`, and hiding it
    // would let a demo imply an LLM answered when none did.
    const badge = await screen.findByTitle(/No model answered/)
    expect(badge).toHaveTextContent('deterministic')
  })

  it('surfaces stripped claims rather than silently showing a shorter answer', async () => {
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
    await user.click(await screen.findByText('What should I do to fix this?'))

    expect(await screen.findByText(/1 claim\(s\) stripped/)).toBeTruthy()
  })

  it('says the run has no verdict yet rather than showing a raw error', async () => {
    const { ApiError } = await import('@/api/client')
    vi.spyOn(mockApi, 'chat').mockRejectedValueOnce(
      new ApiError(404, { error: 'run has not produced a verdict yet' }),
    )

    const user = userEvent.setup()
    withRouter(<ChatView />)
    await user.click(await screen.findByText('What should I do to fix this?'))

    expect(await screen.findByText(/has not produced a verdict yet/)).toBeTruthy()
  })
})

describe('scope', () => {
  /**
   * The gate lives in the backend, but the UI has to render its verdict honestly:
   * `refused` must not wear the same badge as an answer, or a demo looks like the
   * bot cheerfully discussed lunch.
   */
  it('renders a refusal as refused, with no citation claim attached', async () => {
    vi.spyOn(mockApi, 'chat').mockResolvedValueOnce({
      answer: 'I can only answer questions about this incident.',
      mode: 'refused',
      citations: [],
      stripped: [],
      citations_valid: true,
      retrieved: [],
      usd: 0,
      attempts: 1,
    })

    const user = userEvent.setup()
    withRouter(<ChatView />)
    await user.click(await screen.findByText('What should I do to fix this?'))

    const badge = await screen.findByTitle(/declined before any model was called/)
    expect(badge).toHaveTextContent('refused')
    // "citations resolve" on a refusal would be a nonsense claim about nothing
    expect(screen.queryByText(/citations resolve/)).toBeNull()
  })

  it('mock chat declines off-topic questions, exactly as the backend does', async () => {
    for (const q of ["what's the weather?", 'what is the best food to try at hyderabad?']) {
      const res = await mockApi.chat({ question: q })
      expect(res.mode, q).toBe('refused')
      expect(res.usd).toBe(0)
    }
  })

  it('mock chat still answers real incident questions', async () => {
    const res = await mockApi.chat({ question: 'what is the evidence for catalogue-db?' })
    expect(res.mode).toBe('deterministic')
    expect(res.citations.length).toBeGreaterThan(0)
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
    // "to fix this" carries the domain word; a bare "what should I do?" is all stop
    // words once tokenised, so the real gate declines that too. The mock agrees.
    const res = await mockApi.chat({ question: 'what should I do to fix this?' })
    expect(res.mode).toBe('deterministic')
    expect(res.usd).toBe(0)
  })
})
