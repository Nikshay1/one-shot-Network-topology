/**
 * The same walk, against a REAL backend — the check mock mode cannot make.
 *
 * Skipped unless VERDICT_LIVE=1, because it needs a server on :8000 with warm
 * data. Run it with:
 *
 *   # terminal 1
 *   OFFLINE=1 OPENAI_API_KEY= VERDICT_CORS_ORIGINS=http://127.0.0.1:5200 \
 *     py -m backend.main --port 8000
 *   # terminal 2
 *   VERDICT_LIVE=1 npx playwright test e2e/live.spec.ts --project=projector-1366
 *
 * OFFLINE=1 replays cached agent transcripts, so no LLM call is made and the run
 * costs nothing. OPENAI_API_KEY must be BLANKED, not unset — .env reloads it on
 * the next `import backend` (README bug #9).
 *
 * What this catches that mock mode cannot: CORS (a preflight failure kills the
 * EventSource before a line of our code runs), real case ids, real payload
 * shapes, and the real stream's ordering.
 */
import { test, expect } from '@playwright/test'

const LIVE = process.env.VERDICT_LIVE === '1'

test.skip(!LIVE, 'set VERDICT_LIVE=1 and run the backend on :8000')

test('live backend: console lists real cases and /benchmark redacts', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error' && !m.text().includes('[vite]')) errors.push(m.text())
  })
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))

  await page.goto('/')

  // Real /cases — the demo row is built from whatever the backend actually has.
  await expect(page.getByText('DEMO 1')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(/\d+ total/)).toBeVisible()

  // The health dot must say online — this is also the CORS check: a preflight
  // failure would show OFFLINE · API UNREACHABLE instead.
  await expect(page.locator('header').getByText('system live')).toBeVisible()
  await expect(page.getByText(/OFFLINE/)).toHaveCount(0)

  // Benchmark, from the real endpoint.
  await page.getByRole('link', { name: 'Benchmark' }).click()
  await expect(page.getByTestId('metric-tile').first()).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('ground truth redacted')).toBeVisible()
  await expect(page.getByText(/agentic vs fixed vs ablations/)).toBeVisible()

  expect(errors, errors.join('\n')).toEqual([])
})

test('live backend: a real run streams to a verdict', async ({ page }) => {
  test.setTimeout(180_000)

  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error' && !m.text().includes('[vite]')) errors.push(m.text())
  })
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))

  await page.goto('/')
  await expect(page.getByText('DEMO 1')).toBeVisible({ timeout: 20_000 })

  // POST /case/{id}/run at speed 10 → roughly 34s of replay.
  await page.getByText('DEMO 1').click()
  await expect(page).toHaveURL(/\/run\/.+\?view=incident/)

  // The topology came from GET /case/{id}/topology, not a fixture.
  await expect(page.getByTestId('topology-graph')).toBeVisible()
  await expect(page.getByTestId('topology-graph').locator('canvas').first()).toBeVisible()

  // Events actually arrive over the real SSE stream.
  await expect(page.getByText(/derived from anomaly windows/)).toBeVisible({ timeout: 60_000 })
  await expect(page.getByTestId('run-status').filter({ hasText: 'done' })).toBeVisible({
    timeout: 120_000,
  })

  // A real verdict, with the backend's own tier.
  await page.getByRole('tab', { name: 'Verdict' }).click()
  const cards = page.getByTestId('hypothesis-card')
  await expect(cards.first()).toBeVisible()
  await expect(cards.first()).toHaveAttribute('data-rank', '1')
  await expect(page.getByTestId('tier-pill').first()).toHaveAttribute(
    'data-tier',
    /CONFIRMED|CORRELATED|MISSING_EVIDENCE/,
  )

  // Agents: OFFLINE replays the cached transcript, so steps must still appear.
  await page.getByRole('tab', { name: 'Agents' }).click()
  await expect(page.getByText('Budget')).toBeVisible()

  expect(errors, errors.join('\n')).toEqual([])
})
