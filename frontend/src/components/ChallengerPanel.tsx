/**
 * The challenger's objections.
 *
 * WHY THERE ARE NO DISMISSED ATTACKS HERE
 *
 * Showing rejected attacks would be the better trust story, and the backend
 * makes it impossible today. challenger.py:103-113:
 *
 *     for attack in parse_attacks(result.final_text):
 *         if not validate_attack(ctx, hypothesis, attack):
 *             continue                      # <- discarded, silently
 *         upheld.append({**attack, "upheld": True})
 *
 * A dismissed attack is `continue`d past before anything is appended or emitted,
 * `upheld: True` is a hardcoded literal, and validate_attack returns a bare bool
 * so no rejection reason is produced anywhere. Even the COUNT is unrecoverable:
 * parse_attacks() is called inline and its result never stored, so nothing knows
 * how many attacks were proposed. `upheld: false` cannot reach the wire or the
 * verdict — the contract types it as a bool, so we render the flag rather than
 * assume, but the false branch is unreachable in production.
 *
 * The only trace of a rejected attack is prose in the challenger's `final_text`
 * on its transcript. This panel surfaces that rather than re-implementing
 * parse_attacks() and validate_attack() client-side — a frontend re-deriving the
 * backend's validation would be inventing verdicts.
 *
 * Two gates decide upheld, both in code (the model has no say): the cited event
 * must RESOLVE against the event store, and it must PERTAIN — its component is
 * the suspect or upstream-reachable from it, or its timestamp falls inside a
 * suspect anomaly window ±5s.
 */
import { CHALLENGER_PENALTY_NOTE } from '@/lib/challenger'
import { EventChip } from '@/components/EventChip'
import { Badge } from '@/components/ui/badge'
import type { ChallengerAttackEvent } from '@/types/api'

export interface ChallengerPanelProps {
  attacks: ChallengerAttackEvent[]
  /** The challenger's final_text from its transcript, if fetched. */
  finalText?: string | null | undefined
}

export function ChallengerPanel({ attacks, finalText }: ChallengerPanelProps) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-[11px] uppercase tracking-wide text-muted-foreground">Objections</h4>
        <Badge variant={attacks.length ? 'danger' : 'outline'}>{attacks.length} upheld</Badge>
      </div>

      {attacks.length === 0 ? (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3">
          <p className="text-xs leading-snug text-emerald-300">
            No attack survived validation.
          </p>
          <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
            The challenger ran and failed to break the verdict. An attack is upheld only if its
            cited event resolves against the event store AND pertains to the suspect — same
            component, upstream of it, or inside one of its anomaly windows (±5s).
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {attacks.map((attack) => (
            <li
              key={`${attack.hypothesis_id}-${attack.contradicting_event_id}`}
              className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-3"
              data-testid="attack-card"
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="danger">UPHELD</Badge>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {attack.hypothesis_id}
                </span>
                <span className="ml-auto text-[10px] text-muted-foreground">
                  {CHALLENGER_PENALTY_NOTE}
                </span>
              </div>
              <p className="mt-1.5 text-xs leading-snug text-foreground/90">{attack.claim}</p>
              <div className="mt-1.5 flex items-center gap-1.5">
                <span className="text-[10px] text-muted-foreground">contradicted by</span>
                <EventChip eventId={attack.contradicting_event_id} />
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* done.summary is deliberately not repeated here — the transcript's
          agent_done banner above already shows it. */}

      {finalText && (
        <details className="rounded-lg border border-border bg-card p-2">
          <summary className="cursor-pointer text-[11px] text-muted-foreground">
            What the challenger proposed, including attacks that were thrown out
          </summary>
          <p className="mt-1.5 whitespace-pre-wrap text-[11px] leading-relaxed text-muted-foreground">
            {finalText}
          </p>
        </details>
      )}

      <p className="text-[10px] leading-snug text-muted-foreground/60">
        Rejected attacks are discarded by the backend before emission and carry no reason, so they
        cannot be listed here — only the challenger&apos;s own report mentions them. Surfacing them
        properly needs a backend change.
      </p>
    </div>
  )
}
