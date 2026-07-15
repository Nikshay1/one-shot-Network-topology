/**
 * The presenter's teleprompter. Unlinked route: /demo-script.
 *
 * Every beat is a deep link, because rule 8 says demo-critical states must be
 * URL-addressable — on stage you jump, you don't remember a click path.
 *
 * The claims in the notes are the ones the backend can actually support. Where a
 * number would be nice and does not exist, the note says what to say instead.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { DEMO_PRESET } from '@/demo/scenarios'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface Beat {
  minute: string
  title: string
  to: string
  say: string
  note?: string
}

const CLEAN = 'clean_cascade-01'
const HERRING = 'red_herring_config-01'
const MISSING = 'missing_telemetry-01'

const BEATS: Beat[] = [
  {
    minute: '0:00',
    title: 'The console',
    to: '/',
    say: 'Twenty-five synthetic scenarios plus real RE2-SS cases. Seven demo buttons, one click each.',
    note: `Every demo runs at speed ${DEMO_PRESET.speed}×, seed ${DEMO_PRESET.seed} — about 34s. Do not use instant: it flushes the whole ingest burst before detection, so the feed sits still then dumps and the agent steps never interleave.`,
  },
  {
    minute: '0:30',
    title: 'Demo 1 — watch it happen',
    to: `/run/${CLEAN}?view=incident`,
    say: 'A config push lands. Latency climbs at the database, errors surface at the front-end. The graph lights up in stream order.',
    note: 'Amber = anomalous, cyan ring = blast radius, red = the rank-1 suspect. The marching dashes are the causal path — computed here from the topology, since the API ships no path.',
  },
  {
    minute: '1:30',
    title: 'The verdict, and the tier',
    to: `/run/${CLEAN}?view=verdict`,
    say: 'catalogue-db, CONFIRMED. The tier is not a vibe: cited evidence resolves, a topology path reaches every observed symptom, precedence holds, and the twin reproduced it.',
    note: 'Point at tier_reason — those are the backend\'s own words. The score bar is five pre-weighted contributions that sum to the score.',
  },
  {
    minute: '2:30',
    title: 'Demo 2 — the red herring falls',
    to: `/run/${HERRING}?view=verdict`,
    say: 'payment leads on precedence alone: a risky config change 60s before the incident. Then we test it. Removing payment still leaves 100% of anomalies explained — redundant, not load-bearing. Watch it fall.',
    note: 'THE moment. The cards re-order live. The green ✓ says counterfactual-unchanged, never "innocent" — innocence is ground truth and lives in /eval.',
  },
  {
    minute: '3:30',
    title: 'Remove it yourself',
    to: `/run/${CLEAN}?view=verdict`,
    say: 'The toggle asks the backend the same question live: take catalogue-db out and only 16.7% of the incident still explains itself.',
    note: 'Read-only and idempotent. It does not re-rank — the endpoint returns a percentage, not a new verdict, and this UI will not invent one.',
  },
  {
    minute: '4:15',
    title: 'Bounded agents',
    to: `/run/${CLEAN}?view=agents`,
    say: 'The investigator gets 16 calls and 7 cost points. A twin costs 2, a counterfactual 1. The meter is the proof that the budget is code, not a polite request in a prompt.',
    note: 'Caveat if asked: the limits are mirrored from source — Budget.snapshot() exists backend-side and nothing serves it.',
  },
  {
    minute: '5:00',
    title: 'It tried to break itself',
    to: `/run/${HERRING}?view=agents&agent=challenger`,
    say: 'The challenger attacks its own top hypothesis. An attack only stands if the cited event resolves AND pertains — same component, upstream, or inside an anomaly window.',
    note: 'On clean_cascade nothing survives validation, which is the stronger story: it tried and failed.',
  },
  {
    minute: '5:45',
    title: 'What it does not know',
    to: `/run/${MISSING}?view=incident`,
    say: 'The hollow dashed nodes emit no telemetry. A predicted symptom there is observed:null — not false. Absence of evidence is recorded as absence of evidence.',
    note: 'This is the missing_telemetry scenario. The tier drops to MISSING_EVIDENCE because of it, and the report recommends what to instrument.',
  },
  {
    minute: '6:15',
    title: 'The audit trail',
    to: `/run/${CLEAN}?view=report`,
    say: 'Every claim carries a ledger fact id. Hover one and you get the fact. A claim whose citation does not resolve is deleted by the narrator, not shipped.',
    note: 'The PDF is the same trail, downloadable.',
  },
  {
    minute: '6:45',
    title: 'The numbers',
    to: '/benchmark',
    say: 'Held-out accuracy, the false-blame rate, and the ablations — remove the counterfactual, remove the twin, remove topology, and watch it degrade.',
    note: 'Ground truth is redacted per run, so there is no per-case answer key here. If asked why baselines are empty: RCAEval is not importable in this environment, and the runner records that rather than pretending.',
  },
]

export function DemoScript() {
  const [checked, setChecked] = useState<Set<string>>(new Set())

  const toggle = (minute: string) =>
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(minute)) next.delete(minute)
      else next.add(minute)
      return next
    })

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-6">
      <header className="space-y-1">
        <h1 className="text-lg font-semibold tracking-tight">Demo script — 7 minutes</h1>
        <p className="text-sm text-muted-foreground">
          Every beat is a deep link. Click the title to jump; tick it off as you go.
        </p>
      </header>

      <ol className="space-y-2">
        {BEATS.map((beat) => {
          const done = checked.has(beat.minute)
          return (
            <li
              key={beat.minute}
              className={cn(
                'rounded-lg border border-border bg-card p-3 transition-opacity',
                done && 'opacity-50',
              )}
            >
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  checked={done}
                  onChange={() => toggle(beat.minute)}
                  aria-label={`mark ${beat.title} done`}
                  className="mt-1 h-4 w-4 shrink-0 accent-sky-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="font-mono">
                      {beat.minute}
                    </Badge>
                    <Link
                      to={beat.to}
                      className="font-medium underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {beat.title}
                    </Link>
                    <span className="font-mono text-[10px] text-muted-foreground/60">{beat.to}</span>
                  </div>
                  <p className="text-sm leading-snug text-foreground/90">{beat.say}</p>
                  {beat.note && (
                    <p className="text-[11px] leading-snug text-muted-foreground">{beat.note}</p>
                  )}
                </div>
              </div>
            </li>
          )
        })}
      </ol>

      <p className="text-[11px] leading-snug text-muted-foreground/70">
        If a run dies mid-demo: rule 11 means the pipeline falls back to the deterministic autopilot
        and still produces a verdict. The error is a toast, not a wall — keep going.
      </p>
    </div>
  )
}
