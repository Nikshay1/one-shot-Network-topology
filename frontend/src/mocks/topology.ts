/**
 * Mock topologies that mirror what the backend ACTUALLY serves — which the
 * repo's fixtures/sample_topology.json does not.
 *
 * Verified against the on-disk topology.json files:
 *  - synthetic case:        {"instrumented": true, "id": "carts"}
 *  - missing_telemetry-01:  {"instrumented": false, "id": "carts-db"}
 *  - real RE2-SS case:      {"id": "carts"}   — no `instrumented` key at all
 *
 * The fixture's `service_type`, `graph: {name, case_id}` and varied `relation`
 * values are invented: no backend code writes them, `build_topology`
 * (re2ss_adapter.py:285-299) adds no node attributes and always writes
 * relation="calls", and the graph dict is `{}`. Mock mode reproduces the real
 * shapes so that a bug like "we read topology.graph.name" fails here rather than
 * only in production.
 */
import type { TopologyGraph } from '@/types/api'
import { identifyCase } from '@/demo/scenarios'

/** backend/overlay/scenarios.py CANON_COMPONENTS. */
const COMPONENTS = [
  'loadgenerator',
  'front-end',
  'catalogue',
  'catalogue-db',
  'carts',
  'carts-db',
  'orders',
  'orders-db',
  'payment',
  'shipping',
  'user',
  'user-db',
  'queue-master',
  'rabbitmq',
  'session-db',
] as const

/** backend/ingest/re2ss_adapter.py SOCK_SHOP_DEPS. Edges point caller → callee. */
const DEPS: [string, string][] = [
  ['loadgenerator', 'front-end'],
  ['front-end', 'catalogue'],
  ['front-end', 'carts'],
  ['front-end', 'orders'],
  ['front-end', 'user'],
  ['front-end', 'session-db'],
  ['catalogue', 'catalogue-db'],
  ['carts', 'carts-db'],
  ['orders', 'orders-db'],
  ['orders', 'payment'],
  ['orders', 'shipping'],
  ['orders', 'user'],
  ['orders', 'carts'],
  ['user', 'user-db'],
  ['shipping', 'rabbitmq'],
  ['queue-master', 'rabbitmq'],
]

/** Which nodes the missing_telemetry scenario leaves uninstrumented. */
const UNINSTRUMENTED = new Set(['carts-db', 'queue-master'])

export function mockTopology(caseId: string): TopologyGraph {
  const { kind, scenarioType } = identifyCase(caseId)
  const isMissingTelemetry = scenarioType === 'missing_telemetry'

  const nodes = COMPONENTS.map((id) => {
    // Real cases carry no `instrumented` key — the backend defaults it to true.
    if (kind === 'real') return { id }
    return { id, instrumented: isMissingTelemetry ? !UNINSTRUMENTED.has(id) : true }
  })

  return {
    directed: true,
    multigraph: false,
    graph: {},
    nodes,
    links: DEPS.map(([source, target]) => ({ source, target, relation: 'calls' as const })),
  }
}
