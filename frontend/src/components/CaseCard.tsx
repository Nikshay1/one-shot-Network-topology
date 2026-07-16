import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { identifyCase, SCENARIO_LABELS } from '@/demo/scenarios'
import type { CaseSummary } from '@/types/api'

/**
 * What /cases actually returns is {case_id, title, n_components, n_events} —
 * nothing else. So this card shows those, plus a scenario badge inferred from
 * the case_id itself (see demo/scenarios.ts).
 *
 * Only REAL cases are badged. Stamping "synthetic" on the other 25 said the same
 * thing 25 times and read as a disclaimer on the majority of the grid; the
 * scenario badge underneath ("Clean cascade #1") already tells you what a case is.
 * Marking the exception is the informative direction: `real` on catalogue_cpu-1
 * means RE2-SS telemetry, and that is worth pointing at.
 *
 * Deliberately absent: `system` and `duration`. Neither exists on /cases.
 * `system` is available per case as topology.graph.name, but only via a separate
 * GET /case/{id}/topology — one request per card is not worth it, so RunControls
 * shows it for the single case you're about to run. `duration` has no source on
 * any endpoint, so it is not rendered rather than faked.
 */
export function CaseCard({ case: c, onSelect }: { case: CaseSummary; onSelect: () => void }) {
  const { kind, scenarioType, variantNumber } = identifyCase(c.case_id)

  return (
    <Card className="transition-colors focus-within:border-ring hover:border-ring/60">
      <button
        type="button"
        onClick={onSelect}
        className="w-full text-left focus-visible:outline-none"
        aria-label={`Configure a run for ${c.case_id}`}
      >
        <CardHeader>
          <div className="flex items-start justify-between gap-2">
            <CardTitle className="font-mono text-sm">{c.case_id}</CardTitle>
            {kind === 'real' && <Badge variant="real">{kind}</Badge>}
          </div>
          {scenarioType && (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="outline">{SCENARIO_LABELS[scenarioType]}</Badge>
              {variantNumber !== null && (
                <Badge variant="outline" title="scenario variant number">
                  #{variantNumber}
                </Badge>
              )}
            </div>
          )}
        </CardHeader>

        <CardContent>
          <dl className="flex gap-4 text-xs text-muted-foreground">
            <div>
              <dt className="uppercase tracking-wide text-[10px]">events</dt>
              <dd className="font-mono text-sm tabular-nums text-foreground">
                {c.n_events.toLocaleString()}
              </dd>
            </div>
            <div>
              <dt className="uppercase tracking-wide text-[10px]">components</dt>
              <dd className="font-mono text-sm tabular-nums text-foreground">{c.n_components}</dd>
            </div>
          </dl>
        </CardContent>
      </button>
    </Card>
  )
}
