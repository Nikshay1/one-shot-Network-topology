/**
 * Backend reachability, polled via GET /health.
 *
 * CONTRACT GAP: /health returns only {status, version} (backend/api/app.py:242).
 * It exposes no `offline_mode`, so the frontend CANNOT know whether the backend
 * is running in its OFFLINE (cached-transcript replay) mode. Rather than invent
 * the field, this reports what is actually knowable: whether the API answers.
 * See OfflineReason for what the badge can honestly say.
 */
import { useEffect, useState } from 'react'
import { IS_MOCK, startHealthPoll } from '@/api/client'
import type { HealthState } from '@/api/client'

export interface HealthInfo {
  state: HealthState
  version?: string
  /** True when nothing real is behind the UI: mock fixtures or a dead API. */
  offline: boolean
  reason: OfflineReason
}

export type OfflineReason = 'mock' | 'unreachable' | 'none'

export function useHealth(intervalMs = 5_000): HealthInfo {
  const [state, setState] = useState<HealthState>(IS_MOCK ? 'online' : 'unknown')
  const [version, setVersion] = useState<string | undefined>(undefined)

  useEffect(() => {
    return startHealthPoll((next, v) => {
      setState(next)
      setVersion(v)
    }, intervalMs)
  }, [intervalMs])

  const reason: OfflineReason = IS_MOCK ? 'mock' : state === 'offline' ? 'unreachable' : 'none'

  return {
    state,
    ...(version === undefined ? {} : { version }),
    offline: reason !== 'none',
    reason,
  }
}
