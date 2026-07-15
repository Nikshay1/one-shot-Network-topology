/**
 * The judgment view.
 *
 * Everything on this page is the backend's: rank order, score, tier, tier_reason.
 * The only thing computed here is which evidence explains which criterion, and
 * that mirrors backend/rank/tiers.py rather than inventing a scheme.
 */
import { LayoutGroup } from 'framer-motion'
import { HypothesisCard } from '@/components/HypothesisCard'
import { ImpactBadge } from '@/components/ImpactBadge'
import { useClearedComponents, useRankedHypotheses, useRunStore } from '@/store/useRunStore'
import { Badge } from '@/components/ui/badge'

export function VerdictView({ runId }: { runId: string }) {
  // Backend rank order. Never re-sorted here — not by score, not by tier.
  const ranked = useRankedHypotheses()
  const cleared = useClearedComponents()
  const status = useRunStore((s) => s.status)
  const errorInfo = useRunStore((s) => s.errorInfo)
  const tierChanges = useRunStore((s) => s.tierChanges.length)

  const done = status === 'done'

  if (ranked.length === 0) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-border">
        <p className="max-w-sm p-8 text-center text-sm text-muted-foreground">
          {status === 'error'
            ? `The run failed at stage ${errorInfo?.stage ?? '?'}, so there is no verdict.`
            : status === 'idle' || status === 'connecting'
              ? 'Waiting for the run to start.'
              : 'No hypotheses ranked yet — the pipeline reaches ranking after detection and localization.'}
        </p>
      </div>
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Badge variant="outline">
          {ranked.length} hypothes{ranked.length === 1 ? 'is' : 'es'}
        </Badge>
        <ImpactBadge />
        {tierChanges > 0 && (
          <Badge variant="outline" title="tier_changed events received from the ranking stage">
            {tierChanges} tier change{tierChanges === 1 ? '' : 's'}
          </Badge>
        )}
        {!done && (
          <Badge variant="outline" className="text-muted-foreground">
            still ranking — cards re-order as the backend rescores
          </Badge>
        )}
      </div>

      <LayoutGroup>
        <ul className="space-y-2">
          {ranked.map((h, i) => (
            <HypothesisCard
              key={h.hypothesis_id}
              runId={runId}
              hypothesis={h}
              ranked={ranked}
              cleared={cleared.get(h.suspect_component)}
              showCounterfactual={i === 0}
              runDone={done}
              ledgerNonce={done ? 1 : 0}
            />
          ))}
        </ul>
      </LayoutGroup>
    </div>
  )
}
