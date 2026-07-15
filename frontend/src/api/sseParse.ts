/**
 * SSE frame → typed message. Extracted from stream.ts so that both the real
 * EventSource path and the mock replayer share one parser (and so stream.ts can
 * import the mock without a runtime import cycle).
 *
 * Pure: no network, no EventSource, directly unit-testable.
 */
import type { SseMessage } from '@/types/api'
import { isKnownSseEvent } from '@/types/api'

/**
 * Returns null — never throws — for an `event:` name this build doesn't know or
 * for unparseable data. Ignoring unknown event types is a contract requirement
 * (forward compat): a newer backend may emit events this build predates.
 */
export function toSseMessage(name: string, raw: string): SseMessage | null {
  if (!isKnownSseEvent(name)) return null
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return null
  }
  if (data === null || typeof data !== 'object') return null
  return { event: name, data } as SseMessage
}
