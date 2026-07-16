/**
 * Wires the run stream into the store. The one place the two meet — components
 * call this rather than touching stream.ts or EventSource.
 *
 * Messages are COALESCED before they reach the store. Each SSE message arrives in
 * its own task, so React cannot auto-batch them the way it batches a click handler:
 * one dispatch per message meant one full commit per message, and a real case
 * flushes thousands of `event_ingested` frames back-to-back (the catch-up flush
 * alone re-sends every cited event the stream cap dropped). The page spent its time
 * re-rendering a cytoscape graph instead of drawing.
 *
 * The reducer and the message order are unchanged, so replay-from-zero stays
 * idempotent — only the number of commits drops.
 */
import { useEffect } from 'react'
import { openRunStream } from '@/api/stream'
import { runStore } from '@/store/runStore'
import { useToast } from '@/components/Toaster'
import { isTerminalSseEvent } from '@/types/api'
import type { SseMessage } from '@/types/api'

/**
 * Coalescing window. ~One frame: long enough to collapse a burst, short enough that
 * a paced demo still looks live (at speed=10 events arrive ~47ms apart, so they flush
 * individually and the timeline animates exactly as before).
 *
 * setTimeout, NOT requestAnimationFrame: rAF does not fire in a hidden tab, so a
 * backgrounded run would queue every event and then dump the whole run in one commit
 * on return — the precise stall this exists to prevent.
 */
const FLUSH_MS = 16

export interface UseRunStreamOptions {
  /** null/undefined = don't connect (e.g. no run selected yet). */
  runId: string | null | undefined
  /** The `stream` field from POST /case/{id}/run. Defaults to /stream/{runId}. */
  streamPath?: string
  /** Log every dispatched message to the console. */
  debug?: boolean
}

export function useRunStream({ runId, streamPath, debug = false }: UseRunStreamOptions): void {
  const { toast } = useToast()

  useEffect(() => {
    if (!runId) return

    const { attach, dispatchMany, reset, setConnection } = runStore.getState()
    attach(runId)

    let pending: SseMessage[] = []
    let timer: ReturnType<typeof setTimeout> | null = null

    const flush = () => {
      timer = null
      if (pending.length === 0) return
      const batch = pending
      pending = []
      dispatchMany(batch)
    }

    const schedule = () => {
      if (timer === null) timer = setTimeout(flush, FLUSH_MS)
    }

    const handle = openRunStream({
      runId,
      ...(streamPath ? { streamPath } : {}),
      onOpen: () => {
        // The server replays the run from index 0 on every connect, so rebuild
        // from the replay rather than appending onto stale state. Anything still
        // queued belongs to the previous connection and would be applied ON TOP of
        // the reset — drop it with the state it described.
        pending = []
        if (timer !== null) {
          clearTimeout(timer)
          timer = null
        }
        reset()
        if (debug) console.info('[verdict] stream open — replaying run from index 0')
      },
      onMessage: (msg) => {
        if (debug) console.debug('[verdict] %s', msg.event, msg.data)
        pending.push(msg)

        // Terminal events land now rather than in up to FLUSH_MS: `pipeline_done` is
        // what every "is it finished?" check keys off, and a 16ms lie about that is
        // a race someone else gets to debug.
        if (isTerminalSseEvent(msg.event)) {
          if (timer !== null) clearTimeout(timer)
          flush()
        } else {
          schedule()
        }

        // A failed pipeline is a toast, never a wall. Rule 11: an agent error,
        // timeout or budget exhaustion falls back to the deterministic autopilot
        // and the run still completes — so whatever already rendered stays, and
        // the presenter keeps going.
        if (msg.event === 'pipeline_error') {
          toast({
            tone: 'error',
            title: `Pipeline failed at stage ${msg.data.stage}`,
            detail: msg.data.error,
          })
        }
      },
      onStatus: (status, detail) => {
        setConnection(status)
        if (debug) console.info('[verdict] stream %s%s', status, detail ? ` — ${detail}` : '')
      },
      onUnknown: (name) => {
        if (debug) console.warn('[verdict] ignoring unknown event type: %s', name)
      },
    })

    return () => {
      handle.close()
      // Drop the queue rather than flushing it: this run is being torn down, and the
      // next mount resets and replays from index 0 anyway.
      if (timer !== null) clearTimeout(timer)
      pending = []
    }
  }, [runId, streamPath, debug, toast])
}
