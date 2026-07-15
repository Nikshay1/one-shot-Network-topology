import { useEffect, useState } from 'react'
import { getLedger } from '@/api/client'
import type { LedgerQuery, LedgerRecord } from '@/types/api'

export interface LedgerState {
  facts: LedgerRecord[]
  loading: boolean
  error: string | null
}

/**
 * Ledger facts for a component. Refetches when `nonce` changes, which the caller
 * bumps on pipeline_done — facts are filed as the pipeline runs, so a fetch made
 * mid-run legitimately returns fewer than the final set.
 */
export function useLedger(
  runId: string | null | undefined,
  query: LedgerQuery,
  nonce = 0,
): LedgerState {
  const [facts, setFacts] = useState<LedgerRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const key = JSON.stringify(query)

  useEffect(() => {
    if (!runId || !query.component_id) {
      setFacts([])
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)

    getLedger(runId, JSON.parse(key) as LedgerQuery)
      .then((next) => {
        if (!cancelled) setFacts(next)
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, key, nonce])

  return { facts, loading, error }
}
