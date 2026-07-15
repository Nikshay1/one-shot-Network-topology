/**
 * Blast-radius impact pill.
 *
 * Two things this deliberately does NOT say.
 *
 * "est. sessions": there is no session, user or traffic concept anywhere in the
 * API. The twin has an internal `arrival_rate` on its Calibration, but it never
 * leaves the simulator — Twin's public shape is {run, similarity, verdict,
 * missing_evidence} and forbids extra fields. There is nothing to estimate
 * sessions from, not even a proxy, so no number is shown rather than a made-up
 * one.
 *
 * Per-component radius: the `blast_radius` payload's `radius` is `len(affected)`
 * (app.py:210) — a cardinality named like a hop count — and `affected` is the
 * same GLOBAL node set for every component, because the backend computes the
 * blast once over the union of all anomalous components and then re-emits it per
 * component minus itself (app.py:203-211). So a per-component "N services" would
 * print the same N on every component. The honest reading is run-level: the size
 * of the union.
 */
import { useBlastComponents } from '@/store/useRunStore'
import { Badge } from '@/components/ui/badge'

export function ImpactBadge() {
  const blast = useBlastComponents()
  if (blast.size === 0) return null

  return (
    <Badge
      variant="outline"
      className="border-cyan-400/40 text-cyan-300"
      title={`Blast radius: ${[...blast].sort().join(', ')}`}
    >
      {blast.size} service{blast.size === 1 ? '' : 's'} in blast radius
    </Badge>
  )
}
