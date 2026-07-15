import { useEffect, useState } from 'react'
import { getNarration } from '@/api/client'

export interface NarrationState {
  text: string
  loading: boolean
}

/**
 * GET /run/{id}/narration — the fallback for when the live chunks are gone
 * (a reload, or attaching after the run ended). Returns the same text the
 * chunks concatenate to; the chunks' `ts` is hardcoded 0.0 backend-side and is
 * not a clock.
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
        if (!cancelled) setText(res.chunks.map((c) => c.text).join(''))
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
