/**
 * Grouped bars: agentic vs fixed vs ablations vs baselines.
 *
 * Everything is driven by what the payload contains. The mode names are the ones
 * the backend emits — fixed | agentic | fixed-no-counterfactual | fixed-no-twin |
 * fixed-no-topology — and the metric names differ by suite: heldout/dev report
 * AC@1/AC@3/Avg@5, synthetic reports precision@1/precision@3. So the chart reads
 * the keys present rather than assuming a fixed set, and a suite with neither
 * renders an empty state instead of a chart of zeros.
 *
 * Baselines are frequently absent for a real reason: they shell out to RCAEval,
 * and if it isn't importable the runner records skipped=true with the reason.
 * That is rendered, not hidden — an empty baseline row would imply a score of 0.
 */
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { modeColor, modeLabel } from '@/theme/tokens'
import type { BenchmarkMetrics, Baselines } from '@/types/api'

/** Metric keys that are a 0..1 accuracy, in the order a reader expects them. */
const ACCURACY_KEYS = ['AC@1', 'AC@3', 'Avg@5', 'precision@1', 'precision@3'] as const

export interface ComparisonChartProps {
  /** metrics entries for ONE suite, keyed by mode. */
  byMode: Record<string, BenchmarkMetrics>
  suite: string
  baselines?: Baselines | undefined
}

/** Below this a bar chart compares anecdotes, and should say so. */
const SMALL_SAMPLE_N = 5

interface Row {
  metric: string
  [mode: string]: string | number
}

export function ComparisonChart({ byMode, suite, baselines }: ComparisonChartProps) {
  const modes = Object.keys(byMode)

  // Only the accuracy keys this suite actually reported.
  const metrics = ACCURACY_KEYS.filter((k) =>
    modes.some((m) => typeof byMode[m]?.[k] === 'number'),
  )

  const rows: Row[] = metrics.map((metric) => {
    const row: Row = { metric }
    for (const mode of modes) {
      const value = byMode[mode]?.[metric]
      if (typeof value === 'number') row[mode] = value
    }
    return row
  })

  const baselineRows = (baselines?.results ?? []).filter((b) => !b.skipped)

  if (rows.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-border">
        <p className="max-w-sm p-6 text-center text-sm text-muted-foreground">
          The <span className="font-mono">{suite}</span> suite reported no accuracy metrics.
        </p>
      </div>
    )
  }

  // The n behind every bar. If it is small, the chart is comparing single runs
  // and must say so — five bars at different heights look like a result.
  const sampleSizes = [...new Set(modes.map((m) => byMode[m]?.n ?? 0))]
  const maxN = Math.max(0, ...sampleSizes)

  return (
    <div className="space-y-3">
      {maxN > 0 && maxN < SMALL_SAMPLE_N && (
        <p
          role="note"
          className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-2 text-[11px] leading-snug text-amber-300"
        >
          The <span className="font-mono">{suite}</span> suite has n=
          {sampleSizes.join('/')} per mode. These bars compare single runs, not rates — a mode at
          1.0 got its one case right. Do not read the ordering as a result.
        </p>
      )}

      <div className="h-72 rounded-lg border border-border bg-card p-3">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: -18 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(217 33% 20%)" vertical={false} />
            <XAxis
              dataKey="metric"
              tick={{ fill: 'hsl(215 20% 65%)', fontSize: 11 }}
              axisLine={{ stroke: 'hsl(217 33% 20%)' }}
              tickLine={false}
            />
            <YAxis
              domain={[0, 1]}
              tick={{ fill: 'hsl(215 20% 65%)', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: 'hsl(217 33% 17%)', opacity: 0.4 }}
              contentStyle={{
                background: 'hsl(222 44% 9%)',
                border: '1px solid hsl(217 33% 20%)',
                borderRadius: 6,
                fontSize: 11,
              }}
              formatter={(value: number, name: string) => [value.toFixed(3), modeLabel(name)]}
            />
            <Legend
              formatter={(value: string) => (
                <span className="text-[11px] text-muted-foreground">{modeLabel(value)}</span>
              )}
            />
            {modes.map((mode) => (
              <Bar
                key={mode}
                dataKey={mode}
                fill={modeColor(mode)}
                radius={[2, 2, 0, 0]}
                isAnimationActive={false}
                // A 0.0 bar has zero height and is indistinguishable from a mode
                // that wasn't run. The stub says "measured, and it was zero" —
                // it is deliberately too small to misread as a value.
                minPointSize={2}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>

      <section className="space-y-1.5">
        <h4 className="text-[11px] uppercase tracking-wide text-muted-foreground">Baselines</h4>
        {baselineRows.length > 0 ? (
          <ul className="space-y-1">
            {baselineRows.map((b) => (
              <li
                key={b.name}
                className="flex items-center justify-between gap-2 rounded border border-border px-2 py-1.5"
              >
                <span className="font-mono text-[11px]">{b.name}</span>
                <span className="flex gap-3 font-mono text-[10px] tabular-nums text-muted-foreground">
                  {typeof b['AC@1'] === 'number' && <span>AC@1 {b['AC@1'].toFixed(3)}</span>}
                  {typeof b['AC@3'] === 'number' && <span>AC@3 {b['AC@3'].toFixed(3)}</span>}
                  {typeof b.n === 'number' && <span>n={b.n}</span>}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rounded border border-border bg-card p-2 text-[11px] leading-snug text-muted-foreground">
            No baselines were run
            {baselines?.reason ? (
              <>
                {' '}
                — <span className="font-mono">{baselines.reason}</span>
              </>
            ) : (
              '.'
            )}{' '}
            Nothing is charted for them: an empty bar would read as a score of zero rather than an
            absent comparison.
          </p>
        )}
      </section>
    </div>
  )
}
