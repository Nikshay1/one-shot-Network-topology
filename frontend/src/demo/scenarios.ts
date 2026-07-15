/**
 * Demo presets and case identification.
 *
 * WHY THIS FILE EXISTS AND WHAT IT DELIBERATELY DOES NOT CONTAIN
 *
 * `/cases` returns only {case_id, title, n_components, n_events} — it exposes no
 * `kind`, `scenario_type`, `system` or `duration`. So a case's kind is inferred
 * from the case_id the API itself hands us, the same way the backend synthesizes
 * `title` from it. Nothing here is invented data: it is a display convenience
 * over an id.
 *
 * The seven type NAMES mirror scenarios/registry.json's `scenario_types`. That
 * registry also carries `params.fault_service` — ground truth, which rule 4
 * confines to /eval and /scenarios label files. So the registry is NEVER
 * imported here, only the seven names are restated, and nothing in this file
 * discloses which component is at fault for any case.
 *
 * If a case_id doesn't match a known scenario prefix it is simply "real". The
 * demo buttons render only for scenarios that actually appear in /cases, so a
 * backend with no synthetic cases degrades to an empty demo row rather than
 * seven dead buttons.
 */
import type { CaseSummary } from '@/types/api'
import type { RunRequest } from '@/types/api'

export const SCENARIO_TYPES = [
  'clean_cascade',
  'red_herring_config',
  'alert_storm',
  'confounded_pair',
  'missing_telemetry',
  'topology_drift',
  'ambiguous',
] as const

export type ScenarioType = (typeof SCENARIO_TYPES)[number]

/** What the demo presenter sees on the button. */
export const SCENARIO_LABELS: Record<ScenarioType, string> = {
  clean_cascade: 'Clean cascade',
  red_herring_config: 'Red herring config',
  alert_storm: 'Alert storm',
  confounded_pair: 'Confounded pair',
  missing_telemetry: 'Missing telemetry',
  topology_drift: 'Topology drift',
  ambiguous: 'Ambiguous',
}

/** One line on why this scenario is worth showing. No ground truth. */
export const SCENARIO_BLURBS: Record<ScenarioType, string> = {
  clean_cascade: 'A single fault propagating cleanly upstream.',
  red_herring_config: 'A harmless config change competing with the real cause.',
  alert_storm: 'Enough firing alerts to bury the signal.',
  confounded_pair: 'Two components that move together.',
  missing_telemetry: 'The evidence you need was never collected.',
  topology_drift: 'The graph changed underneath the incident.',
  ambiguous: 'Genuinely under-determined — the tier should say so.',
}

export type CaseKind = 'synthetic' | 'real'

export interface CaseIdentity {
  kind: CaseKind
  scenarioType: ScenarioType | null
  /** The `-NN` suffix of a scenario variant, e.g. 3 for clean_cascade-03. */
  variantNumber: number | null
}

/** Infers kind/scenario/variant from the case_id alone. Never reads a label file. */
export function identifyCase(caseId: string): CaseIdentity {
  for (const type of SCENARIO_TYPES) {
    if (caseId === type || caseId.startsWith(`${type}-`)) {
      const match = /-(\d+)$/.exec(caseId)
      return {
        kind: 'synthetic',
        scenarioType: type,
        variantNumber: match ? Number(match[1]) : null,
      }
    }
  }
  return { kind: 'real', scenarioType: null, variantNumber: null }
}

/**
 * Preset params for the on-stage buttons.
 *
 * speed=10 is not a taste call: at speed=0 the whole ingest burst flushes before
 * detection starts, so the UI sits still and then dumps, and `agent_step` never
 * interleaves. speed=1 is not real-time either (a 2s clamp squashes idle gaps)
 * and takes an unpredictable 46–114s. speed=10 replays every demo scenario in
 * roughly 34s.
 */
export const DEMO_PRESET: RunRequest = { speed: 10, seed: 42, twin_enabled: true }

export const SPEED_OPTIONS = [
  { label: '1×', value: 1, hint: 'not real-time — 46–114s' },
  { label: '10×', value: 10, hint: 'demo speed — ~34s' },
  { label: '60×', value: 60, hint: 'fast' },
  { label: 'instant', value: 0, hint: 'eval only — UI freezes then dumps' },
] as const

export interface DemoButton {
  n: number
  scenarioType: ScenarioType
  label: string
  blurb: string
  caseId: string
}

/**
 * DEMO 1..7 — one button per scenario type, bound to its lowest-numbered
 * variant present in /cases. Types with no case are dropped, not faked.
 */
export function demoButtons(cases: CaseSummary[]): DemoButton[] {
  const byType = new Map<ScenarioType, CaseSummary[]>()
  for (const c of cases) {
    const { scenarioType } = identifyCase(c.case_id)
    if (!scenarioType) continue
    const list = byType.get(scenarioType) ?? []
    list.push(c)
    byType.set(scenarioType, list)
  }

  const out: DemoButton[] = []
  for (const type of SCENARIO_TYPES) {
    const variants = byType.get(type)
    if (!variants?.length) continue
    const first = [...variants].sort((a, b) => a.case_id.localeCompare(b.case_id))[0]!
    out.push({
      n: out.length + 1,
      scenarioType: type,
      label: SCENARIO_LABELS[type],
      blurb: SCENARIO_BLURBS[type],
      caseId: first.case_id,
    })
  }
  return out
}
