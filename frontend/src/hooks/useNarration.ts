import { useEffect, useState } from 'react'
import { getNarration } from '@/api/client'

export interface NarrationState {
  text: string
  loading: boolean
}

/**
 * GET /run/{id}/narration — the fallback for when the live chunks are gone
 * (a reload, or attaching after the run ended).
 *
 * Chunks are joined with a blank line for the same reason the store does it:
 * the backend produced them with `text.split("\n\n")` and dropped the
 * separator, so concatenation glues every heading onto the previous paragraph.
 * The endpoint replays those same chunks off the bus log, so it inherits the
 * bug — it only returns the intact narration.text when there are no chunks at
 * all, in which case the join is a no-op over a single element.
 *
 * The chunks' `ts` is hardcoded 0.0 backend-side and is not a clock.
 */
export function useNarration(runId: string | null | undefined, enabled: boolean): NarrationState {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!runId || !enabled) return
    let cancelled = false
    setLoading(true)

    getNarration(runId)
      .then((res) => {
        if (!cancelled) setText(res.chunks.map((c) => c.text).join('\n\n'))
      })
      .catch(() => {
        /* the view renders its own empty state */
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [runId, enabled])

  return { text, loading }
}
