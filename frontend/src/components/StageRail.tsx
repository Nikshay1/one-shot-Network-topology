/**
 * The pipeline stage strip: DETECT → LOCALIZE → RANK → INVESTIGATE → VERIFY →
 * NARRATE, with the run id and elapsed on the right.
 *
 * The stage is INFERRED from which event types have arrived — no endpoint or
 * event carries a stage name (`pipeline_error.stage` is only "replay" or
 * "pipeline"). It is display state only and feeds no tier, score or rank.
 *
 * Elapsed is measured in the browser from the first event to the terminal one,
 * and is labelled as such: the SSE stream carries no timestamps, and at speed=10
 * a replay is not wall-clock anyway.
 */
import { useEffect, useRef, useState } from 'react'
import { useRunStore, useStageProgress } from '@/store/useRunStore'
import { PIPELINE_STAGES } from '@/store/runStore'
import { cn } from '@/lib/utils'

function useElapsed(): string {
  const status = useRunStore((s) => s.status)
  const started = useRef<number | null>(null)
  const [, tick] = useState(0)

  const live = status === 'streaming'
  const idle = status === 'idle'

  useEffect(() => {
    if (idle) started.current = null
    else if (started.current === null) started.current = Date.now()
  }, [idle])

  useEffect(() => {
    if (!live) return
    const timer = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [live])

  if (started.current === null) return '--:--'
  const s = Math.floor((Date.now() - started.current) / 1000)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export function StageRail() {
  const progress = useStageProgress()
  const status = useRunStore((s) => s.status)
  const runId = useRunStore((s) => s.runId)
  const elapsed = useElapsed()

  if (status === 'idle' && !runId) return null

  return (
    <div className="shrink-0 px-[4.6%] pt-5">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white/70 px-4 py-2.5 backdrop-blur">
        <ol className="flex flex-wrap items-center gap-x-5 gap-y-1" aria-label="pipeline stage">
          {PIPELINE_STAGES.map((stage) => {
            const reached = progress.reached.includes(stage)
            const active = progress.active === stage && !progress.complete
            return (
              <li key={stage} className="flex items-center gap-2">
                <span
                  className={cn(
                    'grid h-4 w-4 place-items-center rounded-full border text-[8px] transition-colors',
                    reached
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border bg-transparent text-transparent',
                    active && 'animate-pulse',
                  )}
                  aria-hidden
                >
                  ✓
                </span>
                <span
                  className={cn(
                    'font-mono text-[10px] uppercase tracking-[1px] transition-colors',
                    reached ? 'text-foreground' : 'text-muted-foreground/50',
                    active && 'text-primary',
                  )}
                  aria-current={active ? 'step' : undefined}
                >
                  {stage}
                </span>
              </li>
            )
          })}
        </ol>

        <div className="flex items-center gap-4 font-mono text-[10px] text-muted-foreground">
          {runId && (
            <span>
              Run ID: <span className="text-foreground">{runId}</span>
            </span>
          )}
          <span title="measured in the browser — the stream carries no timestamps">
            Elapsed: <span className="text-foreground">{elapsed}</span>
          </span>
          {/*
            The run's own status. Amber on error, not red: rule 11 means a failed
            pipeline still produces a verdict via the autopilot, so it is a thing
            to mention rather than a stop sign.
          */}
          <span
            data-testid="run-status"
            className={cn(
              'rounded-full border px-2 py-0.5 uppercase tracking-wide',
              status === 'error'
                ? 'border-amber-500/50 bg-amber-500/10 text-amber-700'
                : status === 'done'
                  ? 'border-tier-confirmed/40 bg-tier-confirmed/10 text-tier-confirmed'
                  : 'border-border bg-white text-muted-foreground',
            )}
            title={
              status === 'error'
                ? 'The pipeline reported an error. The run may still have a verdict — rule 11 falls back to the deterministic autopilot.'
                : undefined
            }
          >
            {status}
          </span>
        </div>
      </div>
    </div>
  )
}
