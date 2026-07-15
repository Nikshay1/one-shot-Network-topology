import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/**
 * One number from /benchmark.
 *
 * `value` is always what the API returned. A tile whose metric is absent renders
 * "—" and says which metric key was missing, rather than substituting a zero: on
 * this endpoint an absent metric means the suite wasn't run, and a 0 would read
 * as a measured failure.
 */
/**
 * Below this, a rate is not a rate — it is an anecdote. `AC@1 = 0.0%` over one
 * case and over twenty-four are the same string and different claims, so the
 * sample size is shown always and called out when it is this small.
 */
export const SMALL_SAMPLE_N = 5

export interface MetricTileProps {
  label: string
  /** null/undefined = the API didn't provide it. */
  value: number | null | undefined
  format?: (value: number) => string
  hint?: string
  /** Where the number came from, e.g. "heldout:agentic". */
  source?: string
  /** How many cases the metric was computed over. Never omit it. */
  n?: number | undefined
  loading?: boolean
  emphasis?: boolean
}

export function MetricTile({
  label,
  value,
  format = (v) => v.toFixed(2),
  hint,
  source,
  n,
  loading,
  emphasis,
}: MetricTileProps) {
  const missing = value === null || value === undefined
  const smallSample = !missing && n !== undefined && n < SMALL_SAMPLE_N

  return (
    <div
      className={cn(
        'space-y-1 rounded-lg border border-border bg-card p-3',
        emphasis && 'border-sky-500/30',
      )}
      data-testid="metric-tile"
      data-metric={label}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
        {source && (
          <span className="font-mono text-[9px] text-muted-foreground/60" title="metrics key">
            {source}
          </span>
        )}
      </div>

      {loading ? (
        <Skeleton className="h-7 w-20" />
      ) : (
        <div className="flex items-baseline gap-2">
          <span
            className={cn(
              'font-mono text-2xl tabular-nums',
              missing && 'text-muted-foreground/40',
            )}
            data-testid="metric-value"
          >
            {missing ? '—' : format(value)}
          </span>
          {n !== undefined && !missing && (
            <span
              className={cn(
                'font-mono text-[10px] tabular-nums',
                smallSample ? 'text-amber-400' : 'text-muted-foreground/70',
              )}
              data-testid="metric-n"
            >
              n={n}
            </span>
          )}
        </div>
      )}

      {hint && <p className="text-[10px] leading-snug text-muted-foreground/70">{hint}</p>}

      {smallSample && (
        <p className="text-[10px] leading-snug text-amber-400/90">
          {n === 1 ? 'one case — this is an anecdote, not a rate' : `only ${n} cases — treat as indicative`}
        </p>
      )}

      {missing && !loading && (
        <p className="text-[10px] leading-snug text-muted-foreground/60">
          not reported for this suite
        </p>
      )}
    </div>
  )
}
