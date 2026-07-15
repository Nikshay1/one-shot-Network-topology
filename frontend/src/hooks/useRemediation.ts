import { useEffect, useState } from 'react'
import { getRemediation } from '@/api/client'
import type { RemediationReport } from '@/types/api'

export interface RemediationState {
  report: RemediationReport | null
  loading: boolean
  error: string | null
}

/**
 * GET /run/{id}/remediation. Returns null (a 404 with {error, done}) until the
 * run has a verdict, so this only fetches when `enabled`.
 */
export function useRemediation(
  runId: string | null | undefined,
  enabled: boolean,
): RemediationState {
  const [report, setReport] = useState<RemediationReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!runId || !enabled) return
    let cancelled = false
    setLoading(true)
    setError(null)

    getRemediation(runId)
      .then((next) => {
        if (!cancelled) setReport(next)
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
  }, [runId, enabled])

  return { report, loading, error }
}
