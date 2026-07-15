/**
 * Cytoscape stylesheet + layout for the dependency graph.
 *
 * The visual grammar, and what each state is actually claiming:
 *
 *  hollow / dashed  `instrumented === false` on the topology node. A real
 *                   backend fact (scenarios.py:80-87) — NOT "we saw no events
 *                   for it yet", which would make every node hollow at t=0.
 *  amber + pulse    the component has >= 1 anomaly_detected.
 *  blast shading    the component is in a blast_radius event's `affected` set.
 *  red              suspect_component of the rank-1 hypothesis.
 *  animated edge    a causal path we computed client-side (the API ships none);
 *                   drawn in the direction failure travels, i.e. against the
 *                   call edges.
 *  green ✓ badge    a bought counterfactual found it redundant (>= 70%). Not
 *                   "innocent" — see lib/graph.ts clearedComponents().
 *
 * Colours are read off the CSS custom properties so the graph matches the rest
 * of the dark theme rather than hard-coding a second palette.
 */
import type cytoscape from 'cytoscape'

export interface NodeData {
  id: string
  label: string
  /** Stored inverted because the cytoscape selector tests truthiness: [?uninstrumented]. */
  uninstrumented: boolean
  anomalous: boolean
  inBlast: boolean
  suspect: boolean
  cleared: boolean
  /** Toggled on an interval to drive the pulse (canvas can't use CSS keyframes). */
  pulse: boolean
  /** Cosmetic only — not a backend field. See types/api.ts TopologyNode. */
  shape: 'ellipse' | 'round-rectangle' | 'diamond'
}

export interface EdgeData {
  id: string
  source: string
  target: string
  relation: string
  /** On the computed causal path, drawn in failure-propagation direction. */
  causal: boolean
  inBlast: boolean
}

/**
 * Tuned for the LIGHT workspace, matching the reference's topology map: hollow
 * rings on white, colour carried by the stroke rather than a filled blob. The
 * previous values were chosen against a near-black panel and are unreadable
 * here.
 */
const COLORS = {
  node: '#ffffff',
  nodeBorder: '#9c938f',
  text: '#171413',
  muted: '#a89f9b',
  edge: '#cfc7c2',
  amber: '#e08700',
  red: '#d9202b',
  green: '#168452',
  // cyan, not violet: violet means the MISSING_EVIDENCE tier, and one hue must
  // not carry two meanings.
  blast: '#0e7490',
} as const

export const graphStylesheet: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      // Hollow ring on white, like the reference's topology map.
      'background-color': COLORS.node,
      'border-color': COLORS.nodeBorder,
      'border-width': 2,
      shape: 'data(shape)' as unknown as cytoscape.Css.NodeShape,
      label: 'data(label)',
      color: COLORS.text,
      'font-size': 9,
      'font-family': "'DM Sans', system-ui, sans-serif",
      'font-weight': 600,
      'text-valign': 'bottom',
      'text-margin-y': 5,
      // Labels sit over edges in a dense graph; a paper-coloured plate behind
      // each one keeps it readable without hiding the link underneath.
      'text-background-color': '#ffffff',
      'text-background-opacity': 0.92,
      // cytoscape types this as a CSS length string, not a number.
      'text-background-padding': '2px',
      'text-background-shape': 'roundrectangle',
      width: 26,
      height: 26,
      'transition-property': 'background-color, border-color, border-width, opacity',
      'transition-duration': 220,
    },
  },

  // Uninstrumented: hollow + dashed. The missing-telemetry visual.
  {
    selector: 'node[?uninstrumented]',
    style: {
      'background-opacity': 0,
      'border-style': 'dashed',
      'border-color': COLORS.muted,
      color: COLORS.muted,
    },
  },

  { selector: 'node[?inBlast]', style: { 'border-color': COLORS.blast, 'border-width': 2 } },

  {
    selector: 'node[?anomalous]',
    style: {
      'background-color': '#fff8ed',
      'border-color': COLORS.amber,
      'border-width': 3,
      color: COLORS.text,
    },
  },

  // The pulse: a data flag flipped on an interval, smoothed by transition-duration.
  {
    selector: 'node[?anomalous][?pulse]',
    style: { 'border-width': 8, 'border-opacity': 0.45 },
  },

  {
    selector: 'node[?suspect]',
    style: {
      'background-color': COLORS.red,
      'border-color': '#8f1119',
      'border-width': 3,
      color: COLORS.red,
      width: 44,
      height: 44,
      'font-size': 11,
    },
  },

  // Cleared beats amber: it is a later, stronger statement about the component.
  {
    selector: 'node[?cleared]',
    style: {
      'background-color': '#eefaf3',
      'border-color': COLORS.green,
      'border-width': 3,
      color: COLORS.green,
    },
  },

  { selector: 'node:selected', style: { 'overlay-color': COLORS.text, 'overlay-opacity': 0.12 } },

  {
    selector: 'edge',
    style: {
      width: 1,
      'line-color': COLORS.edge,
      'target-arrow-color': COLORS.edge,
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.6,
      // Orthogonal segments read as a wiring diagram rather than a bowl of
      // spaghetti, and dagre's layered ranks give them clean channels to run in.
      'curve-style': 'taxi',
      'taxi-direction': 'downward',
      'taxi-turn': 22,
      'taxi-turn-min-distance': 8,
      opacity: 0.45,
      'transition-property': 'line-color, width, opacity',
      'transition-duration': 220,
    },
  },

  { selector: 'edge[?inBlast]', style: { 'line-color': COLORS.blast, opacity: 0.8 } },

  /**
   * The causal edge is drawn REVERSED (target-arrow at the source end) because
   * the arrow shows where the failure goes, while the edge itself records who
   * calls whom. line-dash-offset is animated in TopologyGraph to make it march.
   */
  {
    selector: 'edge[?causal]',
    style: {
      width: 2.6,
      'line-color': COLORS.red,
      'source-arrow-color': COLORS.red,
      'source-arrow-shape': 'triangle',
      'target-arrow-shape': 'none',
      'arrow-scale': 1,
      opacity: 1,
      'line-style': 'dashed',
      'line-dash-pattern': [6, 4],
    },
  },
]

/**
 * dagre, top-to-bottom.
 *
 * breadthfirst was here first and it could not cope: sock-shop is not a tree —
 * `orders` calls `carts` and `user`, `front-end` calls `user` — and breadthfirst
 * makes no attempt to minimise edge crossings, so those back-links flew across
 * the whole canvas and landed on top of the labels. dagre does layered
 * assignment with crossing reduction, which is exactly this problem, and it
 * needs no per-component knowledge (so a real case with a different roster still
 * lays out cleanly).
 *
 * rankDir TB puts callers above callees: loadgenerator on top, databases at the
 * bottom, failure travelling upward — the shape an SRE expects.
 */
export function graphLayout(): cytoscape.LayoutOptions {
  return {
    name: 'dagre',
    rankDir: 'TB',
    // The card is much wider than it is tall, so the fit is height-bound:
    // spreading siblings horizontally uses that width instead of scaling the
    // whole graph down into a narrow column.
    nodeSep: 48,
    rankSep: 58,
    edgeSep: 14,
    ranker: 'network-simplex',
    padding: 28,
    animate: false,
    fit: true,
  } as unknown as cytoscape.LayoutOptions
}

/** Cosmetic shape from the id. The API has no service_type — see types/api.ts. */
export function shapeFor(id: string): NodeData['shape'] {
  if (id.endsWith('-db')) return 'round-rectangle'
  if (id === 'rabbitmq') return 'diamond'
  return 'ellipse'
}
