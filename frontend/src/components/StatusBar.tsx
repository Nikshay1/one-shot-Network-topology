import { NavLink } from 'react-router-dom'
import { motion } from 'framer-motion'
import { useHealth } from '@/hooks/useHealth'
import { useRunStore, useStageProgress } from '@/store/useRunStore'
import { PIPELINE_STAGES } from '@/store/runStore'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

function HealthDot() {
  const { state, version, offline, reason } = useHealth()

  const dot =
    state === 'online'
      ? 'bg-emerald-400'
      : state === 'offline'
        ? 'bg-rose-500'
        : 'bg-slate-500 animate-pulse'

  const title =
    reason === 'mock'
      ? 'MOCK MODE — fixtures only, the API is not being called'
      : state === 'online'
        ? `API reachable${version ? ` · v${version}` : ''}`
        : state === 'offline'
          ? 'API unreachable — is the backend running?'
          : 'checking API…'

  return (
    <div className="flex items-center gap-2">
      <span className={cn('h-2 w-2 shrink-0 rounded-full', dot)} aria-hidden />
      {/* The dot is decorative; the text carries the meaning (rule 7). */}
      <span className="text-xs text-muted-foreground" title={title}>
        {reason === 'mock' ? 'mock' : state}
      </span>
      {offline && (
        <Badge variant="danger" title={title}>
          {reason === 'mock' ? 'OFFLINE · MOCK' : 'OFFLINE · API UNREACHABLE'}
        </Badge>
      )}
    </div>
  )
}

function StageIndicator() {
  const progress = useStageProgress()
  const status = useRunStore((s) => s.status)

  if (!progress.active && status === 'idle') return null

  return (
    <ol className="flex items-center gap-1" aria-label="pipeline stage">
      {PIPELINE_STAGES.map((stage) => {
        const reached = progress.reached.includes(stage)
        const active = progress.active === stage && !progress.complete
        return (
          <li key={stage} className="flex items-center gap-1">
            <span
              className={cn(
                'relative rounded px-1.5 py-0.5 font-mono text-[10px] tracking-wide transition-colors',
                reached ? 'text-foreground' : 'text-muted-foreground/40',
                active && 'bg-secondary',
              )}
              aria-current={active ? 'step' : undefined}
            >
              {active && (
                <motion.span
                  className="absolute inset-0 rounded bg-primary/15"
                  animate={{ opacity: [0.25, 0.8, 0.25] }}
                  transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
                  aria-hidden
                />
              )}
              <span className="relative">{stage}</span>
            </span>
            {stage !== 'NARRATE' && (
              <span className="text-muted-foreground/30" aria-hidden>
                ›
              </span>
            )}
          </li>
        )
      })}
    </ol>
  )
}

export function StatusBar() {
  const runId = useRunStore((s) => s.runId)
  const status = useRunStore((s) => s.status)

  return (
    <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-border px-4">
      <div className="flex min-w-0 items-center gap-3">
        {runId ? (
          <NavLink
            to={`/run/${runId}`}
            className="truncate font-mono text-xs text-foreground hover:underline"
          >
            {runId}
          </NavLink>
        ) : (
          <span className="text-xs text-muted-foreground">no run</span>
        )}
        <Badge variant={status === 'error' ? 'danger' : 'outline'}>{status}</Badge>
      </div>

      <StageIndicator />

      <HealthDot />
    </header>
  )
}
