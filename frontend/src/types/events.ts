/**
 * Mirrors contracts/event_envelope.schema.json (frozen).
 *
 * Optionality here is exactly the schema's `required` list — a field is optional
 * in TS iff it is absent from `required`. Nullability is exactly the schema's
 * `type: [..., "null"]`. Do not "tidy" either one.
 */

/** `^[a-z0-9][a-z0-9-]*$` — produced ONLY by backend normalize_component(). */
export type ComponentId = string

/** `^(metric|log|alert|topology|config)-[A-Za-z0-9_.]+-[0-9]{6}$` */
export type EventId = string

export type SourceKind = 'metric' | 'log' | 'alert' | 'topology' | 'config'

export const SOURCE_KINDS: readonly SourceKind[] = [
  'metric',
  'log',
  'alert',
  'topology',
  'config',
] as const

/**
 * The schema's enum includes `null`, and `level` is not in `required`: RE2-SS
 * logs.csv carries a `level` column that is empty for most container logs.
 */
export type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR' | 'FATAL' | 'TRACE'

export type TopologyRelation = 'calls' | 'depends_on' | 'routes_to' | 'reads_from' | 'writes_to'

/** config_payload old_value/new_value: `["string", "number", "boolean", "null"]`. */
export type ConfigValue = string | number | boolean | null

export interface MetricPayload {
  kind: 'metric'
  name: string
  value: number
  unit?: string
}

export interface LogPayload {
  kind: 'log'
  message: string
  level?: LogLevel | null
  template?: string
  template_id?: string
  req_path?: string | null
  error?: string | null
}

export interface AlertPayload {
  kind: 'alert'
  name: string
  /** 0..1 */
  severity: number
  state: 'firing' | 'resolved'
}

export interface TopologyPayload {
  kind: 'topology'
  target_component_id: ComponentId
  relation: TopologyRelation
}

export interface ConfigPayload {
  kind: 'config'
  key: string
  new_value: ConfigValue
  old_value?: ConfigValue
  risky?: boolean
}

/** oneOf over the five source-specific payloads, discriminated by `kind`. */
export type EventPayload =
  | MetricPayload
  | LogPayload
  | AlertPayload
  | TopologyPayload
  | ConfigPayload

export interface EventEnvelope {
  event_id: EventId
  case_id: string
  source: SourceKind
  component_id: ComponentId
  /** epoch seconds, UTC */
  ts: number
  payload: EventPayload
}
