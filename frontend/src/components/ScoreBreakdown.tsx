/**
 * The five pre-weighted score terms as one stacked bar.
 *
 * These are CONTRIBUTIONS, not factors: the schema states they must sum to
 * `score`, an invariant the backend enforces in code because JSON Schema can't.
 * So the bar is drawn against a 0..1 scale and the segments are the terms at
 * their true widths — never normalised to fill the bar, which would silently
 * turn a 0.15 hypothesis into a full-looking one.
 *
 * The Σ caption renders the backend's `score`. The sum is computed only to CHECK
 * the backend's own invariant, and a mismatch is surfaced rather than hidden —
 * that is a diagnostic, not a recomputed score. `score` on the card always comes
 * from the backend (rule 3).
 */
import { SCORE_BREAKDOWN_KEYS } from '@/types/hypothesis'
import type { ScoreBreakdown as ScoreBreakdownData } from '@/types/hypothesis'
import { cn } from '@/lib/utils'

const TERM_LABEL: Record<keyof ScoreBreakdownData, string> = {
  coverage: 'coverage',
  topo_consistency: 'topology',
  precedence: 'precedence',
  corroboration: 'corroboration',
  pagerank: 'pagerank',
}

const TERM_CLASS: Record<keyof ScoreBreakdownData, string> = {
  coverage: 'bg-sky-400',
  topo_consistency: 'bg-violet-400',
  precedence: 'bg-emerald-400',
  corroboration: 'bg-amber-400',
  pagerank: 'bg-rose-400',
}

/** Floats don't sum exactly; the backend rounds to 2dp. */
const EPSILON = 0.011

export function sumBreakdown(breakdown: ScoreBreakdownData): number {
  return SCORE_BREAKDOWN_KEYS.reduce((total, key) => total + breakdown[key], 0)
}

export interface ScoreBreakdownProps {
  breakdown: ScoreBreakdownData
  /** The backend's score. Displayed as-is — never recomputed from the terms. */
  score: number
  compact?: boolean
}

export function ScoreBreakdown({ breakdown, score, compact = false }: ScoreBreakdownProps) {
  const sum = sumBreakdown(breakdown)
  const drift = Math.abs(sum - score)
  const invariantHolds = drift <= EPSILON

  return (
    <div className="space-y-1.5">
      <div
        className="flex h-2 w-full overflow-hidden rounded-full bg-secondary"
        role="img"
        aria-label={`score ${score.toFixed(2)} of 1, made up of ${SCORE_BREAKDOWN_KEYS.map(
          (k) => `${TERM_LABEL[k]} ${breakdown[k].toFixed(2)}`,
        ).join(', ')}`}
      >
        {SCORE_BREAKDOWN_KEYS.map((key) => {
          const value = breakdown[key]
          if (value <= 0) return null
          return (
            <div
              key={key}
              // Width is the term's true share of the 0..1 scale.
              style={{ width: `${value * 100}%` }}
              className={cn(TERM_CLASS[key], 'transition-[width] duration-300')}
              title={`${TERM_LABEL[key]}: ${value.toFixed(2)}`}
            />
          )
        })}
      </div>

      {!compact && (
        <ul className="flex flex-wrap gap-x-3 gap-y-0.5">
          {SCORE_BREAKDOWN_KEYS.map((key) => (
            <li key={key} className="flex items-center gap-1" title={`${TERM_LABEL[key]}: ${breakdown[key].toFixed(2)}`}>
              <span className={cn('h-1.5 w-1.5 rounded-full', TERM_CLASS[key])} aria-hidden />
              <span className="text-[10px] text-muted-foreground">{TERM_LABEL[key]}</span>
              <span className="font-mono text-[10px] tabular-nums text-foreground">
                {breakdown[key].toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}

      <p className="font-mono text-[10px] text-muted-foreground">
        Σ = {sum.toFixed(2)} = score {score.toFixed(2)}
        {!invariantHolds && (
          <span className="ml-1.5 text-rose-400" role="alert">
            — backend invariant broken: terms sum to {sum.toFixed(3)}, score is {score.toFixed(3)}
          </span>
        )}
      </p>
    </div>
  )
}
