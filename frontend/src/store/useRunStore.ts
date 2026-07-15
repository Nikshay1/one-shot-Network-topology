/**
 * React bindings for the vanilla run store. Kept separate so runStore.ts stays
 * free of React and testable as plain TypeScript.
 *
 * WHY THE DERIVED HOOKS EXIST
 *
 * zustand v5 sits on useSyncExternalStore, which compares snapshots with
 * Object.is. A selector that allocates on every call — `[...map.values()]`, a
 * `{...}` literal — never compares equal to its previous result, so React
 * re-renders, re-derives, and loops until it throws "Maximum update depth
 * exceeded". Passing selectRankedHypotheses straight to useRunStore is exactly
 * that mistake.
 *
 * So: subscribe to a STABLE slice (a Map's identity only changes when the
 * reducer replaces it; a primitive is trivially stable) and derive inside
 * useMemo keyed on it.
 */
import { useMemo } from 'react'
import { useStore } from 'zustand'
import {
  runStore,
  rankHypotheses,
  selectActiveStage,
  selectCaseWindow,
  stagesUpTo,
} from './runStore'
import type {
  CaseWindow,
  FeedItem,
  MetricSeries,
  PipelineStage,
  RunState,
  RunStore,
} from './runStore'
import { clearedComponents } from '@/lib/graph'
import type { ClearedVerdict } from '@/lib/graph'
import type { AnomalyEvent } from '@/types/anomaly'
import type { ComponentId, EventEnvelope } from '@/types/events'
import type { RankedHypothesis } from '@/types/hypothesis'

/**
 * Subscribe to a slice. The selector MUST return something stable: a primitive,
 * or a value the reducer replaces by identity (a Map, an array from state). If
 * you find yourself writing `s => [...s.x.values()]` here, use one of the
 * derived hooks below instead — that form re-renders forever.
 */
export function useRunStore<T>(selector: (state: RunStore) => T): T {
  return useStore(runStore, selector)
}

/** Actions are stable — safe to pull once and use in effects. */
export function runActions() {
  return runStore.getState()
}

/** Hypotheses in backend rank order. */
export function useRankedHypotheses(): RankedHypothesis[] {
  const hypotheses = useRunStore((s) => s.hypotheses)
  return useMemo(() => rankHypotheses(hypotheses), [hypotheses])
}

/** Events and anomalies interleaved in arrival order, newest last. */
export function useFeed(): FeedItem[] {
  const feed = useRunStore((s) => s.feed)
  return useMemo(() => [...feed.values()], [feed])
}

export function useAnomalies(): AnomalyEvent[] {
  const anomalies = useRunStore((s) => s.anomalies)
  return useMemo(() => [...anomalies.values()], [anomalies])
}

export interface StageProgress {
  active: PipelineStage | null
  reached: PipelineStage[]
  complete: boolean
}

/**
 * The stage indicator. selectActiveStage returns a plain string, so it is safe
 * to subscribe to directly — this re-renders when the stage changes rather than
 * on every one of a run's ~186k ingested events.
 */
export function useStageProgress(): StageProgress {
  const active = useRunStore(selectActiveStage)
  const complete = useRunStore((s) => s.status === 'done')
  return useMemo(() => ({ active, reached: stagesUpTo(active), complete }), [active, complete])
}

/** Components carrying at least one anomaly — the amber set. */
export function useAnomalousComponents(): Set<ComponentId> {
  const anomalies = useRunStore((s) => s.anomalies)
  return useMemo(() => {
    const out = new Set<ComponentId>()
    for (const a of anomalies.values()) out.add(a.component_id)
    return out
  }, [anomalies])
}

/** Everything named by any blast_radius event: the origin and its affected set. */
export function useBlastComponents(): Set<ComponentId> {
  const blastRadius = useRunStore((s) => s.blastRadius)
  return useMemo(() => {
    const out = new Set<ComponentId>()
    for (const b of blastRadius.values()) {
      out.add(b.component_id)
      for (const c of b.affected) out.add(c)
    }
    return out
  }, [blastRadius])
}

/** The rank-1 hypothesis, or null. Rank comes from the backend — never re-sorted. */
export function useTopHypothesis(): RankedHypothesis | null {
  const ranked = useRankedHypotheses()
  return ranked[0] ?? null
}

/** Components a bought counterfactual found redundant. See lib/graph.ts. */
export function useClearedComponents(): Map<ComponentId, ClearedVerdict> {
  const ranked = useRankedHypotheses()
  return useMemo(
    () => new Map(clearedComponents(ranked).map((c) => [c.component, c])),
    [ranked],
  )
}

export function useCaseWindow(): CaseWindow | null {
  const tsMin = useRunStore((s) => s.tsMin)
  const tsMax = useRunStore((s) => s.tsMax)
  const anomalies = useRunStore((s) => s.anomalies)
  return useMemo(
    () => selectCaseWindow({ tsMin, tsMax, anomalies } as RunState),
    [tsMin, tsMax, anomalies],
  )
}

export function useSeriesFor(component: ComponentId | null): MetricSeries[] {
  const metricSeries = useRunStore((s) => s.metricSeries)
  return useMemo(() => {
    if (!component) return []
    return [...metricSeries.values()]
      .filter((s) => s.component_id === component)
      .map((s) => ({ ...s, points: [...s.points].sort((a, b) => a.ts - b.ts) }))
  }, [metricSeries, component])
}

export function useAnomaliesFor(component: ComponentId | null): AnomalyEvent[] {
  const anomalies = useRunStore((s) => s.anomalies)
  return useMemo(() => {
    if (!component) return []
    return [...anomalies.values()].filter((a) => a.component_id === component)
  }, [anomalies, component])
}

export function useConfigChanges(): EventEnvelope[] {
  const configChanges = useRunStore((s) => s.configChanges)
  return useMemo(
    () => [...configChanges.values()].sort((a, b) => a.ts - b.ts),
    [configChanges],
  )
}
