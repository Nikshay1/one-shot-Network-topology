/**
 * A fake cytoscape core for jsdom.
 *
 * Cytoscape renders to a canvas and jsdom has no 2d context, so the real library
 * throws on construction. Rather than pull in node-canvas (a native dep) to
 * render pixels no test can read anyway, this records the data() writes —
 * which is exactly what TopologyGraph's paint step produces. Asserting on them
 * verifies the wiring: is the right node red, amber, cleared; is the right edge
 * causal.
 *
 * It implements only the surface TopologyGraph uses.
 */

export interface FakeElement {
  _data: Record<string, unknown>
  id: () => string
  data: (key?: string, value?: unknown) => unknown
  style: (key: string, value: unknown) => FakeElement
  select: () => void
  unselect: () => void
  selected: boolean
}

function makeElement(data: Record<string, unknown>): FakeElement {
  const el: FakeElement = {
    _data: { ...data },
    selected: false,
    id: () => String(el._data.id),
    data: (key?: string, value?: unknown) => {
      if (key === undefined) return el._data
      if (value === undefined) return el._data[key]
      el._data[key] = value
      return el
    },
    style: () => el,
    select: () => {
      el.selected = true
    },
    unselect: () => {
      el.selected = false
    },
  }
  return el
}

/** Supports only the `[?field]` truthiness selector, which is all we use. */
function matches(el: FakeElement, selector?: string): boolean {
  if (!selector) return true
  const m = /^\[\?(\w+)\]$/.exec(selector)
  if (!m) return true
  return Boolean(el._data[m[1]!])
}

function collection(items: FakeElement[], selector?: string) {
  const filtered = items.filter((el) => matches(el, selector))
  return {
    forEach: (fn: (el: FakeElement) => void) => filtered.forEach(fn),
    unselect: () => filtered.forEach((el) => el.unselect()),
    length: filtered.length,
    map: <T,>(fn: (el: FakeElement) => T) => filtered.map(fn),
  }
}

export interface FakeCy {
  nodes: (selector?: string) => ReturnType<typeof collection>
  edges: (selector?: string) => ReturnType<typeof collection>
  batch: (fn: () => void) => void
  on: (...args: unknown[]) => void
  layout: () => { run: () => void }
  destroy: () => void
  getElementById: (id: string) => FakeElement
  _nodes: FakeElement[]
  _edges: FakeElement[]
  _handlers: Map<string, (ev: { target: unknown }) => void>
  _layoutOptions: unknown
}

/** The most recently constructed instance, for assertions. */
export let lastCy: FakeCy | null = null

export function resetCytoscapeMock() {
  lastCy = null
}

interface CyOptions {
  elements: { nodes: { data: Record<string, unknown> }[]; edges: { data: Record<string, unknown> }[] }
}

export function makeCytoscapeMock() {
  function cytoscape(options: CyOptions): FakeCy {
    const nodes = options.elements.nodes.map((n) => makeElement(n.data))
    const edges = options.elements.edges.map((e) => makeElement(e.data))
    const handlers = new Map<string, (ev: { target: unknown }) => void>()

    const cy: FakeCy = {
      _nodes: nodes,
      _edges: edges,
      _handlers: handlers,
      _layoutOptions: null,
      nodes: (selector?: string) => collection(nodes, selector),
      edges: (selector?: string) => collection(edges, selector),
      batch: (fn: () => void) => fn(),
      on: (...args: unknown[]) => {
        // cy.on('tap', 'node', fn) | cy.on('tap', fn)
        const key = args.length === 3 ? `${args[0] as string}:${args[1] as string}` : (args[0] as string)
        handlers.set(key, args.at(-1) as (ev: { target: unknown }) => void)
      },
      layout: (opts?: unknown) => {
        cy._layoutOptions = opts
        return { run: () => {} }
      },
      destroy: () => {},
      getElementById: (id: string) => nodes.find((n) => n.id() === id) ?? makeElement({ id }),
    }
    lastCy = cy
    return cy
  }

  // TopologyGraph registers the dagre layout with cytoscape.use() at module
  // load. The mock never lays anything out — positions are meaningless without
  // a renderer — so this only has to exist.
  cytoscape.use = () => {}

  return cytoscape
}

/** Read a node's paint data after a render. */
export function nodeData(id: string): Record<string, unknown> | null {
  const node = lastCy?._nodes.find((n) => n.id() === id)
  return node ? node._data : null
}

/** Ids of edges currently flagged causal, as `source->target`. */
export function causalEdgeIds(): string[] {
  return (lastCy?._edges ?? []).filter((e) => e._data.causal).map((e) => String(e._data.id))
}
