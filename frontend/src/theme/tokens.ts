/**
 * The one place a colour or a duration is decided.
 *
 * Every value maps to a CSS custom property in index.css or a tailwind token in
 * tailwind.config.js. Nothing here encodes data — a tier's colour lives here,
 * but which tier a hypothesis has is always the backend's.
 */
import type { Tier } from '@/types/hypothesis'

/** Page/tab transitions. The spec's ceiling is 200ms; nothing should exceed it. */
export const MOTION_MS = {
  page: 180,
  pill: 350,
  bar: 300,
} as const

/** The tier palette, matching --tier-* in index.css. */
export const TIER_TOKEN: Record<Tier, { text: string; bg: string; border: string; dot: string }> = {
  CONFIRMED: {
    text: 'text-tier-confirmed',
    bg: 'bg-tier-confirmed/10',
    border: 'border-tier-confirmed/30',
    dot: 'bg-tier-confirmed',
  },
  CORRELATED: {
    text: 'text-tier-correlated',
    bg: 'bg-tier-correlated/10',
    border: 'border-tier-correlated/30',
    dot: 'bg-tier-correlated',
  },
  MISSING_EVIDENCE: {
    text: 'text-tier-missing',
    bg: 'bg-tier-missing/10',
    border: 'border-tier-missing/30',
    dot: 'bg-tier-missing',
  },
}

/**
 * Series colours for the benchmark's grouped bars. Keyed by the mode names the
 * backend actually emits (eval/run_benchmark.py:337 and the --with-ablations
 * flag), NOT invented: fixed | agentic | fixed-no-counterfactual | fixed-no-twin
 * | fixed-no-topology.
 */
export const MODE_COLOR: Record<string, string> = {
  agentic: '#38bdf8',
  fixed: '#a78bfa',
  'fixed-no-counterfactual': '#fbbf24',
  'fixed-no-twin': '#f43f5e',
  'fixed-no-topology': '#4ade80',
}

export const MODE_FALLBACK_COLOR = '#64748b'

export function modeColor(mode: string): string {
  return MODE_COLOR[mode] ?? MODE_FALLBACK_COLOR
}

/** Presenter-facing names for the modes. */
export const MODE_LABEL: Record<string, string> = {
  agentic: 'agentic',
  fixed: 'fixed pipeline',
  'fixed-no-counterfactual': 'ablation: no counterfactual',
  'fixed-no-twin': 'ablation: no twin',
  'fixed-no-topology': 'ablation: no topology',
}

export function modeLabel(mode: string): string {
  return MODE_LABEL[mode] ?? mode
}
