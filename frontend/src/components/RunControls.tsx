import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { startRun } from '@/api/client'
import { DEMO_PRESET, SPEED_OPTIONS, identifyCase, SCENARIO_LABELS } from '@/demo/scenarios'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

export interface RunControlsProps {
  caseId: string | null
  onOpenChange: (open: boolean) => void
}

/**
 * Speed/seed/twin, then POST /case/{id}/run → /run/{run_id}.
 *
 * A 409 is not surfaced as a failure: it means a run for this case is already in
 * flight, and the right move is to attach to its stream. Every subscriber is
 * replayed the run from index 0, so attaching late loses nothing.
 */
export function RunControls({ caseId, onOpenChange }: RunControlsProps) {
  const navigate = useNavigate()
  const [speed, setSpeed] = useState<number>(DEMO_PRESET.speed)
  const [seed, setSeed] = useState<number>(DEMO_PRESET.seed)
  const [twinEnabled, setTwinEnabled] = useState<boolean>(DEMO_PRESET.twin_enabled)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // No `system` here any more. It was read from topology.graph.name, but the
  // real graph dict is `{}` — only fixtures/sample_topology.json ever carried a
  // name, so the line could never render against a live backend.

  const launch = async () => {
    if (!caseId) return
    setBusy(true)
    setError(null)
    try {
      const result = await startRun(caseId, { speed, seed, twin_enabled: twinEnabled })
      onOpenChange(false)
      navigate(`/run/${encodeURIComponent(result.run_id)}?view=incident`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const identity = caseId ? identifyCase(caseId) : null

  return (
    <Dialog open={caseId !== null} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-mono text-base">{caseId}</DialogTitle>
          <DialogDescription>
            {identity?.scenarioType ? SCENARIO_LABELS[identity.scenarioType] : 'Real case'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <fieldset>
            <legend className="mb-1.5 text-xs uppercase tracking-wide text-muted-foreground">
              Speed
            </legend>
            <div className="grid grid-cols-4 gap-1.5">
              {SPEED_OPTIONS.map((opt) => (
                <Button
                  key={opt.label}
                  type="button"
                  size="sm"
                  variant={speed === opt.value ? 'default' : 'outline'}
                  onClick={() => setSpeed(opt.value)}
                  title={opt.hint}
                  aria-pressed={speed === opt.value}
                >
                  {opt.label}
                </Button>
              ))}
            </div>
            <p
              className={cn(
                'mt-1.5 text-[11px]',
                speed === 0 ? 'text-amber-400' : 'text-muted-foreground',
              )}
            >
              {speed === 0
                ? 'instant flushes the whole ingest burst before detection starts — the feed sits still, then dumps, and agent steps never interleave. Eval only.'
                : SPEED_OPTIONS.find((o) => o.value === speed)?.hint}
            </p>
          </fieldset>

          <div className="flex items-center justify-between gap-4">
            <label htmlFor="seed" className="text-sm">
              Seed
            </label>
            <input
              id="seed"
              type="number"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
              className="h-8 w-24 rounded-md border border-border bg-background px-2 text-right font-mono text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <label htmlFor="twin" className="text-sm">
              SimPy twin
              <span className="ml-2 text-xs text-muted-foreground">verification</span>
            </label>
            <Switch id="twin" checked={twinEnabled} onCheckedChange={setTwinEnabled} />
          </div>

          {error && (
            <p role="alert" className="text-sm text-rose-400">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={() => void launch()} disabled={busy}>
              {busy ? 'Starting…' : 'Start run'}
            </Button>
          </div>

          <p className="text-[11px] text-muted-foreground">
            <Badge variant="outline" className="mr-1.5">
              note
            </Badge>
            run_id == case_id. If a run for this case is already in flight the API returns 409 and we
            attach to it instead — the stream replays from the start either way.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  )
}
