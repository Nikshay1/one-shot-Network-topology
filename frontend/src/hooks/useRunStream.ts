/**
 * Wires the run stream into the store. The one place the two meet — components
 * call this rather than touching stream.ts or EventSource.
 */
import { useEffect } from 'react'
import { openRunStream } from '@/api/stream'
import { runStore } from '@/store/runStore'
import { useToast } from '@/components/Toaster'

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

    const { attach, dispatch, reset, setConnection } = runStore.getState()
    attach(runId)

    const handle = openRunStream({
      runId,
      ...(streamPath ? { streamPath } : {}),
      onOpen: () => {
        // The server replays the run from index 0 on every connect, so rebuild
        // from the replay rather than appending onto stale state.
        reset()
        if (debug) console.info('[verdict] stream open — replaying run from index 0')
      },
      onMessage: (msg) => {
        if (debug) console.debug('[verdict] %s', msg.event, msg.data)
        dispatch(msg)

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

    return () => handle.close()
  }, [runId, streamPath, debug, toast])
}
