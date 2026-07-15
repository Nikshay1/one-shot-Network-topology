/**
 * The twin's verdict on a hypothesis.
 *
 * WHY THERE IS NO OBSERVED-vs-SIMULATED GRID
 *
 * The spec asks for a side-by-side sparkline grid. The simulated series does not
 * exist anywhere a client can reach, and not because someone forgot a field —
 * it is destroyed inside the simulator:
 *
 *   TwinModel keeps per-event records, but on a local object created inside
 *   _avg_sim_deltas() (twin/runner.py:61) that is garbage-collected when the
 *   loop exits. model.aggregate() already collapses a window into a 5-float
 *   feature vector — the finest thing that survives — and only for two windows.
 *   Those are differenced into scalar deltas, then compare() collapses them into
 *   one cosine. twin() returns {run, similarity, verdict, missing_evidence}, and
 *   the Twin model forbids extra fields.
 *
 * So the grid needs a new capture path and endpoint in the backend, not a field.
 * What IS real: the cosine, the two thresholds it is bucketed by, and the list
 * of components the twin simulated a symptom on that reality never instrumented.
 * Observed sparklines are shown alone, labelled as such, rather than faking a
 * simulated column.
 */
import { TWIN_MATCH_THETA, TWIN_PARTIAL_THETA, instrumentRecommendation } from '@/lib/twin'
import { MetricSparkline } from '@/components/MetricSparkline'
import { Badge } from '@/components/ui/badge'
import { useAnomaliesFor, useSeriesFor } from '@/store/useRunStore'
import { cn } from '@/lib/utils'
import type { TwinResultEvent } from '@/types/api'
import type { RankedHypothesis, TwinVerdict } from '@/types/hypothesis'

const VERDICT_CLASS: Record<TwinVerdict, string> = {
  match: 'border-tier-confirmed/40 bg-tier-confirmed/10 text-tier-confirmed',
  partial: 'border-tier-correlated/40 bg-tier-correlated/10 text-tier-correlated',
  mismatch: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
}

/** A linear dial with both θ thresholds marked, so the verdict is legible. */
function SimilarityDial({ similarity, verdict }: { similarity: number; verdict: TwinVerdict }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          cosine similarity
        </span>
        <span className="font-mono text-lg tabular-nums">{similarity.toFixed(2)}</span>
      </div>

      <div className="relative h-2 overflow-visible rounded-full bg-secondary">
        <div
          className={cn(
            'h-full rounded-full transition-[width] duration-500',
            verdict === 'match'
              ? 'bg-tier-confirmed'
              : verdict === 'partial'
                ? 'bg-tier-correlated'
                : 'bg-rose-500',
          )}
          style={{ width: `${Math.min(100, Math.max(0, similarity * 100))}%` }}
        />
        {[
          { theta: TWIN_PARTIAL_THETA, label: 'partial' },
          { theta: TWIN_MATCH_THETA, label: 'match' },
        ].map(({ theta, label }) => (
          <div
            key={label}
            className="absolute -top-0.5 h-3 w-px bg-foreground/70"
            style={{ left: `${theta * 100}%` }}
            title={`θ ${label} = ${theta}`}
          >
            <span className="absolute -bottom-4 left-1/2 -translate-x-1/2 whitespace-nowrap font-mono text-[9px] text-muted-foreground">
              θ{theta}
            </span>
          </div>
        ))}
      </div>
      <div className="h-3" aria-hidden />
    </div>
  )
}

export interface TwinCompareProps {
  hypothesis: RankedHypothesis
  /**
   * The twin_result SSE event. Preferred over hypothesis.twin for
   * missing_evidence: in agentic mode rescore rebuilds the twin block by regexing
   * the ledger statement and hardcodes missing_evidence to [], so the hypothesis
   * loses it while this event still carries it.
   */
  twinResult?: TwinResultEvent | undefined
}

export function TwinCompare({ hypothesis, twinResult }: TwinCompareProps) {
  const series = useSeriesFor(hypothesis.suspect_component)
  const anomalies = useAnomaliesFor(hypothesis.suspect_component)
  const windows = anomalies.map((a) => a.window)

  const twin = hypothesis.twin
  if (!twin && !twinResult) {
    return (
      <div className="rounded-lg border border-dashed border-border p-6 text-center">
        <p className="text-sm text-muted-foreground">
          No twin was run for {hypothesis.suspect_component}.
        </p>
        <p className="mx-auto mt-1 max-w-md text-[11px] text-muted-foreground/70">
          The autopilot only buys a twin for the rank-1 hypothesis — it costs 2 of the 7 cost points
          a run may spend.
        </p>
      </div>
    )
  }

  const similarity = twinResult?.similarity ?? twin?.similarity ?? 0
  const verdict = twinResult?.verdict ?? twin?.verdict ?? 'mismatch'
  const run = twinResult?.run ?? twin?.run ?? '—'
  // missing_evidence: SSE first — see the prop docs.
  const missing = twinResult?.missing_evidence ?? twin?.missing_evidence ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn('rounded border px-2 py-0.5 font-mono text-xs uppercase', VERDICT_CLASS[verdict])}
          data-testid="twin-verdict"
        >
          {verdict}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">{run}</span>
        <Badge variant="outline" className="ml-auto">
          {hypothesis.suspect_component}
        </Badge>
      </div>

      <SimilarityDial similarity={similarity} verdict={verdict} />

      <section className="space-y-2">
        <div className="flex items-baseline justify-between gap-2">
          <h4 className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Observed at {hypothesis.suspect_component}
          </h4>
          <span className="text-[10px] text-muted-foreground/70">observed only</span>
        </div>

        {series.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No metric samples for this component on the stream.
          </p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {series.map((s) => (
              <div key={`${s.component_id}|${s.name}`} className="rounded border border-border p-2">
                <div className="flex items-baseline justify-between">
                  <span className="font-mono text-[11px]">
                    {s.name}
                    {s.unit ? ` (${s.unit})` : ''}
                  </span>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {s.points.length} pts
                  </span>
                </div>
                <MetricSparkline series={s} windows={windows} />
              </div>
            ))}
          </div>
        )}

        <p className="text-[10px] leading-snug text-muted-foreground/60">
          There is no simulated column. The twin&apos;s per-component series is collapsed into a
          five-feature vector inside the simulator and garbage-collected before anything persists —
          the cosine above is all that survives. A side-by-side grid needs a new backend capture
          path, not a new field.
        </p>
      </section>

      <section className="space-y-2">
        <h4 className="text-[11px] uppercase tracking-wide text-muted-foreground">
          Missing evidence
        </h4>
        {missing.length === 0 ? (
          <p className="text-[11px] leading-snug text-muted-foreground">
            The twin simulated no symptom on an uninstrumented component
            {twin && !twinResult && ' — though in agentic mode this field is emptied on the verdict, so absence here is not proof'}
            .
          </p>
        ) : (
          <ul className="space-y-1.5">
            {missing.map((component) => (
              <li
                key={component}
                className="rounded border border-tier-missing/30 bg-tier-missing/5 p-2"
              >
                <span className="font-mono text-[11px] text-tier-missing">{component}</span>
                <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
                  {instrumentRecommendation(component)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
