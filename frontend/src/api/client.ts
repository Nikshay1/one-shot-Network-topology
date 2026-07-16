/**
 * The ONLY module that calls fetch(). No component may import fetch directly.
 *
 * Covers every REST endpoint in contracts/api_contract.md v1.1. In MOCK MODE
 * (VITE_MOCK=1) every call is served from src/mocks fixtures and never touches
 * the network, so the whole UI is developable with the backend down.
 */
import type {
  AgentName,
  AnomalyEvent,
  ApiErrorBody,
  BenchmarkResponse,
  CaseSummary,
  ChatRequest,
  ChatResponse,
  CounterfactualRequest,
  CounterfactualResponse,
  HealthResponse,
  LedgerQuery,
  LedgerRecord,
  NarrationResponse,
  RemediationReport,
  RunAccepted,
  RunConflict,
  RunRequest,
  StartRunResult,
  TopologyGraph,
  TranscriptLine,
  VerdictResponse,
} from '@/types/api'
import { parseNdjson } from './ndjson'
import { mockApi } from '@/mocks'

export { parseNdjson }

export const IS_MOCK = import.meta.env.VITE_MOCK === '1'

export const API_BASE = (import.meta.env.VITE_API_BASE ?? 'http://localhost:8000').replace(
  /\/+$/,
  '',
)

/** Requests carry no credentials: CORS is open (`*`) and must stay that way. */
const DEFAULT_TIMEOUT_MS = 15_000

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: ApiErrorBody | string | null,
    message?: string,
  ) {
    super(message ?? `${status}: ${typeof body === 'string' ? body : (body?.error ?? 'request failed')}`)
    this.name = 'ApiError'
  }
}

/** Thrown when the API is unreachable (dead backend, DNS, CORS preflight). */
export class NetworkError extends Error {
  constructor(readonly cause: unknown) {
    super('network request failed — is the VERDICT API running?')
    this.name = 'NetworkError'
  }
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}

async function readBody(res: Response): Promise<ApiErrorBody | string | null> {
  const text = await res.text().catch(() => '')
  if (!text) return null
  try {
    return JSON.parse(text) as ApiErrorBody
  } catch {
    return text
  }
}

interface RequestOptions {
  method?: string
  body?: unknown
  timeoutMs?: number
  /** Status codes to hand back to the caller instead of throwing (e.g. 409). */
  expect?: number[]
}

async function request(path: string, opts: RequestOptions = {}): Promise<Response> {
  const { method = 'GET', body, timeoutMs = DEFAULT_TIMEOUT_MS, expect = [] } = opts
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  let res: Response
  try {
    res = await fetch(apiUrl(path), {
      method,
      signal: controller.signal,
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (err) {
    throw new NetworkError(err)
  } finally {
    clearTimeout(timer)
  }

  if (!res.ok && !expect.includes(res.status)) {
    throw new ApiError(res.status, await readBody(res))
  }
  return res
}

async function getJson<T>(path: string): Promise<T> {
  const res = await request(path)
  return (await res.json()) as T
}

// ─────────────────────────────────────────────────────────────────────────────
// Endpoints
// ─────────────────────────────────────────────────────────────────────────────

/** GET /health — poll this to drive the offline banner. */
export async function getHealth(): Promise<HealthResponse> {
  if (IS_MOCK) return mockApi.health()
  return getJson<HealthResponse>('/health')
}

/** GET /cases */
export async function getCases(): Promise<CaseSummary[]> {
  if (IS_MOCK) return mockApi.cases()
  return getJson<CaseSummary[]>('/cases')
}

/** GET /case/{id}/topology — NetworkX node-link graph. */
export async function getTopology(caseId: string): Promise<TopologyGraph> {
  if (IS_MOCK) return mockApi.topology(caseId)
  return getJson<TopologyGraph>(`/case/${encodeURIComponent(caseId)}/topology`)
}

/**
 * POST /case/{id}/run → 202 {run_id, stream}.
 *
 * 409 is NOT an error: it means a run for this case is already in flight, and
 * the right move is to attach to its stream (a late subscriber is replayed the
 * full history from index 0). So it is returned, not thrown.
 *
 * `run_id === case_id` backend-side, which is what lets an OFFLINE demo replay
 * a warmed agent transcript.
 */
export async function startRun(caseId: string, req: RunRequest): Promise<StartRunResult> {
  if (IS_MOCK) return mockApi.startRun(caseId)

  const res = await request(`/case/${encodeURIComponent(caseId)}/run`, {
    method: 'POST',
    body: req,
    expect: [409],
  })

  if (res.status === 409) {
    const conflict = (await res.json()) as RunConflict
    return {
      status: 'in_flight',
      run_id: conflict.run_id,
      stream: streamPathFor(conflict.run_id),
      error: conflict.error,
    }
  }

  const accepted = (await res.json()) as RunAccepted
  return { status: 'started', run_id: accepted.run_id, stream: accepted.stream }
}

/** The 409 body carries no `stream`, so reconstruct it the way the server does. */
export function streamPathFor(runId: string): string {
  return `/stream/${runId}`
}

/** GET /run/{id}/verdict — `hypotheses` is [] while in flight, never null. */
export async function getVerdict(runId: string): Promise<VerdictResponse> {
  if (IS_MOCK) return mockApi.verdict(runId)
  return getJson<VerdictResponse>(`/run/${encodeURIComponent(runId)}/verdict`)
}

/** GET /run/{id}/anomalies */
export async function getAnomalies(runId: string): Promise<AnomalyEvent[]> {
  if (IS_MOCK) return mockApi.anomalies()
  return getJson<AnomalyEvent[]>(`/run/${encodeURIComponent(runId)}/anomalies`)
}

/** GET /run/{id}/ledger?component_id=&kind=&hypothesis_id= */
export async function getLedger(runId: string, query: LedgerQuery = {}): Promise<LedgerRecord[]> {
  if (IS_MOCK) return mockApi.ledger(query)
  const params = new URLSearchParams()
  if (query.component_id) params.set('component_id', query.component_id)
  if (query.kind) params.set('kind', query.kind)
  if (query.hypothesis_id) params.set('hypothesis_id', query.hypothesis_id)
  const qs = params.toString()
  return getJson<LedgerRecord[]>(`/run/${encodeURIComponent(runId)}/ledger${qs ? `?${qs}` : ''}`)
}

/** GET /run/{id}/narration — note every chunk's `ts` is hardcoded 0.0. */
export async function getNarration(runId: string): Promise<NarrationResponse> {
  if (IS_MOCK) return mockApi.narration(runId)
  return getJson<NarrationResponse>(`/run/${encodeURIComponent(runId)}/narration`)
}

/** POST /run/{id}/counterfactual — `remove_component` must exist in the topology. */
export async function postCounterfactual(
  runId: string,
  req: CounterfactualRequest,
): Promise<CounterfactualResponse> {
  if (IS_MOCK) return mockApi.counterfactual(runId, req)
  const res = await request(`/run/${encodeURIComponent(runId)}/counterfactual`, {
    method: 'POST',
    body: req,
  })
  return (await res.json()) as CounterfactualResponse
}

/**
 * GET /run/{id}/remediation.
 * Returns null when the run has no verdict yet (the backend 404s with
 * `{error, done}` rather than returning an empty report).
 */
export async function getRemediation(runId: string): Promise<RemediationReport | null> {
  if (IS_MOCK) return mockApi.remediation(runId)
  try {
    return await getJson<RemediationReport>(`/run/${encodeURIComponent(runId)}/remediation`)
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

/**
 * GET /run/{run_id}/agent/{agent_name}/transcript — application/x-ndjson.
 * Returns null when that agent never ran (the endpoint 404s).
 */
export async function getAgentTranscript(
  runId: string,
  agent: AgentName,
): Promise<TranscriptLine[] | null> {
  if (IS_MOCK) return mockApi.transcript(agent)
  try {
    const res = await request(`/run/${encodeURIComponent(runId)}/agent/${agent}/transcript`)
    return parseNdjson(await res.text())
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

/** GET /run/{id}/report.pdf — a blob, not JSON. */
export async function getReportPdf(runId: string): Promise<Blob> {
  if (IS_MOCK) return mockApi.reportPdf()
  const res = await request(`/run/${encodeURIComponent(runId)}/report.pdf`)
  return res.blob()
}

/** Direct URL for an <a download> / <iframe>, bypassing fetch. */
export function reportPdfUrl(runId: string): string {
  return apiUrl(`/run/${encodeURIComponent(runId)}/report.pdf`)
}

/**
 * GET /benchmark.
 * `truth`, `rank_of_truth` and `false_blame` are redacted per run at the
 * boundary; aggregate metrics remain. There is deliberately no per-case "was it
 * right?" signal here — ground truth is /eval-only (rule 4). Do not try to
 * reconstruct one.
 */
export async function getBenchmark(): Promise<BenchmarkResponse> {
  if (IS_MOCK) return mockApi.benchmark()
  return getJson<BenchmarkResponse>('/benchmark')
}

/**
 * POST /run/{id}/chat — ask a question about a finished run.
 *
 * 404 means the run has no verdict yet: there is no evidence to answer from, so
 * the caller should wait rather than retry. A missing key, OFFLINE mode or a
 * tripped spend cap are NOT errors — the backend answers with `mode:
 * "deterministic"` instead, so never treat a 200 as proof a model spoke.
 *
 * Slower than the other endpoints (a real model call), hence the longer timeout.
 */
export async function postChat(runId: string, req: ChatRequest): Promise<ChatResponse> {
  if (IS_MOCK) return mockApi.chat(req)
  const res = await request(`/run/${encodeURIComponent(runId)}/chat`, {
    method: 'POST',
    body: req,
    timeoutMs: 60_000,
  })
  return (await res.json()) as ChatResponse
}

// ─────────────────────────────────────────────────────────────────────────────
// Health polling → offline banner
// ─────────────────────────────────────────────────────────────────────────────

export type HealthState = 'online' | 'offline' | 'unknown'

/**
 * Polls /health and reports transitions. Returns a stop function.
 * Only fires the callback when the state actually changes.
 */
export function startHealthPoll(
  onChange: (state: HealthState, version?: string) => void,
  intervalMs = 5_000,
): () => void {
  let stopped = false
  let last: HealthState = 'unknown'
  let timer: ReturnType<typeof setTimeout> | undefined

  const emit = (state: HealthState, version?: string) => {
    if (state !== last) {
      last = state
      onChange(state, version)
    }
  }

  const tick = async () => {
    if (stopped) return
    try {
      const health = await getHealth()
      emit('online', health.version)
    } catch {
      emit('offline')
    }
    if (!stopped) timer = setTimeout(tick, intervalMs)
  }

  void tick()

  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
  }
}
