import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ScoreBreakdown } from '@/components/ScoreBreakdown'
import { EvidencePanel } from '@/components/EvidencePanel'
import { CounterfactualToggle } from '@/components/CounterfactualToggle'
import { EventChip } from '@/components/EventChip'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { clearedReason } from '@/lib/graph'
import { cn } from '@/lib/utils'
import type { RankedHypothesis, Tier } from '@/types/hypothesis'
import type { ClearedVerdict } from '@/lib/graph'

const MEDAL: Record<number, string> = { 1: '①', 2: '②', 3: '③' }

/**
 * The tier pill. Flips and glows when the tier changes — that transition IS the
 * "it just went CONFIRMED" moment, and it fires off `tier_changed` /
 * `hypothesis_ranked` alone (rule 3: the UI never decides a tier).
 */
function TierPill({ tier }: { tier: Tier }) {
  const [flash, setFlash] = useState(false)
  const previous = useRef<Tier | null>(null)

  useEffect(() => {
    // Skip the mount: only a genuine change is worth animating.
    if (previous.current !== null && previous.current !== tier) {
      setFlash(true)
      const timer = setTimeout(() => setFlash(false), 1400)
      return () => clearTimeout(timer)
    }
    previous.current = tier
    return undefined
  }, [tier])

  useEffect(() => {
    previous.current = tier
  }, [tier])

  return (
    <motion.span
      key={tier}
      initial={previous.current === null ? false : { rotateX: -90, opacity: 0 }}
      animate={{ rotateX: 0, opacity: 1 }}
      transition={{ duration: 0.35, ease: 'easeOut' }}
      className="inline-block"
      data-testid="tier-pill"
      data-tier={tier}
    >
      <Badge
        variant={tier}
        className={cn('transition-shadow', flash && 'shadow-[0_0_0_3px_currentColor]/30')}
      >
        {tier}
      </Badge>
    </motion.span>
  )
}

export interface HypothesisCardProps {
  runId: string
  hypothesis: RankedHypothesis
  ranked: RankedHypothesis[]
  cleared?: ClearedVerdict | undefined
  /** Only the top card carries the counterfactual toggle. */
  showCounterfactual: boolean
  runDone: boolean
  ledgerNonce?: number
}

export function HypothesisCard({
  runId,
  hypothesis: h,
  ranked,
  cleared,
  showCounterfactual,
  runDone,
  ledgerNonce = 0,
}: HypothesisCardProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <motion.li
      layout
      // The layout shuffle is the demo moment: when a rescore demotes the
      // red herring, the card physically falls past the real cause.
      transition={{ type: 'spring', stiffness: 320, damping: 34 }}
      className="rounded-lg border border-border bg-card p-3"
      data-testid="hypothesis-card"
      data-hypothesis-id={h.hypothesis_id}
      data-rank={h.rank}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span
            className="mt-0.5 shrink-0 text-lg leading-none text-muted-foreground"
            aria-label={`rank ${h.rank}`}
            title={`rank ${h.rank}`}
          >
            {MEDAL[h.rank] ?? `#${h.rank}`}
          </span>
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-sm">{h.suspect_component}</span>
              <TierPill tier={h.tier} />
              {h.fault_type_guess && <Badge variant="outline">{h.fault_type_guess}</Badge>}
              {cleared && (
                <Badge
                  variant="outline"
                  className="border-tier-confirmed/40 text-tier-confirmed"
                  title={clearedReason(cleared.anomalies_still_explained_pct)}
                >
                  ✓ counterfactual-unchanged
                </Badge>
              )}
            </div>
            <p className="text-xs leading-snug text-foreground/90">{h.statement}</p>
            {/* The backend's own words for why this tier — never paraphrased. */}
            <p className="text-[11px] leading-snug text-muted-foreground">{h.tier_reason}</p>
          </div>
        </div>

        <span className="shrink-0 font-mono text-sm tabular-nums">{h.score.toFixed(2)}</span>
      </div>

      <div className="mt-2.5 space-y-2">
        <ScoreBreakdown breakdown={h.score_breakdown} score={h.score} compact={!expanded} />

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => setExpanded((v) => !v)}>
            {expanded ? 'Hide evidence' : 'Show evidence'}
          </Button>
          {h.trigger_event_id && (
            <span className="flex items-center gap-1">
              <span className="text-[10px] text-muted-foreground">trigger</span>
              <EventChip eventId={h.trigger_event_id} />
            </span>
          )}
          {h.twin && (
            <Badge variant="outline" title={`twin run ${h.twin.run}`}>
              twin {h.twin.verdict} · {h.twin.similarity.toFixed(2)}
            </Badge>
          )}
        </div>

        {expanded && (
          <EvidencePanel runId={runId} hypothesis={h} ledgerNonce={ledgerNonce} />
        )}

        {showCounterfactual && (
          <CounterfactualToggle
            runId={runId}
            hypothesis={h}
            ranked={ranked}
            enabled={runDone}
          />
        )}
      </div>
    </motion.li>
  )
}
