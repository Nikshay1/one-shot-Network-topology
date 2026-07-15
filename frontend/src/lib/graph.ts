/**
 * Graph reasoning the API doesn't do for us. Pure, so every claim below is
 * testable without a renderer.
 *
 * Edge direction, which everything here depends on: edges point CALLER → CALLEE
 * (`front-end` → `catalogue` → `catalogue-db`). Failure therefore propagates
 * AGAINST the edges — a broken `catalogue-db` surfaces as symptoms on
 * `catalogue` and `front-end`. The backend says so in two places:
 * backend/rank/tiers.py:94-95 tests `has_path(topology, symptom, suspect)`, and
 * backend/agents/tools.py:238-239 spells it out: "{src} calls (transitively)
 * {dst}; latency/errors propagate {dst} -> {src}".
 */
import type { ComponentId } from '@/types/events'
import type { RankedHypothesis } from '@/types/hypothesis'
import type { TopologyGraph } from '@/types/api'

/**
 * The `_REDUNDANT_PCT` threshold from backend/rank/autopilot.py:34. Mirrored,
 * not invented: above it the backend itself writes "redundant
 * (counterfactual-unchanged)" into the ledger and halves the score.
 */
export const REDUNDANT_PCT = 70

/** An adjacency view of the node-link graph, both directions. */
export interface GraphIndex {
  nodes: ComponentId[]
  /** caller → callees */
  out: Map<ComponentId, ComponentId[]>
  /** callee → callers */
  in: Map<ComponentId, ComponentId[]>
}

export function indexGraph(topology: TopologyGraph): GraphIndex {
  const out = new Map<ComponentId, ComponentId[]>()
  const inn = new Map<ComponentId, ComponentId[]>()
  const nodes = topology.nodes.map((n) => n.id)

  for (const id of nodes) {
    out.set(id, [])
    inn.set(id, [])
  }
  for (const link of topology.links) {
    // node-link `source`/`target` can be ids or indices depending on producer;
    // the backend emits ids (networkx node_link_data with string nodes).
    out.get(link.source)?.push(link.target)
    inn.get(link.target)?.push(link.source)
  }
  return { nodes, out, in: inn }
}

/**
 * `instrumented` is absent on real cases and the backend reads it as true
 * there (candidates.py:89). Absent !== uninstrumented.
 */
export function isInstrumented(topology: TopologyGraph, id: ComponentId): boolean {
  const node = topology.nodes.find((n) => n.id === id)
  return node?.instrumented ?? true
}

export function uninstrumentedNodes(topology: TopologyGraph): Set<ComponentId> {
  return new Set(topology.nodes.filter((n) => n.instrumented === false).map((n) => n.id))
}

/** Shortest path along edge direction (caller → callee). [] if unreachable. */
export function shortestPath(
  index: GraphIndex,
  from: ComponentId,
  to: ComponentId,
): ComponentId[] {
  if (from === to) return [from]
  const prev = new Map<ComponentId, ComponentId>()
  const seen = new Set<ComponentId>([from])
  const queue: ComponentId[] = [from]

  while (queue.length) {
    const node = queue.shift()!
    for (const next of index.out.get(node) ?? []) {
      if (seen.has(next)) continue
      seen.add(next)
      prev.set(next, node)
      if (next === to) {
        const path = [to]
        let cur = to
        while (cur !== from) {
          cur = prev.get(cur)!
          path.unshift(cur)
        }
        return path
      }
      queue.push(next)
    }
  }
  return []
}

export interface CausalEdge {
  /** The component the failure flows FROM (nearer the suspect). */
  from: ComponentId
  /** The component the failure flows TO (nearer the symptom). */
  to: ComponentId
}

/**
 * The causal path(s) from a suspect out to its predicted symptoms.
 *
 * The API gives no path: the `topology_path` ledger kind has zero production
 * writers (only an LLM `file_finding` can emit one), and RankedHypothesis has
 * no path field. So we compute it, matching the backend's own reachability
 * test: a symptom is downstream-of-failure iff `has_path(symptom → suspect)`
 * along the call edges. We then reverse each such path, because that is the
 * direction the failure actually travels.
 *
 * Only instrumented-or-not is irrelevant here; symptoms with `observed === null`
 * are still on the path, they just have no data to confirm it.
 */
export function causalEdges(
  index: GraphIndex,
  hypothesis: Pick<RankedHypothesis, 'suspect_component' | 'predicted_symptoms'>,
): CausalEdge[] {
  const edges = new Map<string, CausalEdge>()

  for (const symptom of hypothesis.predicted_symptoms) {
    // The call path runs symptom → … → suspect.
    const callPath = shortestPath(index, symptom.component_id, hypothesis.suspect_component)
    if (callPath.length < 2) continue

    // Failure runs the other way: suspect → … → symptom.
    const failurePath = [...callPath].reverse()
    for (let i = 0; i < failurePath.length - 1; i += 1) {
      const from = failurePath[i]!
      const to = failurePath[i + 1]!
      edges.set(`${from}->${to}`, { from, to })
    }
  }
  return [...edges.values()]
}

/** The components a causal path touches, suspect included. */
export function causalComponents(edges: CausalEdge[], suspect: ComponentId): Set<ComponentId> {
  const out = new Set<ComponentId>([suspect])
  for (const e of edges) {
    out.add(e.from)
    out.add(e.to)
  }
  return out
}

export interface ClearedVerdict {
  component: ComponentId
  hypothesis_id: string
  anomalies_still_explained_pct: number
}

/**
 * Components a real counterfactual found unnecessary — the red-herring visual.
 *
 * This is NOT a tier, score or rank, and it is not "innocent" (that word maps to
 * `ground_truth_innocent`, which is /eval-only by rule 4 and never served). It
 * restates one measurement the backend published: we removed this component and
 * the remaining candidates still explained >= 70% of the anomalies, so it is not
 * load-bearing. The backend draws the same line at the same threshold and writes
 * "redundant (counterfactual-unchanged)" into the ledger (autopilot.py:101).
 *
 * `removed === true` is mandatory. At the ranking floor the scorer fills
 * `counterfactual` with a PROXY and sets `removed: false` (scorer.py:123-144);
 * the pct is real evidence only once a counterfactual was actually bought.
 * Reading the proxy would clear components nobody ever tested.
 */
export function clearedComponents(hypotheses: RankedHypothesis[]): ClearedVerdict[] {
  return hypotheses
    .filter((h) => h.counterfactual.removed)
    .filter((h) => h.counterfactual.anomalies_still_explained_pct >= REDUNDANT_PCT)
    .map((h) => ({
      component: h.suspect_component,
      hypothesis_id: h.hypothesis_id,
      anomalies_still_explained_pct: h.counterfactual.anomalies_still_explained_pct,
    }))
}

/** Human phrasing for the cleared badge. Deliberately never says "innocent". */
export function clearedReason(pct: number): string {
  return `Counterfactual tested: removing this still leaves ${pct}% of anomalies explained by other candidates — not load-bearing.`
}
