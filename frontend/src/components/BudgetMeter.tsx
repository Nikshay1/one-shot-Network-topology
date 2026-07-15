/**
 * The on-screen proof that agents are bounded (rule 10).
 *
 * Honesty note, stated in the UI as well as here: the LIMITS are not served by
 * any endpoint. Budget.snapshot() exists in the backend and returns exactly
 * these six numbers, and nothing calls it. So the used-side is reconstructed
 * from the agent_step stream and the limit-side is a copy of backend constants —
 * see lib/budget.ts. The meter is real, its ceiling is mirrored.
 */
import { AGENT_BUDGET, parseBudgetTrip, spendFor } from '@/lib/budget'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { AgentDoneEvent, AgentName, AgentStepEvent } from '@/types/api'

function Bar({
  label,
  used,
  max,
  title,
}: {
  label: string
  used: number
  max: number
  title: string
}) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : used > 0 ? 100 : 0
  const full = max > 0 && used >= max

  return (
    <div className="min-w-0 flex-1 space-y-1" title={title}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
        <span className={cn('font-mono text-[10px] tabular-nums', full && 'text-amber-400')}>
          {used}/{max}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
        <div
          className={cn(
            'h-full transition-[width] duration-300',
            full ? 'bg-amber-400' : 'bg-sky-400',
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export interface BudgetMeterProps {
  agent: AgentName
  steps: AgentStepEvent[]
  done?: AgentDoneEvent | undefined
  /** Real elapsed, from differencing transcript step timestamps. */
  elapsedS?: number | undefined
}

export function BudgetMeter({ agent, steps, done, elapsedS }: BudgetMeterProps) {
  const budget = AGENT_BUDGET[agent]
  const spend = spendFor(steps)
  const trip = parseBudgetTrip(done?.summary ?? null)

  return (
    <div className="space-y-2 rounded-lg border border-border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-[11px] uppercase tracking-wide text-muted-foreground">Budget</h4>
        {done && (
          <Badge variant={done.status === 'completed' ? 'outline' : 'danger'}>{done.status}</Badge>
        )}
      </div>

      <div className="flex gap-4">
        <Bar
          label="calls"
          used={spend.calls}
          max={budget.maxCalls}
          title="Counted from agent_step events — the stream carries no call counter."
        />
        <Bar
          label="cost points"
          used={spend.points}
          max={budget.maxCostPoints}
          title="Reconstructed by mapping each step's tool to its registry cost: run_twin 2, run_counterfactual 1, rehearse_fix 1, everything else 0."
        />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              elapsed
            </span>
            <span className="font-mono text-[10px] tabular-nums">
              {elapsedS === undefined ? '—' : `${elapsedS.toFixed(1)}s`}/{budget.wallClockS}s
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full bg-sky-400 transition-[width] duration-300"
              style={{
                width: `${elapsedS === undefined ? 0 : Math.min(100, (elapsedS / budget.wallClockS) * 100)}%`,
              }}
            />
          </div>
        </div>
      </div>

      {trip && (
        <p className="text-[11px] text-amber-400">
          Stopped on <span className="font-mono">{trip.reason}</span> at limit{' '}
          <span className="font-mono">{trip.limit}</span> — the harness cut it off, not the prompt.
        </p>
      )}

      <p className="text-[10px] leading-snug text-muted-foreground/60">
        Limits mirrored from {budget.source}. No endpoint serves them
        {elapsedS === undefined && ' — elapsed needs the transcript, which lands when the run ends'}.
      </p>
    </div>
  )
}
