/**
 * What the fix-rehearsal agent tried, and what it recommends.
 *
 * Everything is rendered from GET /run/{id}/remediation verbatim — including the
 * caveat, which is the whole point of the panel: the honest cases ("no remedy
 * could be rehearsed", "recommend human review") are the ones worth showing.
 *
 * Two backend behaviours the UI must respect rather than smooth over:
 *  - status "uncertain" deliberately sets `recommended: null` and puts EVERY
 *    rehearsal in `alternatives` (not ordered[1:] as it does for "ok"). So an
 *    uncertain report has no crown, by design.
 *  - a rehearsal can silently vanish. On the LLM path the agent reconstructs
 *    reports by json.loads(step.result_summary), and result_summary is truncated
 *    at 200 chars — a remedy with long side-effect strings fails to parse and is
 *    dropped (remediation.py:107-116). So `rehearsals` is a floor, not a census.
 */
import { RemediationResultEvent } from '@/types/api'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { RecoveryReport, RemediationReport, RemediationStatus } from '@/types/api'

const STATUS_TONE: Record<RemediationStatus, string> = {
  ok: 'border-tier-confirmed/40 bg-tier-confirmed/10 text-tier-confirmed',
  uncertain: 'border-tier-correlated/40 bg-tier-correlated/10 text-tier-correlated',
  skipped: 'border-border bg-secondary text-muted-foreground',
  error: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
}

/** Radial cleared-% dial. */
function ClearedRadial({ pct }: { pct: number }) {
  const r = 18
  const circumference = 2 * Math.PI * r
  const dash = (Math.min(100, Math.max(0, pct)) / 100) * circumference
  // 50 is the backend's CLEARED_THRESHOLD, and it is <=, so exactly 50 is uncertain.
  const good = pct > 50

  return (
    <svg viewBox="0 0 48 48" className="h-12 w-12 shrink-0" role="img" aria-label={`${pct}% of symptoms cleared`}>
      <circle cx="24" cy="24" r={r} fill="none" stroke="currentColor" strokeWidth="4" className="text-secondary" />
      <circle
        cx="24"
        cy="24"
        r={r}
        fill="none"
        strokeWidth="4"
        strokeLinecap="round"
        strokeDasharray={`${dash} ${circumference}`}
        transform="rotate(-90 24 24)"
        className={good ? 'text-tier-confirmed' : 'text-tier-correlated'}
        stroke="currentColor"
      />
      <text
        x="24"
        y="27"
        textAnchor="middle"
        className="fill-foreground font-mono text-[10px]"
      >
        {Math.round(pct)}%
      </text>
    </svg>
  )
}

function RehearsalCard({
  report,
  crowned,
}: {
  report: RecoveryReport
  crowned?: boolean
}) {
  return (
    <li
      className={cn(
        'rounded-lg border p-3',
        crowned ? 'border-tier-confirmed/50 bg-tier-confirmed/5' : 'border-border bg-card',
      )}
      data-testid="rehearsal-card"
    >
      <div className="flex items-start gap-3">
        <ClearedRadial pct={report.symptoms_cleared_pct} />

        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            {crowned && <Badge variant="outline" className="border-tier-confirmed/40 text-tier-confirmed">recommended</Badge>}
            <span className="font-mono text-xs">{report.remedy}</span>
          </div>

          <p className="text-[11px] text-muted-foreground">
            simulated recovery in{' '}
            <span className="font-mono text-foreground">{report.sim_time_to_recover_s}s</span>
          </p>

          {report.side_effects.length > 0 && (
            <div>
              <span className="text-[10px] uppercase tracking-wide text-amber-400">side effects</span>
              <ul className="mt-0.5 space-y-0.5">
                {report.side_effects.map((effect) => (
                  <li key={effect} className="text-[11px] leading-snug text-amber-300/90">
                    · {effect}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.residual_symptoms.length > 0 && (
            <div>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                residual symptoms
              </span>
              <ul className="mt-0.5 space-y-0.5">
                {report.residual_symptoms.map((symptom) => (
                  <li key={symptom} className="text-[11px] leading-snug text-muted-foreground">
                    · {symptom}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.fact_id && (
            <span className="font-mono text-[10px] text-muted-foreground/70">{report.fact_id}</span>
          )}
        </div>
      </div>
    </li>
  )
}

export interface RemediationPanelProps {
  report: RemediationReport | null
  loading?: boolean
  /** Live remediation_result events, shown while the report is still forming. */
  live?: RemediationResultEvent[]
}

export function RemediationPanel({ report, loading, live = [] }: RemediationPanelProps) {
  if (!report) {
    return (
      <div className="space-y-3">
        {live.length > 0 && (
          <ul className="space-y-2">
            {live.map((r) => (
              <li key={r.remedy} className="rounded-lg border border-border bg-card p-3">
                <div className="flex items-center gap-3">
                  <ClearedRadial pct={r.symptoms_cleared_pct} />
                  <div>
                    <span className="font-mono text-xs">{r.remedy}</span>
                    <p className="text-[11px] text-muted-foreground">
                      simulated recovery in {r.sim_time_to_recover_s}s
                    </p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted-foreground">
          {loading
            ? 'Loading the remediation report…'
            : live.length > 0
              ? 'Rehearsals are streaming; the full report lands when the run completes.'
              : 'No remediation report yet — it is written once the run has a verdict.'}
        </p>
      </div>
    )
  }

  // "uncertain" deliberately has no recommended remedy and keeps every rehearsal
  // in `alternatives`; "ok" excludes the winner from it.
  const crown = report.recommended
  const others = report.alternatives

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn('rounded border px-2 py-0.5 font-mono text-xs uppercase', STATUS_TONE[report.status])}>
          {report.status}
        </span>
        {report.component && <Badge variant="outline">{report.component}</Badge>}
        {report.fault_type && <Badge variant="outline">{report.fault_type}</Badge>}
        {report.agent_status && (
          <Badge variant={report.agent_status === 'completed' ? 'outline' : 'anomaly'}>
            agent {report.agent_status}
          </Badge>
        )}
      </div>

      {/* Verbatim. The honest cases are the point of this line. */}
      {report.caveat && (
        <p
          className={cn(
            'rounded-lg border p-2 text-[11px] leading-snug',
            report.status === 'uncertain' || report.status === 'error'
              ? 'border-amber-500/40 bg-amber-500/5 text-amber-300'
              : 'border-border bg-card text-muted-foreground',
          )}
          role={report.status === 'uncertain' || report.status === 'error' ? 'alert' : undefined}
          data-testid="remediation-caveat"
        >
          {report.caveat}
        </p>
      )}

      {crown && (
        <ul className="space-y-2">
          <RehearsalCard report={crown} crowned />
        </ul>
      )}

      {others.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {crown ? 'Alternatives rehearsed' : 'Rehearsed, none recommended'}
          </h4>
          <ul className="space-y-2">
            {others.map((r) => (
              <RehearsalCard key={r.remedy} report={r} />
            ))}
          </ul>
        </section>
      )}

      {!crown && others.length === 0 && (
        <p className="text-xs text-muted-foreground">
          Nothing was rehearsed for this run.
        </p>
      )}
    </div>
  )
}
