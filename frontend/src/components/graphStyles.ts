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

const COLORS = {
  node: '#1e293b',
  nodeBorder: '#334155',
  text: '#e2e8f0',
  muted: '#64748b',
  edge: '#334155',
  amber: '#fbbf24',
  red: '#f43f5e',
  green: '#4ade80',
  // cyan, not violet: violet now means the MISSING_EVIDENCE tier, and one hue
  // must not carry two meanings.
  blast: '#22d3ee',
} as const

export const graphStylesheet: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      'background-color': COLORS.node,
      'border-color': COLORS.nodeBorder,
      'border-width': 1.5,
      shape: 'data(shape)' as unknown as cytoscape.Css.NodeShape,
      label: 'data(label)',
      color: COLORS.text,
      'font-size': 10,
      'font-family': 'ui-monospace, monospace',
      'text-valign': 'bottom',
      'text-margin-y': 5,
      width: 34,
      height: 34,
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
      'background-color': COLORS.amber,
      'border-color': COLORS.amber,
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
      'border-color': COLORS.red,
      'border-width': 3,
      width: 42,
      height: 42,
      'font-size': 11,
    },
  },

  // Cleared beats amber: it is a later, stronger statement about the component.
  {
    selector: 'node[?cleared]',
    style: {
      'background-color': COLORS.node,
      'border-color': COLORS.green,
      'border-width': 2,
      color: COLORS.green,
    },
  },

  { selector: 'node:selected', style: { 'overlay-color': COLORS.text, 'overlay-opacity': 0.12 } },

  {
    selector: 'edge',
    style: {
      width: 1.2,
      'line-color': COLORS.edge,
      'target-arrow-color': COLORS.edge,
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      'curve-style': 'bezier',
      opacity: 0.55,
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
 * breadthfirst rooted at the entry points (nodes nothing calls), which puts
 * `loadgenerator`/`front-end` at the top and databases at the bottom — the
 * shape an SRE expects. dagre would be prettier but is another dependency.
 */
export function graphLayout(roots: string[]): cytoscape.LayoutOptions {
  return {
    name: 'breadthfirst',
    directed: true,
    padding: 24,
    spacingFactor: 1.15,
    avoidOverlap: true,
    grid: false,
    ...(roots.length ? { roots } : {}),
    animate: false,
  } as cytoscape.LayoutOptions
}

/** Cosmetic shape from the id. The API has no service_type — see types/api.ts. */
export function shapeFor(id: string): NodeData['shape'] {
  if (id.endsWith('-db')) return 'round-rectangle'
  if (id === 'rabbitmq') return 'diamond'
  return 'ellipse'
}
