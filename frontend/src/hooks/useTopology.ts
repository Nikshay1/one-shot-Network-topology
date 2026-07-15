import { useEffect, useState } from 'react'
import { getTopology } from '@/api/client'
import type { TopologyGraph } from '@/types/api'

export interface TopologyState {
  topology: TopologyGraph | null
  loading: boolean
  error: string | null
}

export function useTopology(caseId: string | null | undefined): TopologyState {
  const [topology, setTopology] = useState<TopologyGraph | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!caseId) return
    let cancelled = false
    setLoading(true)
    setError(null)

    getTopology(caseId)
      .then((next) => {
        if (!cancelled) setTopology(next)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [caseId])

  return { topology, loading, error }
}
