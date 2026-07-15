/**
 * The demo, walked end to end in a real browser, in mock mode.
 *
 * This is the only check in the repo that renders the app for real: jsdom has no
 * layout engine and no canvas, so everything below — that the graph paints, that
 * nothing overflows at 1366×768, that no console error fires during a full run —
 * is invisible to vitest.
 */
import { test, expect } from '@playwright/test'
import type { ConsoleMessage, Page } from '@playwright/test'

const CLEAN = 'clean_cascade-01'
const HERRING = 'red_herring_config-01'

/** Fail on any console error. Vite HMR noise is not an app error. */
function watchConsole(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (msg: ConsoleMessage) => {
    if (msg.type() !== 'error') return
    const text = msg.text()
    if (text.includes('[vite]') || text.includes('Download the React DevTools')) return
    errors.push(text)
  })
  page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`))
  return errors
}

/** Nothing may scroll the page sideways — a projector has no horizontal scroll. */
async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const el = document.documentElement
    return el.scrollWidth - el.clientWidth
  })
  expect(overflow, 'page scrolls horizontally').toBeLessThanOrEqual(1)
}

test('console → run → incident → verdict → agents → report → benchmark', async ({ page }) => {
  const errors = watchConsole(page)

  // ── Console ──────────────────────────────────────────────────────────────
  await page.goto('/')
  await expect(page.getByText('DEMO 1')).toBeVisible()
  await expect(page.getByText('DEMO 7')).toBeVisible()
  await expect(page.getByText('27 total')).toBeVisible()
  await expectNoHorizontalOverflow(page)

  // Starting a run navigates to the incident view.
  await page.getByText('DEMO 1').click()
  await expect(page).toHaveURL(/\/run\/clean_cascade-01\?view=incident/)

  // ── Incident: the graph must actually paint ──────────────────────────────
  await expect(page.getByTestId('topology-graph')).toBeVisible()
  await expect(page.getByTestId('topology-graph').locator('canvas').first()).toBeVisible()
  await expect(page.getByText(/derived from anomaly windows/)).toBeVisible({ timeout: 20_000 })

  // The stage indicator advances as the mock run streams.
  await expect(page.getByRole('list', { name: 'pipeline stage' })).toBeVisible()
  await expect(page.getByTestId('run-status').filter({ hasText: 'done' })).toBeVisible({ timeout: 30_000 })
  await expectNoHorizontalOverflow(page)

  // ── Verdict ──────────────────────────────────────────────────────────────
  await page.getByRole('tab', { name: 'Verdict' }).click()
  await expect(page).toHaveURL(/view=verdict/)
  const cards = page.getByTestId('hypothesis-card')
  await expect(cards.first()).toBeVisible()
  await expect(cards.first()).toHaveAttribute('data-rank', '1')
  await expect(page.getByTestId('tier-pill').first()).toHaveAttribute('data-tier', 'CONFIRMED')
  await expect(page.getByText(/services in blast radius/)).toBeVisible()
  await expectNoHorizontalOverflow(page)

  // ── Agents ───────────────────────────────────────────────────────────────
  await page.getByRole('tab', { name: 'Agents' }).click()
  await expect(page).toHaveURL(/view=agents/)
  await expect(page.getByText('Budget')).toBeVisible()
  await expect(page.getByText('SPENT 2/2 pts').first()).toBeVisible()
  await expectNoHorizontalOverflow(page)

  // ── Report ───────────────────────────────────────────────────────────────
  await page.getByRole('tab', { name: 'Report' }).click()
  await expect(page).toHaveURL(/view=report/)
  await expect(page.getByTestId('report-markdown')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('heading', { name: 'Verdict' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'fact-catalogue_db-0008' }).first()).toBeVisible()
  // No unresolved-citation warning on a healthy report.
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expectNoHorizontalOverflow(page)

  // ── Benchmark ────────────────────────────────────────────────────────────
  await page.getByRole('link', { name: 'Benchmark' }).click()
  await expect(page).toHaveURL(/\/benchmark/)
  await expect(page.getByTestId('metric-tile').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('ground truth redacted')).toBeVisible()
  // Hero tiles show real numbers, not em-dashes.
  const ac1 = page.getByTestId('metric-tile').filter({ hasText: 'AC@1' }).getByTestId('metric-value')
  await expect(ac1).not.toHaveText('—')
  await expect(page.getByText(/agentic vs fixed vs ablations/)).toBeVisible()
  await expectNoHorizontalOverflow(page)

  expect(errors, `console errors during the walk:\n${errors.join('\n')}`).toEqual([])
})

test('the red herring falls, and is cleared not called innocent', async ({ page }) => {
  const errors = watchConsole(page)

  await page.goto(`/run/${HERRING}?view=verdict`)
  const cards = page.getByTestId('hypothesis-card')
  await expect(cards.first()).toBeVisible({ timeout: 20_000 })

  // Once the run finishes, the real cause leads and payment is demoted.
  await expect(page.getByTestId('run-status').filter({ hasText: 'done' })).toBeVisible({ timeout: 40_000 })
  await expect(cards.first()).toHaveAttribute('data-hypothesis-id', 'hyp-catalogue-02')
  await expect(cards.nth(1)).toHaveAttribute('data-hypothesis-id', 'hyp-payment-01')

  await expect(page.getByText('✓ counterfactual-unchanged').first()).toBeVisible()
  // "innocent" is ground truth and lives in /eval — it must never appear.
  await expect(page.getByText(/innocent/i)).toHaveCount(0)

  expect(errors).toEqual([])
})

test('a failed pipeline is a toast, not a wall', async ({ page }) => {
  await page.goto(`/run/${CLEAN}-error?view=incident`)

  // Two on purpose: a transient toast, and a banner that persists after the
  // toast auto-dismisses. Hence .first().
  await expect(page.getByText(/Pipeline failed at stage pipeline/).first()).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByText(/Pipeline failed at stage pipeline/)).toHaveCount(2)

  // Rule 11: nothing already rendered is torn down by the failure.
  await expect(page.getByTestId('topology-graph')).toBeVisible()
  await expect(page.getByText(/derived from anomaly windows/)).toBeVisible()
  // Amber, not red: the demo continues. Exact, because the run id itself ends
  // in "-error" and would otherwise match the run link beside it.
  await expect(page.getByTestId('run-status').filter({ hasText: 'error' })).toBeVisible()
})

test('demo script deep links are real', async ({ page }) => {
  await page.goto('/demo-script')
  await expect(page.getByRole('heading', { name: /Demo script/ })).toBeVisible()

  const links = page.getByRole('listitem').getByRole('link')
  const count = await links.count()
  expect(count).toBeGreaterThanOrEqual(7)

  // Every beat's link must resolve to a route that renders, not the catch-all.
  for (let i = 0; i < count; i += 1) {
    const href = await links.nth(i).getAttribute('href')
    expect(href).toBeTruthy()
    await page.goto(href!)
    await expect(page.locator('main')).toBeVisible()
    await page.goto('/demo-script')
  }
})
