import { useEffect, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useAnomalies, useFeed, useRunStore } from '@/store/useRunStore'
import type { FeedItem } from '@/store/runStore'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  METHOD_LABEL,
  SOURCE_CLASS,
  SOURCE_GLYPH,
  formatClock,
  formatScore,
  summarizePayload,
} from '@/lib/format'
import { cn } from '@/lib/utils'
import type { AnomalyEvent } from '@/types/anomaly'

const ROW_HEIGHT = 30

function ComponentChip({ id }: { id: string }) {
  return (
    <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-secondary-foreground">
      {id}
    </span>
  )
}

function FeedRow({ item }: { item: FeedItem }) {
  if (item.kind === 'anomaly') {
    const a = item.anomaly
    return (
      <div className="flex h-full items-center gap-2 border-l-2 border-amber-400 bg-amber-500/10 px-2 text-xs">
        <span className="w-14 shrink-0 font-mono text-[10px] text-amber-300/70">
          {formatClock(a.window.start)}
        </span>
        <span className={cn('w-3 shrink-0 text-center', 'text-amber-400')} aria-hidden>
          ◆
        </span>
        <ComponentChip id={a.component_id} />
        <span className="truncate text-amber-200">{a.summary}</span>
        <Badge variant="anomaly" className="ml-auto shrink-0">
          {formatScore(a.score)}
        </Badge>
      </div>
    )
  }

  const e = item.event
  return (
    <div className="flex h-full items-center gap-2 px-2 text-xs">
      <span className="w-14 shrink-0 font-mono text-[10px] text-muted-foreground">
        {formatClock(e.ts)}
      </span>
      <span
        className={cn('w-3 shrink-0 text-center font-mono', SOURCE_CLASS[e.source])}
        title={e.source}
        aria-label={e.source}
      >
        {SOURCE_GLYPH[e.source]}
      </span>
      <ComponentChip id={e.component_id} />
      <span className="truncate text-muted-foreground">{summarizePayload(e.payload)}</span>
    </div>
  )
}

function AnomalyRail({ anomalies }: { anomalies: AnomalyEvent[] }) {
  return (
    <aside className="flex w-80 shrink-0 flex-col rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Anomalies
        </h3>
        <Badge variant={anomalies.length ? 'anomaly' : 'outline'}>{anomalies.length}</Badge>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {anomalies.length === 0 ? (
          <p className="p-3 text-xs text-muted-foreground">None detected yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {anomalies.map((a) => (
              <li key={a.anomaly_id} className="space-y-1.5 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] text-foreground">{a.component_id}</span>
                  <Badge variant="anomaly">{formatScore(a.score)}</Badge>
                </div>
                <p className="text-xs leading-snug text-muted-foreground">{a.summary}</p>
                <div className="flex flex-wrap items-center gap-1">
                  <Badge variant="outline" title="detector method">
                    {METHOD_LABEL[a.method]}
                  </Badge>
                  <Badge variant="outline">{a.source}</Badge>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  )
}

export interface LiveFeedProps {
  /**
   * 'split' shows the feed beside a dedicated anomalies rail; 'rail' shows the
   * feed alone, which is enough because anomalies are already interleaved into
   * it in amber — that is what the reference's "Incident Live Feed" card is.
   */
  layout?: 'split' | 'rail'
}

/**
 * The "something is happening" view: a virtualized tail of the run bus.
 *
 * The store keeps only the last 500 arrivals, so this never renders a real run's
 * ~186k events — it is a tail, and the counter above it is the honest total.
 */
export function LiveFeed({ layout = 'split' }: LiveFeedProps = {}) {
  const feed = useFeed()
  const anomalies = useAnomalies()
  const eventsSeen = useRunStore((s) => s.eventsSeen)
  const status = useRunStore((s) => s.status)

  const parentRef = useRef<HTMLDivElement>(null)
  const [follow, setFollow] = useState(true)

  const virtualizer = useVirtualizer({
    count: feed.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  })

  // Follow the tail unless the presenter has scrolled up to read something.
  useEffect(() => {
    if (follow && feed.length > 0) {
      virtualizer.scrollToIndex(feed.length - 1, { align: 'end' })
    }
  }, [feed.length, follow, virtualizer])

  const onScroll = () => {
    const el = parentRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < ROW_HEIGHT * 2
    setFollow(atBottom)
  }

  const items = virtualizer.getVirtualItems()

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      <section className="flex min-h-0 min-w-0 flex-1 flex-col rounded-xl border border-border bg-card">
        <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Incident feed
          </h3>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
              {eventsSeen.toLocaleString()} events
              {feed.length > 0 && eventsSeen > feed.length && ` · showing last ${feed.length}`}
            </span>
            {!follow && (
              <Button size="sm" variant="outline" onClick={() => setFollow(true)}>
                Follow
              </Button>
            )}
          </div>
        </div>

        <div ref={parentRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-auto">
          {feed.length === 0 ? (
            <p className="p-3 text-xs text-muted-foreground">
              {status === 'idle' || status === 'connecting'
                ? 'Waiting for the stream…'
                : 'No events yet.'}
            </p>
          ) : (
            <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
              {items.map((virtualRow) => {
                const item = feed[virtualRow.index]!
                return (
                  <div
                    key={virtualRow.key}
                    className="absolute inset-x-0 top-0"
                    style={{
                      height: virtualRow.size,
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                  >
                    <FeedRow item={item} />
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </section>

      {layout === 'split' && <AnomalyRail anomalies={anomalies} />}
    </div>
  )
}
