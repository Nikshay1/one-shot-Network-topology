/**
 * Sorting a hypothesis's evidence into Confirmed / Correlated / Missing.
 *
 * WHY THIS IS BUILT FROM THE HYPOTHESIS AND NOT THE LEDGER
 *
 * The obvious design — query the ledger for the hypothesis's facts and bucket
 * them by `kind` — does not work, for three reasons found in the backend:
 *
 *  1. Six of the twelve LedgerKinds have NO production writer (anomaly_observed,
 *     anomaly_absent, topology_path, temporal_order, config_change_observed,
 *     investigation_note). They exist only if an LLM agent files one. On a
 *     deterministic (autopilot) run a ledger-sourced panel is simply empty.
 *  2. `?hypothesis_id=X` returns almost nothing. Most facts are filed with
 *     hypothesis_id=None — including every coverage_gap (tiers.py accepts a
 *     hypothesis_id and never passes it) and every agent file_finding. In
 *     practice a hypothesis has one `hypothesis_scored` fact, sometimes a
 *     counterfactual_result, and (top-1 only) a twin_result.
 *  3. There is no confirming/correlating taxonomy to mirror. The only grouping
 *     constant in the backend is narrator.py's `_EXONERATING_KINDS`, and one of
 *     its three members — counterfactual_result — is written for BOTH outcomes:
 *     "load-bearing" (the strongest incriminating evidence in the system) and
 *     "redundant". Bucketing by kind alone files the smoking gun under
 *     "ruled out".
 *
 * So instead this mirrors the backend's OWN tier criteria (backend/rank/tiers.py
 * assign_tier), whose inputs are all present on the RankedHypothesis:
 *
 *     CONFIRMED  ⟸  cited_ids_resolve ∧ all_symptoms_have_path
 *                   ∧ full_precedence ∧ twin.verdict == "match"
 *                   ∧ no uninstrumented predicted symptom (checked FIRST)
 *     CORRELATED ⟸  otherwise, if the suspect co-occurs with an anomaly
 *     MISSING    ⟸  otherwise
 *
 * Nothing here recomputes a tier — `hypothesis.tier` is always the truth on the
 * card. This only explains, in the backend's own terms, which criteria held and
 * which blocked.
 */
import type { RankedHypothesis } from '@/types/hypothesis'
import type { AnomalyEvent } from '@/types/anomaly'
import type { LedgerRecord } from '@/types/ledger'
import type { ComponentId, EventId } from '@/types/events'
import { REDUNDANT_PCT } from './graph'

export interface EvidenceItem {
  id: string
  /** One line, in plain language. */
  text: string
  /** Event ids to render as clickable chips. */
  eventIds: EventId[]
  /** Where the claim came from, shown as a small provenance tag. */
  source: 'twin' | 'counterfactual' | 'symptom' | 'anomaly' | 'trigger' | 'ledger' | 'challenger'
}

export interface EvidenceColumns {
  confirmed: EvidenceItem[]
  correlated: EvidenceItem[]
  missing: EvidenceItem[]
}

export interface EvidenceInputs {
  hypothesis: RankedHypothesis
  /** Anomalies for the whole run; co-occurrence is filtered from these. */
  anomalies: AnomalyEvent[]
  /** Facts of kind `coverage_gap`. They carry hypothesis_id=None, so they are
   *  fetched by kind and joined on component_ids — see the note above. */
  coverageGaps?: LedgerRecord[]
}

function anomaliesOn(anomalies: AnomalyEvent[], component: ComponentId): AnomalyEvent[] {
  return anomalies.filter((a) => a.component_id === component)
}

export function buildEvidence({
  hypothesis: h,
  anomalies,
  coverageGaps = [],
}: EvidenceInputs): EvidenceColumns {
  const confirmed: EvidenceItem[] = []
  const correlated: EvidenceItem[] = []
  const missing: EvidenceItem[] = []

  // ── twin (a tier criterion) ────────────────────────────────────────────────
  if (h.twin?.verdict === 'match') {
    confirmed.push({
      id: 'twin',
      source: 'twin',
      eventIds: [],
      text: `Twin reproduced the fault — verdict "match" at ${h.twin.similarity.toFixed(2)} similarity (run ${h.twin.run}).`,
    })
  } else if (h.twin) {
    correlated.push({
      id: 'twin',
      source: 'twin',
      eventIds: [],
      text: `Twin verdict "${h.twin.verdict}" at ${h.twin.similarity.toFixed(2)} similarity — reproduction is partial, which blocks confirmation.`,
    })
  } else {
    missing.push({
      id: 'twin',
      source: 'twin',
      eventIds: [],
      text: 'No twin run for this hypothesis, so the fault was never reproduced.',
    })
  }

  for (const gap of h.twin?.missing_evidence ?? []) {
    missing.push({ id: `twin-missing-${gap}`, source: 'twin', eventIds: [], text: gap })
  }

  // ── counterfactual ────────────────────────────────────────────────────────
  // `removed === false` means it was never bought: the pct is the scorer's proxy
  // (scorer.py:123-144), not evidence. Saying anything about it would be a lie.
  if (h.counterfactual.removed) {
    const pct = h.counterfactual.anomalies_still_explained_pct
    if (pct >= REDUNDANT_PCT) {
      missing.push({
        id: 'counterfactual',
        source: 'counterfactual',
        eventIds: [],
        text: `Counterfactual: removing ${h.suspect_component} still leaves ${pct}% of anomalies explained by other candidates — redundant, not load-bearing.`,
      })
    } else {
      confirmed.push({
        id: 'counterfactual',
        source: 'counterfactual',
        eventIds: [],
        text: `Counterfactual: removing ${h.suspect_component} leaves only ${pct}% of anomalies explained — load-bearing.`,
      })
    }
  } else {
    missing.push({
      id: 'counterfactual',
      source: 'counterfactual',
      eventIds: [],
      text: 'No counterfactual was run for this hypothesis — it was never tested by removal.',
    })
  }

  // ── predicted symptoms (path + uninstrumented, both tier criteria) ─────────
  for (const s of h.predicted_symptoms) {
    const id = `symptom-${s.component_id}`
    if (s.observed === true) {
      confirmed.push({
        id,
        source: 'symptom',
        eventIds: [],
        text: `Predicted symptom observed at ${s.component_id}: ${s.expectation}.`,
      })
    } else if (s.observed === false) {
      correlated.push({
        id,
        source: 'symptom',
        eventIds: [],
        text: `Predicted symptom NOT observed at ${s.component_id}: ${s.expectation}. The component is instrumented, so its absence is real.`,
      })
    } else {
      // observed === null is the backend's uninstrumented marker, and it
      // short-circuits the tier to MISSING_EVIDENCE before anything else runs.
      missing.push({
        id,
        source: 'symptom',
        eventIds: [],
        text: `${s.component_id} is uninstrumented, so the predicted symptom "${s.expectation}" cannot be observed either way.`,
      })
    }
  }

  // ── coverage gaps, joined on component (they carry no hypothesis_id) ───────
  const symptomComponents = new Set(h.predicted_symptoms.map((s) => s.component_id))
  const seenGapStatements = new Set<string>()
  for (const gap of coverageGaps) {
    // Emitted once per (gap, suspect) per rescore, so duplicates are expected —
    // the narrator dedupes on statement and so do we.
    if (seenGapStatements.has(gap.statement)) continue
    if (!gap.component_ids.some((c) => symptomComponents.has(c))) continue
    seenGapStatements.add(gap.statement)
    missing.push({
      id: gap.fact_id,
      source: 'ledger',
      eventIds: gap.event_ids,
      text: gap.statement,
    })
  }

  // ── co-occurrence: the CORRELATED discriminator ───────────────────────────
  const own = anomaliesOn(anomalies, h.suspect_component)
  for (const a of own) {
    correlated.push({
      id: a.anomaly_id,
      source: 'anomaly',
      eventIds: a.evidence_event_ids,
      text: a.summary,
    })
  }
  if (own.length === 0) {
    missing.push({
      id: 'co-occurrence',
      source: 'anomaly',
      eventIds: [],
      text: `No anomaly on ${h.suspect_component} itself — the suspect does not co-occur with the observed symptom set.`,
    })
  }

  // ── trigger + citations ───────────────────────────────────────────────────
  if (h.trigger_event_id) {
    correlated.push({
      id: 'trigger',
      source: 'trigger',
      eventIds: [h.trigger_event_id],
      text: 'Trigger event that opened this hypothesis.',
    })
  }
  if (h.cited_evidence_ids.length > 0) {
    confirmed.push({
      id: 'citations',
      source: 'ledger',
      eventIds: h.cited_evidence_ids,
      text: `${h.cited_evidence_ids.length} cited event${h.cited_evidence_ids.length === 1 ? '' : 's'} back this hypothesis.`,
    })
  } else {
    missing.push({
      id: 'citations',
      source: 'ledger',
      eventIds: [],
      text: 'No events are cited for this hypothesis.',
    })
  }

  // ── challenger ────────────────────────────────────────────────────────────
  for (const attack of h.challenger?.attacks ?? []) {
    // An upheld attack is a tier blocker (tiers.py:157), so it belongs with the
    // things standing between this hypothesis and CONFIRMED.
    ;(attack.upheld ? missing : confirmed).push({
      id: `attack-${attack.contradicting_event_id}`,
      source: 'challenger',
      eventIds: [attack.contradicting_event_id],
      text: attack.upheld
        ? `Challenger attack UPHELD: ${attack.claim}`
        : `Challenger attack rejected: ${attack.claim}`,
    })
  }

  return { confirmed, correlated, missing }
}

export function evidenceCount(columns: EvidenceColumns): number {
  return columns.confirmed.length + columns.correlated.length + columns.missing.length
}
