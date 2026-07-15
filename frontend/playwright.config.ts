import { defineConfig, devices } from '@playwright/test'

/**
 * Smoke config.
 *
 * Two projects, both projector resolutions. 1366×768 is the one that bites: it
 * is what most conference rooms actually run, and a layout that only works at
 * 1920×1080 gets discovered on stage.
 *
 * The dev server runs in MOCK MODE, so the smoke needs no backend and no network.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  // VERDICT_LIVE points the suite at a dev server wired to a real backend on
  // :8000 instead of the mock server this config otherwise starts. Without this,
  // a 'live' test silently runs against mocks and passes for the wrong reason.
  use: {
    baseURL: process.env.PW_BASE_URL ?? 'http://127.0.0.1:5199',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'projector-1366',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 768 } },
    },
    {
      name: 'desktop-1920',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } },
    },
  ],
  webServer: process.env.VERDICT_LIVE === '1' ? undefined : {
    command: 'npm run dev -- --port 5199 --host 127.0.0.1',
    url: 'http://127.0.0.1:5199',
    reuseExistingServer: true,
    timeout: 60_000,
    env: { VITE_MOCK: '1' },
  },
})
