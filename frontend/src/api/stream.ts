/**
 * The ONLY module that constructs an EventSource. No component may do it.
 *
 * Three properties of the VERDICT run bus drive this design:
 *
 * 1. The server ends the stream after `pipeline_done` and sends NO `retry:`
 *    directive. A browser therefore does what the SSE spec says — waits ~3s and
 *    reconnects — and `subscribe()` replays the whole run from index 0 again.
 *    The run appears to restart by itself, forever. So we close on terminal
 *    events ourselves and never let native reconnect run.
 * 2. Every subscriber is replayed the full history from index 0 and then
 *    follows live. Attach late, attach twice — the sequence is identical. That
 *    is what makes the UI un-raceable, but the price is that rendering must be
 *    idempotent: `onOpen` fires on every (re)connect so the store can reset and
 *    rebuild from a complete replay rather than appending duplicates.
 * 3. Heartbeats are SSE comment frames (`: heartbeat 15s`). EventSource
 *    swallows comments, so they are invisible here — they exist only to stop
 *    proxies closing an idle connection.
 */
import type { SseEventName, SseMessage } from '@/types/api'
import { SSE_EVENT_NAMES, isTerminalSseEvent } from '@/types/api'
import { API_BASE, IS_MOCK, streamPathFor } from './client'
import { toSseMessage } from './sseParse'
import { openMockRunStream } from '@/mocks/mockStream'

export { toSseMessage }

export type StreamStatus = 'connecting' | 'open' | 'reconnecting' | 'done' | 'error' | 'closed'

export interface RunStreamOptions {
  runId: string
  /** The `stream` field from POST /case/{id}/run (root-relative). */
  streamPath?: string
  onMessage: (msg: SseMessage) => void
  /**
   * Fires on every (re)connection. The server replays from index 0, so treat
   * this as "a full history is about to arrive" and reset derived state.
   */
  onOpen?: () => void
  onStatus?: (status: StreamStatus, detail?: string) => void
  /** An `event:` name this build doesn't know. Ignored by default (forward compat). */
  onUnknown?: (name: string, raw: string) => void
  /** Give up after this many consecutive failed connects. Infinity = never. */
  maxRetries?: number
  backoff?: Partial<BackoffConfig>
}

export interface BackoffConfig {
  initialMs: number
  factor: number
  maxMs: number
  /** Random fraction of the delay added as jitter, to avoid reconnect storms. */
  jitter: number
}

export const DEFAULT_BACKOFF: BackoffConfig = {
  initialMs: 500,
  factor: 2,
  maxMs: 15_000,
  jitter: 0.25,
}

export interface StreamHandle {
  close: () => void
  status: () => StreamStatus
}

export function nextBackoffMs(attempt: number, cfg: BackoffConfig = DEFAULT_BACKOFF): number {
  const base = Math.min(cfg.initialMs * cfg.factor ** attempt, cfg.maxMs)
  return Math.round(base * (1 + Math.random() * cfg.jitter))
}

/**
 * Opens the run stream, dispatching typed messages until a terminal event.
 *
 * Reconnect policy: the native EventSource retry is never used (we close on
 * error and reschedule ourselves) so that the delay is an exponential backoff
 * rather than the spec's flat ~3s, and so that a terminal event stops it dead.
 */
export function openRunStream(opts: RunStreamOptions): StreamHandle {
  if (IS_MOCK) return openMockRunStream(opts)

  const {
    runId,
    streamPath = streamPathFor(runId),
    onMessage,
    onOpen,
    onStatus,
    onUnknown,
    maxRetries = Infinity,
  } = opts
  const backoff: BackoffConfig = { ...DEFAULT_BACKOFF, ...opts.backoff }

  const url = `${API_BASE}${streamPath}`
  let es: EventSource | null = null
  let attempt = 0
  let terminated = false
  let closedByCaller = false
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  let status: StreamStatus = 'connecting'

  const setStatus = (next: StreamStatus, detail?: string) => {
    status = next
    onStatus?.(next, detail)
  }

  const teardown = () => {
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = undefined
    }
    if (es) {
      es.close()
      es = null
    }
  }

  const handle = (name: SseEventName) => (ev: MessageEvent<string>) => {
    const msg = toSseMessage(name, ev.data)
    if (!msg) {
      onUnknown?.(name, ev.data)
      return
    }
    onMessage(msg)

    // The server closes after a terminal event. If we don't close too, the
    // browser reconnects and replays the entire run — forever.
    if (isTerminalSseEvent(msg.event)) {
      terminated = true
      teardown()
      setStatus(
        msg.event === 'pipeline_done' ? 'done' : 'error',
        msg.event === 'pipeline_error' ? (msg.data as { error: string }).error : undefined,
      )
    }
  }

  const connect = () => {
    if (terminated || closedByCaller) return
    setStatus(attempt === 0 ? 'connecting' : 'reconnecting')

    es = new EventSource(url)

    es.onopen = () => {
      attempt = 0
      setStatus('open')
      // Every open replays the run from index 0 — rebuild, don't append.
      onOpen?.()
    }

    for (const name of SSE_EVENT_NAMES) {
      es.addEventListener(name, handle(name) as EventListener)
    }

    // Unnamed frames should not occur; swallow rather than crash the stream.
    es.onmessage = (ev: MessageEvent<string>) => onUnknown?.('message', ev.data)

    es.onerror = () => {
      if (terminated || closedByCaller) return
      teardown()

      if (attempt >= maxRetries) {
        setStatus('error', `giving up after ${attempt} reconnect attempts`)
        return
      }
      const delay = nextBackoffMs(attempt, backoff)
      attempt += 1
      setStatus('reconnecting', `retrying in ${delay}ms`)
      retryTimer = setTimeout(connect, delay)
    }
  }

  connect()

  return {
    close: () => {
      closedByCaller = true
      teardown()
      setStatus('closed')
    },
    status: () => status,
  }
}
