/**
 * The dependency graph, lit up by the live run.
 *
 * Uses the cytoscape core API directly rather than react-cytoscapejs: the React
 * wrapper rebuilds its element list from props on every render, which is exactly
 * what a 5,000-events/second stream must not do. Here the graph is constructed
 * once per topology and every subsequent change is an rAF-batched data write, so
 * a burst of events costs one repaint per frame rather than one per event.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import {
  graphLayout,
  graphStylesheet,
  shapeFor,
} from './graphStyles'
import type { EdgeData, NodeData } from './graphStyles'
import {
  useAnomalousComponents,
  useBlastComponents,
  useClearedComponents,
  useTopHypothesis,
} from '@/store/useRunStore'
import { causalEdges, indexGraph } from '@/lib/graph'
import type { TopologyGraph as TopologyGraphData } from '@/types/api'
import type { ComponentId } from '@/types/events'

const PULSE_MS = 700

export interface TopologyGraphProps {
  topology: TopologyGraphData
  selected: ComponentId | null
  onSelect: (id: ComponentId | null) => void
}

interface PaintState {
  anomalous: Set<ComponentId>
  blast: Set<ComponentId>
  cleared: Set<ComponentId>
  suspect: ComponentId | null
  causal: Set<string>
  causalNodes: Set<ComponentId>
}

export function TopologyGraph({ topology, selected, onSelect }: TopologyGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const rafRef = useRef<number | null>(null)

  /**
   * onSelect is a fresh closure on every parent render. Held in a ref so it can
   * stay OUT of the build effect's deps: with it in there, every store update
   * (i.e. every event of the run) tore the graph down and rebuilt it, throwing
   * away the layout, the selection and the current paint.
   */
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  /** Bumped whenever the graph is rebuilt, so the paint re-applies to it. */
  const [graphGen, setGraphGen] = useState(0)

  const anomalous = useAnomalousComponents()
  const blast = useBlastComponents()
  const cleared = useClearedComponents()
  const top = useTopHypothesis()

  const index = useMemo(() => indexGraph(topology), [topology])

  const paint = useMemo<PaintState>(() => {
    const edges = top ? causalEdges(index, top) : []
    const causalNodes = new Set<ComponentId>()
    const causal = new Set<string>()
    for (const e of edges) {
      // The graph edge runs callee→caller in the call direction, so the edge id
      // we must light is the CALL edge (to → from), not the failure direction.
      causal.add(`${e.to}->${e.from}`)
      causalNodes.add(e.from)
      causalNodes.add(e.to)
    }
    return {
      anomalous,
      blast,
      cleared: new Set(cleared.keys()),
      suspect: top?.suspect_component ?? null,
      causal,
      causalNodes,
    }
  }, [index, top, anomalous, blast, cleared])

  // Build the graph once per topology.
  useEffect(() => {
    if (!containerRef.current) return

    const nodes = topology.nodes.map((n) => ({
      data: {
        id: n.id,
        label: n.id,
        // Absent `instrumented` means instrumented (real RE2-SS cases omit it).
        uninstrumented: n.instrumented === false,
        anomalous: false,
        inBlast: false,
        suspect: false,
        cleared: false,
        pulse: false,
        shape: shapeFor(n.id),
      } satisfies NodeData,
    }))

    const edges = topology.links.map((l) => ({
      data: {
        id: `${l.source}->${l.target}`,
        source: l.source,
        target: l.target,
        relation: l.relation ?? 'calls',
        causal: false,
        inBlast: false,
      } satisfies EdgeData,
    }))

    const cy = cytoscape({
      container: containerRef.current,
      elements: { nodes, edges },
      style: graphStylesheet,
      minZoom: 0.3,
      maxZoom: 2.5,
      wheelSensitivity: 0.2,
    })

    // Roots = nodes nobody calls (loadgenerator), so the graph reads top-down.
    const roots = topology.nodes.map((n) => n.id).filter((id) => !index.in.get(id)?.length)
    cy.layout(graphLayout(roots)).run()

    cy.on('tap', 'node', (ev) => onSelectRef.current(ev.target.id() as ComponentId))
    cy.on('tap', (ev) => {
      if (ev.target === cy) onSelectRef.current(null)
    })

    cyRef.current = cy
    setGraphGen((g) => g + 1)
    return () => {
      cy.destroy()
      cyRef.current = null
    }
    // Rebuild ONLY when the graph itself changes. `index` is derived from
    // `topology`, so it adds no churn.
  }, [topology, index])

  // rAF-batched repaint: a burst of events collapses into one frame.
  useEffect(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(() => {
      const cy = cyRef.current
      if (!cy) return
      cy.batch(() => {
        cy.nodes().forEach((n) => {
          const id = n.id()
          n.data('anomalous', paint.anomalous.has(id))
          n.data('inBlast', paint.blast.has(id))
          n.data('cleared', paint.cleared.has(id))
          n.data('suspect', paint.suspect === id)
        })
        cy.edges().forEach((e) => {
          const id = e.id()
          e.data('causal', paint.causal.has(id))
          e.data(
            'inBlast',
            paint.blast.has(e.data('source') as string) && paint.blast.has(e.data('target') as string),
          )
        })
      })
    })
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
    // graphGen: a freshly rebuilt graph starts unpainted and must be repainted.
  }, [paint, graphGen])

  // The pulse. Canvas has no CSS keyframes, so flip a data flag on an interval
  // and let the stylesheet's transition-duration smooth it.
  useEffect(() => {
    if (paint.anomalous.size === 0) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduced) return

    let on = false
    const timer = setInterval(() => {
      const cy = cyRef.current
      if (!cy) return
      on = !on
      cy.batch(() => {
        cy.nodes().forEach((n) => {
          n.data('pulse', on && paint.anomalous.has(n.id()) && !paint.cleared.has(n.id()))
        })
      })
    }, PULSE_MS)
    return () => clearInterval(timer)
  }, [paint])

  // March the causal path's dashes toward the symptom.
  useEffect(() => {
    if (paint.causal.size === 0) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduced) return

    let offset = 0
    let frame: number
    const step = () => {
      const cy = cyRef.current
      if (cy) {
        offset = (offset + 0.6) % 10
        cy.batch(() => {
          cy.edges('[?causal]').forEach((e) => {
            e.style('line-dash-offset', -offset)
          })
        })
      }
      frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [paint])

  // Keep the cytoscape selection in step with the drawer.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => {
      cy.nodes().unselect()
      if (selected) cy.getElementById(selected).select()
    })
  }, [selected])

  return <div ref={containerRef} className="h-full w-full" data-testid="topology-graph" />
}
