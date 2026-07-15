/** Mirrors contracts/anomaly_event.schema.json (frozen). */
import type { ComponentId, EventId, SourceKind } from './events'

/** `^anom-[A-Za-z0-9_.]+-[0-9]{4}$` */
export type AnomalyId = string

/** The detector that produced this anomaly — exactly one per anomaly. */
export type DetectorMethod =
  | 'mad_zscore'
  | 'isolation_forest'
  | 'log_freq_spike'
  | 'log_rare_template'
  | 'alert_dedup'
  | 'config_risky_flag'

export const DETECTOR_METHODS: readonly DetectorMethod[] = [
  'mad_zscore',
  'isolation_forest',
  'log_freq_spike',
  'log_rare_template',
  'alert_dedup',
  'config_risky_flag',
] as const

/** epoch seconds, UTC */
export interface TimeWindow {
  start: number
  end: number
}

export interface AnomalyEvent {
  anomaly_id: AnomalyId
  case_id: string
  source: SourceKind
  component_id: ComponentId
  window: TimeWindow
  /** 0..1 */
  score: number
  method: DetectorMethod
  evidence_event_ids: EventId[]
  /** maxLength 200 */
  summary: string
}
