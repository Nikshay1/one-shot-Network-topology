/**
 * The centerpiece: the dependency graph lighting up as the incident unfolds.
 *
 * Laid out like the reference — map on the left, a live rail on the right,
 * timeline underneath. The rail carries the incident feed until you pick a
 * component, then it carries that component's detail; they are the same slot
 * because you are never reading both.
 */
import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { TopologyGraph } from '@/components/TopologyGraph'
import { Timeline } from '@/components/Timeline'
import { ComponentDrawer } from '@/components/ComponentDrawer'
import { LiveFeed } from '@/components/LiveFeed'
import { useTopology } from '@/hooks/useTopology'
import { useRunStore, useTopHypothesis } from '@/store/useRunStore'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import type { ComponentId } from '@/types/events'

const LEGEND = [
  { className: 'border-amber-500 bg-amber-100', label: 'anomalous' },
  { className: 'border-primary bg-primary', label: 'suspect (rank 1)' },
  { className: 'border-cyan-700 bg-cyan-50', label: 'blast radius' },
  { className: 'border-tier-confirmed bg-tier-confirmed/15', label: 'counterfactual-unchanged' },
  { className: 'border-dashed border-stone-400 bg-transparent', label: 'uninstrumented' },
] as const

function Legend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {LEGEND.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span className={cn('h-2.5 w-2.5 rounded-full border-2', item.className)} aria-hidden />
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

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
        <Legend />
        {top && (
          <Badge variant={top.tier}>
            suspect: {top.suspect_component} · {top.tier}
          </Badge>
        )}
      </div>

      <div className="flex min-h-0 flex-1 gap-3">
        <section className="relative min-h-0 min-w-0 flex-1 overflow-hidden rounded-xl border border-border bg-card">
          {error ? (
            <div className="flex h-full items-center justify-center p-6 text-center">
              <p className="text-sm text-primary">Could not load topology — {error}</p>
            </div>
          ) : loading || !topology ? (
            <div className="h-full p-4">
              <Skeleton className="h-full w-full" />
            </div>
          ) : (
            <TopologyGraph topology={topology} selected={selected} onSelect={setSelected} />
          )}

          {status === 'idle' && (
            <p className="absolute bottom-3 left-3 text-[11px] text-muted-foreground">
              Waiting for the run to start.
            </p>
          )}
        </section>

        {/* One rail, two jobs: the feed until you pick a component, then its detail. */}
        {selected ? (
          <ComponentDrawer
            runId={runId}
            component={selected}
            topology={topology}
            onClose={() => setSelected(null)}
            ledgerNonce={status === 'done' ? 1 : 0}
          />
        ) : (
          <div className="flex w-[380px] shrink-0">
            <LiveFeed layout="rail" />
          </div>
        )}
      </div>

      <div className="shrink-0">
        <Timeline onSelectComponent={setSelected} />
      </div>
    </div>
  )
}
