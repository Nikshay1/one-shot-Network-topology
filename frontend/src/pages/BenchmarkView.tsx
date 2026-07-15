/**
 * The numbers.
 *
 * Every value here comes from GET /benchmark. There are no hardcoded metrics —
 * the literals in this file are metric KEYS and labels, which must exist
 * somewhere to read the payload at all.
 *
 * Two shapes of reality this has to survive:
 *
 *  1. The four hero metrics do NOT live in one place. metrics is keyed
 *     `${suite}:${mode}`, and the two suites report different things: heldout/dev
 *     give AC@1 / AC@3 / Avg@5, synthetic gives precision@1 / precision@3 /
 *     red_herring_false_blame_rate / median_time_to_rca_s. So AC@1 and the
 *     false-blame rate come from different entries, and each tile names its
 *     source.
 *  2. When eval/results.json is missing the endpoint returns a DIFFERENT object:
 *     {runs: [], metrics: {}, note} with no redacted/generated_at/baselines. That
 *     is a normal state, not an error.
 *
 * And what is deliberately not here: any per-case "was it right?" badge. truth,
 * rank_of_truth and false_blame are redacted per run at the boundary. The
 * aggregate rates survive because they are computed FROM ground truth but
 * disclose it for no individual case — which is exactly what a benchmark page
 * needs, and the line this page does not cross.
 */
import { useEffect, useMemo, useState } from 'react'
import { getBenchmark } from '@/api/client'
import { MetricTile } from '@/components/MetricTile'
import { ComparisonChart } from '@/components/ComparisonChart'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { modeLabel } from '@/theme/tokens'
import type { BenchmarkMetrics, BenchmarkResponse } from '@/types/api'

const AGENTIC = 'agentic'

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

function seconds(v: number): string {
  return `${v.toFixed(1)}s`
}

function splitKey(key: string): { suite: string; mode: string } {
  const [suite = key, mode = ''] = key.split(':')
  return { suite, mode }
}

function EfficiencyTable({ byMode }: { byMode: Record<string, BenchmarkMetrics> }) {
  const rows = Object.entries(byMode)
    .map(([mode, m]) => ({ mode, eff: m.efficiency }))
    .filter((r) => r.eff && r.eff.n > 0)

  if (rows.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No efficiency data — the backend returns {'{n: 0}'} alone when a suite had no rows.
      </p>
    )
  }

  const cols = [
    { key: 'mean_tool_calls' as const, label: 'mean tool calls' },
    { key: 'mean_cost_points' as const, label: 'mean cost points' },
    { key: 'mean_expensive_ops' as const, label: 'mean expensive ops' },
    { key: 'mean_wall_clock_s' as const, label: 'mean wall clock' },
  ]

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-border bg-card">
            <th className="px-3 py-2 font-medium text-muted-foreground">mode</th>
            <th className="px-3 py-2 font-medium text-muted-foreground">n</th>
            {cols.map((c) => (
              <th key={c.key} className="px-3 py-2 font-medium text-muted-foreground">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ mode, eff }) => (
            <tr key={mode} className="border-b border-border/50 last:border-0">
              <td className="px-3 py-2 font-mono">{modeLabel(mode)}</td>
              <td className="px-3 py-2 font-mono tabular-nums">{eff!.n}</td>
              {cols.map((c) => (
                <td key={c.key} className="px-3 py-2 font-mono tabular-nums">
                  {typeof eff![c.key] === 'number' ? eff![c.key]!.toFixed(2) : '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function BenchmarkView() {
  const [data, setData] = useState<BenchmarkResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const [suite, setSuite] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getBenchmark()
      .then((res) => {
        if (!cancelled) setData(res)
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
  }, [nonce])

  const suites = useMemo(() => {
    if (!data) return []
    return [...new Set(Object.keys(data.metrics).map((k) => splitKey(k).suite))].sort()
  }, [data])

  const activeSuite = suite ?? suites[0] ?? null

  const byMode = useMemo(() => {
    if (!data || !activeSuite) return {}
    const out: Record<string, BenchmarkMetrics> = {}
    for (const [key, m] of Object.entries(data.metrics)) {
      const parsed = splitKey(key)
      if (parsed.suite === activeSuite) out[parsed.mode] = m
    }
    return out
  }, [data, activeSuite])

  // The hero tiles read from whichever suite reports each metric — they are not
  // all in one entry.
  const hero = useMemo(() => {
    if (!data) return null
    const find = (metric: keyof BenchmarkMetrics) => {
      for (const [key, m] of Object.entries(data.metrics)) {
        if (splitKey(key).mode !== AGENTIC) continue
        const value = m[metric]
        // `n` travels with the value: a rate over one case and a rate over
        // twenty-four render identically without it.
        if (typeof value === 'number') return { value, source: key, n: m.n }
      }
      return null
    }
    return {
      ac1: find('AC@1'),
      ac3: find('AC@3'),
      falseBlame: find('red_herring_false_blame_rate'),
      medianRca: find('median_time_to_rca_s'),
    }
  }, [data])

  if (loading) {
    return (
      <div className="space-y-4 p-6">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-72" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="space-y-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-4">
          <p className="text-sm text-rose-300">Could not load /benchmark — {error}</p>
          <Button size="sm" variant="outline" onClick={() => setNonce((n) => n + 1)}>
            Retry
          </Button>
        </div>
      </div>
    )
  }

  if (!data || Object.keys(data.metrics).length === 0) {
    return (
      <div className="p-6">
        <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-border">
          <p className="max-w-md p-6 text-center text-sm text-muted-foreground">
            {data?.note ?? 'No benchmark results yet.'}
            <span className="mt-2 block text-[11px] text-muted-foreground/70">
              The endpoint serves eval/results.json; until a benchmark has been run it returns an
              empty document rather than failing.
            </span>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5 p-6">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold tracking-tight">Benchmark</h1>
        <Badge variant="outline">{data.runs.length} runs</Badge>
        {data.redacted && (
          <Badge variant="outline" title={`stripped per run: ${data.redacted.join(', ')}`}>
            ground truth redacted
          </Badge>
        )}
        <div className="ml-auto flex gap-1">
          {suites.map((s) => (
            <Button
              key={s}
              size="sm"
              variant={s === activeSuite ? 'default' : 'outline'}
              onClick={() => setSuite(s)}
              aria-pressed={s === activeSuite}
            >
              {s}
            </Button>
          ))}
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricTile
          label="AC@1"
          value={hero?.ac1?.value}
          source={hero?.ac1?.source}
          n={hero?.ac1?.n}
          format={pct}
          hint="top-1 is the true root cause"
          emphasis
        />
        <MetricTile
          label="AC@3"
          value={hero?.ac3?.value}
          source={hero?.ac3?.source}
          n={hero?.ac3?.n}
          format={pct}
          hint="true cause in the top 3"
        />
        <MetricTile
          label="false-blame rate"
          value={hero?.falseBlame?.value}
          source={hero?.falseBlame?.source}
          n={hero?.falseBlame?.n}
          format={pct}
          hint="red herrings blamed — lower is better"
        />
        <MetricTile
          label="median time-to-RCA"
          value={hero?.medianRca?.value}
          source={hero?.medianRca?.source}
          n={hero?.medianRca?.n}
          format={seconds}
          hint="median wall clock to a verdict"
        />
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">
          {activeSuite}: agentic vs fixed vs ablations
        </h2>
        <ComparisonChart byMode={byMode} suite={activeSuite ?? ''} baselines={data.baselines} />
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">Agent efficiency</h2>
        <EfficiencyTable byMode={byMode} />
        {Object.values(byMode)[0]?.fixed_budget_note && (
          <p className="text-[11px] leading-snug text-muted-foreground/70">
            {Object.values(byMode)[0]!.fixed_budget_note}
          </p>
        )}
      </section>

      <p className="text-[10px] leading-snug text-muted-foreground/60">
        Per-run ground truth is stripped at the API boundary, so there is no per-case
        &ldquo;was it right?&rdquo; column here. The aggregate rates survive because they disclose
        no individual case&apos;s answer.
      </p>
    </div>
  )
}
