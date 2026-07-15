/**
 * The bottom strip: where in the incident we are.
 *
 * The case window is DERIVED. No endpoint exposes one (/cases returns only
 * counts), and LedgerRecord.ts_range is useless for it — nearly every backend
 * writer passes (0.0, 0.0). So the window is the union of the anomaly windows
 * (which arrive complete and survive a capped event stream) and any event
 * timestamps seen. The playhead is the latest event ts, which is the honest
 * meaning of "how far the replay has got".
 */
import { useMemo } from 'react'
import { useRunStore, useAnomalies, useCaseWindow, useConfigChanges } from '@/store/useRunStore'
import { DENSITY_BUCKET_S } from '@/store/runStore'
import { formatClock, summarizePayload } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { ComponentId, EventEnvelope } from '@/types/events'

export interface TimelineProps {
  onSelectComponent: (id: ComponentId) => void
  height?: number
}

function pct(value: number, start: number, end: number): number {
  if (end <= start) return 0
  return Math.min(100, Math.max(0, ((value - start) / (end - start)) * 100))
}

export function Timeline({ onSelectComponent, height = 76 }: TimelineProps) {
  const window = useCaseWindow()
  const anomalies = useAnomalies()
  const configs = useConfigChanges()
  const density = useRunStore((s) => s.density)
  const playhead = useRunStore((s) => s.tsMax)
  const status = useRunStore((s) => s.status)

  const ribbon = useMemo(() => {
    if (!window) return []
    const buckets = [...density.entries()].map(([bucket, count]) => ({
      ts: bucket * DENSITY_BUCKET_S,
      count,
    }))
    const max = buckets.reduce((m, b) => Math.max(m, b.count), 0)
    return buckets
      .filter((b) => b.ts >= window.start - DENSITY_BUCKET_S && b.ts <= window.end)
      .map((b) => ({
        left: pct(b.ts, window.start, window.end),
        intensity: max > 0 ? b.count / max : 0,
        count: b.count,
        ts: b.ts,
      }))
  }, [density, window])

  if (!window) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-border bg-card text-xs text-muted-foreground"
        style={{ height }}
      >
        {status === 'idle' || status === 'connecting'
          ? 'Timeline appears once events arrive.'
          : 'No timed events yet.'}
      </div>
    )
  }

  const bucketWidth = Math.max(0.4, pct(window.start + DENSITY_BUCKET_S, window.start, window.end))

  return (
    <div className="rounded-lg border border-border bg-card p-2" style={{ height }}>
      <div className="mb-1 flex items-center justify-between text-[10px] text-muted-foreground">
        <span className="font-mono">{formatClock(window.start)}</span>
        <span className="uppercase tracking-wide">
          incident window · derived from anomaly windows
        </span>
        <span className="font-mono">{formatClock(window.end)}</span>
      </div>

      {/*
        Two tracks, not one overlay. Anomaly windows are wide and overlapping —
        painted full-height they swallowed the density bars and the whole strip
        turned into an amber smear. Density gets the tall track; anomalies get a
        thin band beneath it, where their extent is still readable.
      */}
      <div className="relative h-10 overflow-hidden rounded-md border border-border/70 bg-[#faf8f6]">
        {/* Event density — bars from the floor, so height means volume. */}
        {ribbon.map((b) => (
          <div
            key={b.ts}
            className="absolute bottom-2.5 bg-primary/70"
            style={{
              left: `${b.left}%`,
              width: `${bucketWidth}%`,
              height: `${18 + b.intensity * 60}%`,
              opacity: 0.35 + b.intensity * 0.5,
            }}
            title={`${b.count} events at ${formatClock(b.ts)}`}
          />
        ))}

        {/* Anomaly windows — their own track along the bottom. */}
        <div className="absolute inset-x-0 bottom-0 h-2.5 border-t border-border/60 bg-white/60" />
        {anomalies.map((a) => (
          <div
            key={a.anomaly_id}
            className="absolute bottom-0 h-2.5 rounded-sm bg-amber-500/70"
            style={{
              left: `${pct(a.window.start, window.start, window.end)}%`,
              width: `${Math.max(0.4, pct(a.window.end, window.start, window.end) - pct(a.window.start, window.start, window.end))}%`,
            }}
            title={`${a.component_id}: ${a.summary}`}
          />
        ))}

        {/* Config changes: clickable diamonds. The demo's smoking gun. */}
        {configs.map((ev: EventEnvelope) => {
          const risky = ev.payload.kind === 'config' && ev.payload.risky === true
          return (
            <button
              key={ev.event_id}
              type="button"
              onClick={() => onSelectComponent(ev.component_id)}
              title={`${ev.component_id} · ${summarizePayload(ev.payload)}`}
              aria-label={`config change on ${ev.component_id}: ${summarizePayload(ev.payload)}`}
              className={cn(
                'absolute top-2 h-2.5 w-2.5 -translate-x-1/2 rotate-45 border transition-transform hover:scale-150',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                risky ? 'border-amber-700 bg-amber-400' : 'border-stone-500 bg-stone-300',
              )}
              style={{ left: `${pct(ev.ts, window.start, window.end)}%` }}
            />
          )
        })}

        {/* Playhead. */}
        {playhead !== null && (
          <div
            className="absolute inset-y-0 w-px bg-foreground"
            style={{ left: `${pct(playhead, window.start, window.end)}%` }}
          >
            <div className="absolute -top-px left-1/2 h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-foreground" />
          </div>
        )}
      </div>
    </div>
  )
}
