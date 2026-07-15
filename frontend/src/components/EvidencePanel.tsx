/**
 * Three columns: what holds, what merely co-occurs, what's missing.
 *
 * The columns mirror backend/rank/tiers.py's own criteria rather than bucketing
 * ledger facts by kind — see lib/evidence.ts for why that doesn't work (six of
 * twelve kinds have no production writer, and `?hypothesis_id=X` returns almost
 * nothing).
 */
import { useMemo } from 'react'
import { useAnomalies } from '@/store/useRunStore'
import { useLedger } from '@/hooks/useLedger'
import { buildEvidence } from '@/lib/evidence'
import type { EvidenceItem } from '@/lib/evidence'
import { EventChips } from '@/components/EventChip'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { RankedHypothesis } from '@/types/hypothesis'

function Column({
  title,
  items,
  accent,
  empty,
}: {
  title: string
  items: EvidenceItem[]
  accent: string
  empty: string
}) {
  return (
    <section className="min-w-0 flex-1 space-y-2">
      <header className="flex items-center gap-2">
        <span className={cn('h-2 w-2 rounded-full', accent)} aria-hidden />
        <h4 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          {title}
        </h4>
        <Badge variant="outline">{items.length}</Badge>
      </header>

      {items.length === 0 ? (
        <p className="text-[11px] leading-snug text-muted-foreground/70">{empty}</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.id} className="rounded border border-border bg-background/40 p-2">
              <p className="text-[11px] leading-snug text-muted-foreground">{item.text}</p>
              <EventChips eventIds={item.eventIds} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export interface EvidencePanelProps {
  runId: string
  hypothesis: RankedHypothesis
  /** Bumped on pipeline_done so coverage gaps refetch once the run is complete. */
  ledgerNonce?: number
}

export function EvidencePanel({ runId, hypothesis, ledgerNonce = 0 }: EvidencePanelProps) {
  const anomalies = useAnomalies()

  // coverage_gap facts carry hypothesis_id=None (tiers.py accepts the id and
  // never passes it), so they cannot be fetched per hypothesis — fetch by kind
  // and join on component_ids inside buildEvidence.
  const { facts: coverageGaps } = useLedger(runId, { kind: 'coverage_gap' }, ledgerNonce)

  const evidence = useMemo(
    () => buildEvidence({ hypothesis, anomalies, coverageGaps }),
    [hypothesis, anomalies, coverageGaps],
  )

  return (
    <div className="space-y-3 border-t border-border pt-3">
      <div className="flex flex-col gap-4 sm:flex-row">
        <Column
          title="Confirmed evidence"
          items={evidence.confirmed}
          accent="bg-tier-confirmed"
          empty="Nothing here meets a confirmation criterion yet."
        />
        <Column
          title="Correlated signals"
          items={evidence.correlated}
          accent="bg-tier-correlated"
          empty="No co-occurring signals on this suspect."
        />
        <Column
          title="Missing evidence"
          items={evidence.missing}
          accent="bg-tier-missing"
          empty="Nothing is blocking confirmation."
        />
      </div>

      <p className="text-[10px] leading-snug text-muted-foreground/60">
        Columns mirror the backend&apos;s tier criteria (cited evidence resolves · topology path to
        every observed symptom · temporal precedence · twin verdict = match), not a per-fact label —
        the backend does not classify facts. The tier on the card is always the backend&apos;s.
      </p>
    </div>
  )
}
