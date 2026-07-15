/**
 * Display formatting for backend payloads. Read-only: nothing here computes a
 * tier, score or rank — it only renders what arrived.
 */
import type { EventPayload, SourceKind } from '@/types/events'
import type { DetectorMethod } from '@/types/anomaly'
import type { ConfigValue } from '@/types/events'

/** epoch seconds → HH:MM:SS (local). */
export function formatClock(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString(undefined, { hour12: false })
}

function truncate(text: string, max = 90): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat
}

function renderValue(value: ConfigValue | undefined): string {
  if (value === undefined) return '?'
  if (value === null) return 'null'
  return String(value)
}

/** One-line summary of any of the five payload variants. */
export function summarizePayload(payload: EventPayload): string {
  switch (payload.kind) {
    case 'metric':
      return `${payload.name} ${payload.value}${payload.unit ? ` ${payload.unit}` : ''}`
    case 'log': {
      const level = payload.level ? `[${payload.level}] ` : ''
      return truncate(`${level}${payload.message}`)
    }
    case 'alert':
      return `${payload.name} ${payload.state} (sev ${payload.severity})`
    case 'topology':
      return `${payload.relation} → ${payload.target_component_id}`
    case 'config':
      return `${payload.key}: ${renderValue(payload.old_value)} → ${renderValue(payload.new_value)}${
        payload.risky ? ' · risky' : ''
      }`
  }
}

/** Single-char glyph per source. Never the only signal — always paired with text. */
export const SOURCE_GLYPH: Record<SourceKind, string> = {
  metric: '▲',
  log: '❯',
  alert: '!',
  topology: '⇄',
  config: '⚙',
}

export const SOURCE_CLASS: Record<SourceKind, string> = {
  metric: 'text-sky-400',
  log: 'text-slate-400',
  alert: 'text-rose-400',
  topology: 'text-violet-400',
  config: 'text-amber-400',
}

/** The detector that produced an anomaly, in presenter English. */
export const METHOD_LABEL: Record<DetectorMethod, string> = {
  mad_zscore: 'MAD z-score',
  isolation_forest: 'isolation forest',
  log_freq_spike: 'log freq spike',
  log_rare_template: 'rare template',
  alert_dedup: 'alert dedup',
  config_risky_flag: 'risky config',
}

export function formatScore(score: number): string {
  return score.toFixed(2)
}

export function formatPct(pct: number): string {
  return `${Math.round(pct * 10) / 10}%`
}
