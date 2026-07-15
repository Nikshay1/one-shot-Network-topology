import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { startRun } from '@/api/client'
import { useCases } from '@/hooks/useCases'
import { CaseCard } from '@/components/CaseCard'
import { RunControls } from '@/components/RunControls'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { DEMO_PRESET, demoButtons } from '@/demo/scenarios'

/**
 * The presenter's control surface.
 *
 * The DEMO row is the on-stage path: one click, preset params, no modal. The
 * card grid below is the explorer path for everything else.
 */
export function Console() {
  const { cases, loading, error, reload } = useCases()
  const [selected, setSelected] = useState<string | null>(null)
  const [firing, setFiring] = useState<string | null>(null)
  const navigate = useNavigate()

  const demos = demoButtons(cases)

  const fireDemo = async (caseId: string) => {
    setFiring(caseId)
    try {
      const result = await startRun(caseId, DEMO_PRESET)
      navigate(`/run/${encodeURIComponent(result.run_id)}?view=incident`)
    } catch {
      // Don't die on stage: fall back to the modal so the run is still one click away.
      setSelected(caseId)
    } finally {
      setFiring(null)
    }
  }

  return (
    <div className="space-y-8 p-6">
      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Demo</h1>
            <p className="text-sm text-muted-foreground">
              One click each — speed {DEMO_PRESET.speed}×, seed {DEMO_PRESET.seed}, twin{' '}
              {DEMO_PRESET.twin_enabled ? 'on' : 'off'}. ~34s per run.
            </p>
          </div>
          {demos.length > 0 && <Badge variant="outline">{demos.length} scenarios</Badge>}
        </div>

        {demos.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {loading ? 'Loading cases…' : 'No scenario cases in /cases — nothing to demo.'}
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {demos.map((demo) => (
              <Button
                key={demo.scenarioType}
                size="demo"
                variant="secondary"
                disabled={firing !== null}
                onClick={() => void fireDemo(demo.caseId)}
                className="h-full"
              >
                <span className="font-mono text-[10px] tracking-widest text-muted-foreground">
                  DEMO {demo.n}
                </span>
                <span className="text-sm font-semibold">{demo.label}</span>
                <span className="text-[11px] font-normal leading-snug text-muted-foreground">
                  {demo.blurb}
                </span>
                <span className="mt-0.5 font-mono text-[10px] text-muted-foreground/70">
                  {firing === demo.caseId ? 'starting…' : demo.caseId}
                </span>
              </Button>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-lg font-semibold tracking-tight">Cases</h2>
          {!loading && !error && (
            <span className="text-xs text-muted-foreground">{cases.length} total</span>
          )}
        </div>

        {error ? (
          <div className="space-y-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-4">
            <p className="text-sm text-rose-300">Could not load /cases — {error}</p>
            <Button size="sm" variant="outline" onClick={reload}>
              Retry
            </Button>
          </div>
        ) : loading ? (
          <p className="text-sm text-muted-foreground">Loading cases…</p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {cases.map((c) => (
              <CaseCard key={c.case_id} case={c} onSelect={() => setSelected(c.case_id)} />
            ))}
          </div>
        )}
      </section>

      <RunControls caseId={selected} onOpenChange={(open) => !open && setSelected(null)} />
    </div>
  )
}
