/**
 * Node click → everything the run knows about one component.
 *
 * Three sources, all backend: anomalies (SSE), metric series (SSE — the only
 * source there is), and ledger facts (GET /run/{id}/ledger?component_id=X).
 * Nothing here is inferred except the "uninstrumented" note, which reads the
 * topology's own `instrumented` attribute.
 */
import { useMemo } from 'react'
import { useAnomaliesFor, useClearedComponents, useSeriesFor } from '@/store/useRunStore'
import { useLedger } from '@/hooks/useLedger'
import { MetricSparkline } from '@/components/MetricSparkline'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { METHOD_LABEL, formatClock, formatScore } from '@/lib/format'
import { clearedReason, isInstrumented } from '@/lib/graph'
import type { TopologyGraph } from '@/types/api'
import type { ComponentId } from '@/types/events'

export interface ComponentDrawerProps {
  runId: string
  component: ComponentId | null
  topology: TopologyGraph | null
  onClose: () => void
  /** Bumped on pipeline_done so the facts refetch when the ledger is complete. */
  ledgerNonce?: number
}

export function ComponentDrawer({
  runId,
  component,
  topology,
  onClose,
  ledgerNonce = 0,
}: ComponentDrawerProps) {
  const anomalies = useAnomaliesFor(component)
  const series = useSeriesFor(component)
  const cleared = useClearedComponents()
  const { facts, loading } = useLedger(
    runId,
    component ? { component_id: component } : {},
    ledgerNonce,
  )

  const windows = useMemo(() => anomalies.map((a) => a.window), [anomalies])
  if (!component) return null

  const instrumented = topology ? isInstrumented(topology, component) : true
  const clearedVerdict = cleared.get(component)

  return (
    <aside
      className="flex w-96 shrink-0 flex-col overflow-hidden rounded-lg border border-border bg-card"
      aria-label={`details for ${component}`}
    >
      <div className="flex items-start justify-between gap-2 border-b border-border p-3">
        <div className="space-y-1">
          <h3 className="font-mono text-sm">{component}</h3>
          <div className="flex flex-wrap gap-1">
            {!instrumented && (
              <Badge variant="outline" title="topology node has instrumented: false">
                uninstrumented
              </Badge>
            )}
            {clearedVerdict && (
              <Badge
                variant="outline"
                className="border-tier-confirmed/40 text-tier-confirmed"
                title={clearedReason(clearedVerdict.anomalies_still_explained_pct)}
              >
                ✓ counterfactual-unchanged
              </Badge>
            )}
            <Badge variant={anomalies.length ? 'anomaly' : 'outline'}>
              {anomalies.length} anomal{anomalies.length === 1 ? 'y' : 'ies'}
            </Badge>
          </div>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="close details">
          ✕
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
        {!instrumented && (
          <p className="rounded border border-border bg-background p-2 text-[11px] leading-snug text-muted-foreground">
            This component emits no telemetry, so an absent symptom here is not evidence of health —
            it is evidence of nothing. The backend marks predicted symptoms on it{' '}
            <span className="font-mono">observed: null</span> rather than false.
          </p>
        )}

        {clearedVerdict && (
          <p className="rounded border border-tier-confirmed/30 bg-tier-confirmed/5 p-2 text-[11px] leading-snug text-tier-confirmed">
            {clearedReason(clearedVerdict.anomalies_still_explained_pct)}
          </p>
        )}

        <section className="space-y-2">
          <h4 className="text-[11px] uppercase tracking-wide text-muted-foreground">Anomalies</h4>
          {anomalies.length === 0 ? (
            <p className="text-xs text-muted-foreground">None on this component.</p>
          ) : (
            <ul className="space-y-2">
              {anomalies.map((a) => (
                <li key={a.anomaly_id} className="rounded border border-border p-2">
                  <div className="flex items-center justify-between gap-2">
                    <Badge variant="outline">{METHOD_LABEL[a.method]}</Badge>
                    <Badge variant="anomaly">{formatScore(a.score)}</Badge>
                  </div>
                  <p className="mt-1 text-xs leading-snug text-muted-foreground">{a.summary}</p>
                  <p className="mt-1 font-mono text-[10px] text-muted-foreground/70">
                    {formatClock(a.window.start)} – {formatClock(a.window.end)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="space-y-2">
          <h4 className="text-[11px] uppercase tracking-wide text-muted-foreground">Metrics</h4>
          {series.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {instrumented
                ? 'No metric samples seen on the stream yet.'
                : 'No metrics — component is uninstrumented.'}
            </p>
          ) : (
            series.map((s) => (
              <div key={`${s.component_id}|${s.name}`} className="space-y-0.5">
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
            ))
          )}
        </section>

        <section className="space-y-2">
          <h4 className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Ledger facts
          </h4>
          {loading ? (
            <p className="text-xs text-muted-foreground">Loading…</p>
          ) : facts.length === 0 ? (
            <p className="text-xs text-muted-foreground">No facts filed for this component yet.</p>
          ) : (
            <ul className="space-y-2">
              {facts.map((f) => (
                <li key={f.fact_id} className="rounded border border-border p-2">
                  <div className="flex items-center justify-between gap-2">
                    <Badge variant="outline">{f.kind}</Badge>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {formatScore(f.confidence)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs leading-snug text-muted-foreground">{f.statement}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </aside>
  )
}
