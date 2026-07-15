/**
 * "Remove from graph" — the live counterfactual.
 *
 * WHAT THE ENDPOINT ACTUALLY RETURNS
 *
 * POST /run/{id}/counterfactual → {removed, anomalies_still_explained_pct,
 * affected_hypotheses}. Three keys. It does NOT re-rank and returns no
 * hypothesis objects, so the "without" side cannot show a re-ordered list: doing
 * that would mean computing a ranking in the UI, which rule 3 forbids and which
 * would be a guess besides. What it shows instead is the measurement the backend
 * did make — how much of the incident survives the removal — and which
 * hypotheses the backend names as affected.
 *
 * `affected_hypotheses` is a misnomer worth knowing: it is
 * `[h for h in hyps if h.suspect_component == removed]` (app.py:406-408) — the
 * hypothesis that names the removed component as ITS OWN suspect, so 0 or 1
 * element. The other candidates, the ones that would actually pick up the
 * explanatory load, are never returned.
 *
 * The endpoint is read-only (no ledger write, no run mutation — the sandboxed
 * twin of the agent's run_counterfactual tool) and idempotent, so toggling is
 * safe. It is gated on `done` because mid-run it reads rec.verdict, which is
 * empty, and returns an answer that changes once the run completes.
 */
import { useState } from 'react'
import { postCounterfactual } from '@/api/client'
import { REDUNDANT_PCT } from '@/lib/graph'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { CounterfactualResponse } from '@/types/api'
import type { RankedHypothesis } from '@/types/hypothesis'

export interface CounterfactualToggleProps {
  runId: string
  hypothesis: RankedHypothesis
  /** The run's ranked list, shown unchanged on the "with" side. */
  ranked: RankedHypothesis[]
  /** The endpoint needs a completed verdict to answer meaningfully. */
  enabled: boolean
}

function MiniRow({
  h,
  dimmed,
  note,
}: {
  h: RankedHypothesis
  dimmed?: boolean
  note?: string
}) {
  return (
    <li
      className={cn(
        'flex items-center justify-between gap-2 rounded border border-border px-2 py-1.5 transition-opacity',
        dimmed && 'opacity-40',
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-[10px] text-muted-foreground">#{h.rank}</span>
        <span className="truncate font-mono text-[11px]">{h.suspect_component}</span>
      </span>
      <span className="flex shrink-0 items-center gap-1.5">
        {note && <span className="text-[10px] text-muted-foreground">{note}</span>}
        <span className="font-mono text-[10px] tabular-nums">{h.score.toFixed(2)}</span>
      </span>
    </li>
  )
}

export function CounterfactualToggle({
  runId,
  hypothesis,
  ranked,
  enabled,
}: CounterfactualToggleProps) {
  const [removed, setRemoved] = useState(false)
  const [result, setResult] = useState<CounterfactualResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const component = hypothesis.suspect_component

  const toggle = async (next: boolean) => {
    if (!next) {
      // Undo: purely local — the endpoint changed no state to roll back.
      setRemoved(false)
      setResult(null)
      setError(null)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const res = await postCounterfactual(runId, { remove_component: component })
      setResult(res)
      setRemoved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const affected = new Set(result?.affected_hypotheses ?? [])
  const pct = result?.anomalies_still_explained_pct
  const redundant = pct !== undefined && pct >= REDUNDANT_PCT

  return (
    <div className="space-y-3 border-t border-border pt-3">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={`cf-${hypothesis.hypothesis_id}`} className="text-xs">
          Remove <span className="font-mono">{component}</span> from the graph
          <span className="ml-2 text-[11px] text-muted-foreground">
            {enabled
              ? 'ask the backend how much of the incident survives'
              : 'available once the run completes'}
          </span>
        </label>
        <Switch
          id={`cf-${hypothesis.hypothesis_id}`}
          checked={removed}
          disabled={!enabled || busy}
          onCheckedChange={(v) => void toggle(v)}
        />
      </div>

      {error && (
        <p role="alert" className="text-xs text-rose-400">
          {error}
        </p>
      )}

      {removed && result && (
        <div className="space-y-2">
          <div className="flex flex-col gap-3 sm:flex-row">
            <div className="min-w-0 flex-1 space-y-1.5">
              <h5 className="text-[10px] uppercase tracking-wide text-muted-foreground">With</h5>
              <ul className="space-y-1">
                {ranked.map((h) => (
                  <MiniRow key={h.hypothesis_id} h={h} />
                ))}
              </ul>
            </div>

            <div className="min-w-0 flex-1 space-y-1.5">
              <h5 className="text-[10px] uppercase tracking-wide text-muted-foreground">
                Without <span className="font-mono">{component}</span>
              </h5>
              <ul className="space-y-1">
                {ranked.map((h) => (
                  <MiniRow
                    key={h.hypothesis_id}
                    h={h}
                    dimmed={affected.has(h.hypothesis_id)}
                    {...(affected.has(h.hypothesis_id) ? { note: 'removed' } : {})}
                  />
                ))}
              </ul>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-background/40 p-2">
            <Badge variant={redundant ? 'outline' : 'danger'}>
              {pct}% still explained
            </Badge>
            <p className="text-[11px] leading-snug text-muted-foreground">
              {redundant
                ? `Without ${component} the other candidates still explain ${pct}% of the anomalies — it is redundant, not load-bearing.`
                : `Without ${component} only ${pct}% of the anomalies are still explained — it is carrying the incident.`}
            </p>
          </div>

          <p className="text-[10px] leading-snug text-muted-foreground/60">
            The ranks and scores above are unchanged on both sides: the endpoint returns the
            still-explained percentage and the affected hypothesis ids, not a re-ranked verdict. Only{' '}
            <span className="font-mono">backend/rank/scorer.py</span> ranks — a &ldquo;what the order
            would become&rdquo; column would be this UI guessing.
          </p>
        </div>
      )}
    </div>
  )
}
