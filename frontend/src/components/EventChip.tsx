/**
 * A cited event id, clickable, opening the raw event.
 *
 * CONTRACT GAP: there is no endpoint that fetches an event by id. The API's 14
 * routes have no /event/{id}, so `client.ts` has nothing to call and "GET via
 * client, cached" is not available. The SSE stream is the only source of events.
 *
 * What makes this work anyway: the store pins any event the moment something
 * cites it (see pinCited in runStore), which survives the 500-event ring
 * buffer's eviction. That covers every id an anomaly or hypothesis references,
 * because the server backfills dropped-but-cited events immediately before the
 * anomalies. When an id genuinely isn't held — a run joined late, a reload — we
 * say so instead of showing a spinner forever.
 */
import { useState } from 'react'
import { useRunStore } from '@/store/useRunStore'
import { SOURCE_CLASS, SOURCE_GLYPH, formatClock, summarizePayload } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { EventId } from '@/types/events'

export function EventChip({ eventId }: { eventId: EventId }) {
  const [open, setOpen] = useState(false)
  const event = useRunStore((s) => s.pinnedEvents.get(eventId) ?? s.events.get(eventId))

  return (
    <span className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(
          'rounded border border-border bg-background px-1 py-0.5 font-mono text-[10px] text-muted-foreground',
          'transition-colors hover:border-ring hover:text-foreground',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        )}
        title={event ? summarizePayload(event.payload) : 'event not held locally'}
      >
        {eventId}
      </button>

      {open && (
        <span
          role="dialog"
          aria-label={`raw event ${eventId}`}
          className="absolute left-0 top-full z-50 mt-1 block w-80 rounded-md border border-border bg-popover p-2 shadow-xl"
        >
          {event ? (
            <>
              <span className="mb-1 flex items-center gap-2">
                <span className={cn('font-mono text-[10px]', SOURCE_CLASS[event.source])}>
                  {SOURCE_GLYPH[event.source]} {event.source}
                </span>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {event.component_id} · {formatClock(event.ts)}
                </span>
              </span>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-background p-1.5 font-mono text-[10px] leading-snug text-muted-foreground">
                {JSON.stringify(event.payload, null, 2)}
              </pre>
            </>
          ) : (
            <span className="block text-[11px] leading-snug text-muted-foreground">
              This event isn&apos;t held locally. Events arrive only on the run stream — the API has
              no endpoint to fetch one by id — so an event cited before you attached, or before a
              reload, can&apos;t be shown.
            </span>
          )}
        </span>
      )}
    </span>
  )
}

export function EventChips({ eventIds }: { eventIds: EventId[] }) {
  if (eventIds.length === 0) return null
  return (
    <span className="mt-1 flex flex-wrap gap-1">
      {eventIds.map((id) => (
        <EventChip key={id} eventId={id} />
      ))}
    </span>
  )
}
