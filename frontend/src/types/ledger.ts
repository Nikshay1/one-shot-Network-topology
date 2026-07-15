/** Mirrors contracts/ledger_record.schema.json v1.1 (frozen). */
import type { ComponentId, EventId } from './events'
import type { HypothesisId } from './hypothesis'
import type { TimeWindow } from './anomaly'

/** `^fact-[A-Za-z0-9_.]+-[0-9]{4}$` */
export type FactId = string

/** v1.1 adds `investigation_note` and `remediation_result`. */
export type LedgerKind =
  | 'anomaly_observed'
  | 'anomaly_absent'
  | 'topology_path'
  | 'topology_no_path'
  | 'temporal_order'
  | 'config_change_observed'
  | 'counterfactual_result'
  | 'twin_result'
  | 'coverage_gap'
  | 'hypothesis_scored'
  | 'investigation_note'
  | 'remediation_result'

export const LEDGER_KINDS: readonly LedgerKind[] = [
  'anomaly_observed',
  'anomaly_absent',
  'topology_path',
  'topology_no_path',
  'temporal_order',
  'config_change_observed',
  'counterfactual_result',
  'twin_result',
  'coverage_gap',
  'hypothesis_scored',
  'investigation_note',
  'remediation_result',
] as const

export type Modality = 'metric' | 'log' | 'alert' | 'topology' | 'config' | 'mixed' | 'derived'

export const MODALITIES: readonly Modality[] = [
  'metric',
  'log',
  'alert',
  'topology',
  'config',
  'mixed',
  'derived',
] as const

/** One immutable fact in the evidence ledger (append-only). */
export interface LedgerRecord {
  fact_id: FactId
  case_id: string
  kind: LedgerKind
  /** maxLength 300 */
  statement: string
  component_ids: ComponentId[]
  event_ids: EventId[]
  modality: Modality
  ts_range: TimeWindow
  /** 0..1 */
  confidence: number
  /** null = a case-level fact not attached to any hypothesis. */
  hypothesis_id: HypothesisId | null
}
