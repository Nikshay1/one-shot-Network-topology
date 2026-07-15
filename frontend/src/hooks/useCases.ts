import { useEffect, useState } from 'react'
import { getCases } from '@/api/client'
import type { CaseSummary } from '@/types/api'

export interface CasesState {
  cases: CaseSummary[]
  loading: boolean
  error: string | null
  reload: () => void
}

export function useCases(): CasesState {
  const [cases, setCases] = useState<CaseSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    getCases()
      .then((next) => {
        if (cancelled) return
        setCases(next)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [nonce])

  return { cases, loading, error, reload: () => setNonce((n) => n + 1) }
}
