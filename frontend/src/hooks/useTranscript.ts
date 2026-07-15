import { useEffect, useState } from 'react'
import { getAgentTranscript } from '@/api/client'
import type { AgentName, TranscriptLine, TranscriptResultLine, TranscriptStepLine } from '@/types/api'

export interface TranscriptState {
  steps: TranscriptStepLine[]
  result: TranscriptResultLine | null
  /** last.ts − first.ts. The only real timing signal: SSE agent_step has no ts. */
  elapsedS: number | undefined
  loading: boolean
  /** null = that agent never ran (the endpoint 404s). */
  found: boolean
}

function split(lines: TranscriptLine[]): Omit<TranscriptState, 'loading' | 'found'> {
  const steps = lines.filter((l): l is TranscriptStepLine => l.type === 'step')
  const result = lines.find((l): l is TranscriptResultLine => l.type === 'result') ?? null

  // Transcript ts is absolute epoch seconds (harness.py:307 uses time.time()),
  // so only differences mean anything. A transcript may legitimately have zero
  // steps, in which case there is nothing to difference.
  const stamps = [...steps.map((s) => s.ts), ...(result ? [result.ts] : [])]
  const elapsedS =
    stamps.length >= 2 ? Math.max(...stamps) - Math.min(...stamps) : undefined

  return { steps, result, elapsedS }
}

/**
 * A historical agent transcript. Fetched when `enabled` (i.e. once the run is
 * done) — mid-run the endpoint 404s because the agent isn't registered on
 * RunVerdict.transcripts yet.
 */
export function useTranscript(
  runId: string | null | undefined,
  agent: AgentName,
  enabled: boolean,
): TranscriptState {
  const [state, setState] = useState<TranscriptState>({
    steps: [],
    result: null,
    elapsedS: undefined,
    loading: false,
    found: false,
  })

  useEffect(() => {
    if (!runId || !enabled) return
    let cancelled = false
    setState((s) => ({ ...s, loading: true }))

    getAgentTranscript(runId, agent)
      .then((lines) => {
        if (cancelled) return
        if (!lines) {
          setState({ steps: [], result: null, elapsedS: undefined, loading: false, found: false })
          return
        }
        setState({ ...split(lines), loading: false, found: true })
      })
      .catch(() => {
        if (!cancelled) setState((s) => ({ ...s, loading: false }))
      })

    return () => {
      cancelled = true
    }
  }, [runId, agent, enabled])

  return state
}
