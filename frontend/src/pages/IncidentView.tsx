/**
 * The centerpiece: the dependency graph lighting up as the incident unfolds,
 * the timeline underneath, the feed and anomalies rail beside it.
 */
import { useCallback, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { TopologyGraph } from '@/components/TopologyGraph'
import { Timeline } from '@/components/Timeline'
import { ComponentDrawer } from '@/components/ComponentDrawer'
import { LiveFeed } from '@/components/LiveFeed'
import { useTopology } from '@/hooks/useTopology'
import { useRunStore, useTopHypothesis } from '@/store/useRunStore'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { ComponentId } from '@/types/events'

const LEGEND = [
  { className: 'border-amber-400 bg-amber-400/70', label: 'anomalous' },
  { className: 'border-rose-500 bg-rose-500/80', label: 'suspect (rank 1)' },
  { className: 'border-cyan-400 bg-cyan-400/30', label: 'blast radius' },
  { className: 'border-tier-confirmed bg-transparent', label: 'counterfactual-unchanged' },
  { className: 'border-dashed border-slate-500 bg-transparent', label: 'uninstrumented' },
] as const

function Legend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {LEGEND.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span className={cn('h-2.5 w-2.5 rounded-full border', item.className)} aria-hidden />
          <span className="text-[10px] text-muted-foreground">{item.label}</span>
        </li>
      ))}
    </ul>
  )
}

export function IncidentView({ runId }: { runId: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const caseId = useRunStore((s) => s.caseId) ?? runId
  const status = useRunStore((s) => s.status)
  const { topology, loading, error } = useTopology(caseId)
  const top = useTopHypothesis()

  // Selection lives in the URL so the presenter can deep-link to a component
  // mid-story instead of clicking to find it again.
  const selected = (searchParams.get('component') as ComponentId | null) ?? null
  const setSelected = useCallback(
    (id: ComponentId | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (id) next.set('component', id)
          else next.delete('component')
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const [showFeed, setShowFeed] = useState(false)

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
        <Legend />
        <div className="flex items-center gap-2">
          {top && (
            <Badge variant={top.tier}>
              suspect: {top.suspect_component} · {top.tier}
            </Badge>
          )}
          <Button size="sm" variant="outline" onClick={() => setShowFeed((v) => !v)}>
            {showFeed ? 'Show graph' : 'Show feed'}
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-3">
        {showFeed ? (
          <LiveFeed />
        ) : (
          <>
            <section className="relative min-h-0 min-w-0 flex-1 overflow-hidden rounded-lg border border-border bg-card">
              {error ? (
                <div className="flex h-full items-center justify-center p-6 text-center">
                  <p className="text-sm text-rose-300">Could not load topology — {error}</p>
                </div>
              ) : loading || !topology ? (
                <div className="flex h-full items-center justify-center">
                  <p className="text-sm text-muted-foreground">Loading topology…</p>
                </div>
              ) : (
                <TopologyGraph topology={topology} selected={selected} onSelect={setSelected} />
              )}

              {status === 'idle' && (
                <p className="absolute bottom-2 left-2 text-[11px] text-muted-foreground">
                  Waiting for the run to start.
                </p>
              )}
            </section>

            {selected && (
              <ComponentDrawer
                runId={runId}
                component={selected}
                topology={topology}
                onClose={() => setSelected(null)}
                ledgerNonce={status === 'done' ? 1 : 0}
              />
            )}
          </>
        )}
      </div>

      <div className="shrink-0">
        <Timeline onSelectComponent={setSelected} />
      </div>
    </div>
  )
}
