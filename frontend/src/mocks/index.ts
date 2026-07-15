/**
 * MOCK MODE data source (VITE_MOCK=1).
 *
 * Every payload here is fixture JSON copied verbatim from the repo's /fixtures
 * (schema-valid, hand-written) or shaped from the backend dataclasses for the
 * three endpoints that have no schema file (remediation, benchmark, transcripts).
 *
 * This module IS the backend in mock mode, so it is the one place in the
 * frontend allowed to synthesize a response. It still never invents a field:
 * the shapes are exactly api_contract v1.1.
 *
 * Note the fixtures all describe case-001, so any case_id is served the same
 * data. That is a mock convenience, not a contract behaviour.
 */
import type {
  AgentName,
  AnomalyEvent,
  BenchmarkResponse,
  CaseSummary,
  CounterfactualRequest,
  CounterfactualResponse,
  HealthResponse,
  LedgerQuery,
  LedgerRecord,
  NarrationResponse,
  RankedHypothesis,
  RemediationReport,
  StartRunResult,
  TopologyGraph,
  TranscriptLine,
  VerdictResponse,
} from '@/types/api'
import { parseNdjson } from '@/api/ndjson'
import { loadMockSseMessages, MOCK_RECORDINGS, scenarioForRun } from './mockStream'
import { mockTopology } from './topology'

import casesJson from './fixtures/mock_cases.json'
import anomaliesJson from './fixtures/sample_anomalies.json'
import ledgerJson from './fixtures/sample_ledger.json'
import remediationJson from './fixtures/mock_remediation.json'
import benchmarkJson from './fixtures/mock_benchmark.json'

import investigatorRaw from './fixtures/transcript_investigator.jsonl?raw'
import challengerRaw from './fixtures/transcript_challenger.jsonl?raw'
import remediationRaw from './fixtures/transcript_remediation.jsonl?raw'

const CASES = casesJson as CaseSummary[]
const ANOMALIES = anomaliesJson as AnomalyEvent[]
const LEDGER = ledgerJson as LedgerRecord[]
const REMEDIATION = remediationJson as RemediationReport
const BENCHMARK = benchmarkJson as unknown as BenchmarkResponse

const TRANSCRIPTS: Record<AgentName, string> = {
  investigator: investigatorRaw,
  challenger: challengerRaw,
  remediation: remediationRaw,
}

/** A touch of latency so loading states are actually visible in mock mode. */
const LATENCY_MS = 120

function delay<T>(value: T, ms = LATENCY_MS): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

/** Deep copy, so a caller mutating a result can't corrupt the fixture. */
function clone<T>(value: T): T {
  return structuredClone(value)
}

/**
 * The hypotheses a run ends on, replayed from its recording — the same
 * full-object upsert the store does, so `/verdict` and the counterfactual agree
 * with what the stream showed.
 */
function finalHypotheses(runId: string): RankedHypothesis[] {
  const byId = new Map<string, RankedHypothesis>()
  for (const msg of loadMockSseMessages(MOCK_RECORDINGS[scenarioForRun(runId)])) {
    if (msg.event === 'hypothesis_ranked') byId.set(msg.data.hypothesis_id, msg.data)
  }
  return [...byId.values()].sort((a, b) => a.rank - b.rank)
}

/** Minimal valid single-page PDF, so the report button has something to open. */
function stubPdf(): Blob {
  const pdf = [
    '%PDF-1.4',
    '1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj',
    '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj',
    '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj',
    '4 0 obj<</Length 60>>stream',
    'BT /F1 12 Tf 20 50 Td (VERDICT mock report - case-001) Tj ET',
    'endstream endobj',
    '5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj',
    'trailer<</Root 1 0 R>>',
    '%%EOF',
  ].join('\n')
  return new Blob([pdf], { type: 'application/pdf' })
}

export const mockApi = {
  health: (): Promise<HealthResponse> => delay({ status: 'ok', version: '1.1' }, 20),

  cases: (): Promise<CaseSummary[]> => delay(clone(CASES)),

  /**
   * Shape depends on the case, exactly as it does in production: synthetic cases
   * carry `instrumented`, missing_telemetry sets some false, real cases omit the
   * key entirely.
   */
  topology: (caseId: string): Promise<TopologyGraph> => delay(mockTopology(caseId)),

  startRun: (caseId: string): Promise<StartRunResult> =>
    delay({ status: 'started', run_id: caseId, stream: `/stream/${caseId}` }, 60),

  verdict: (runId: string): Promise<VerdictResponse> =>
    delay({
      run_id: runId,
      case_id: runId,
      hypotheses: finalHypotheses(runId),
      done: true,
    }),

  anomalies: (): Promise<AnomalyEvent[]> => delay(clone(ANOMALIES)),

  ledger: (query: LedgerQuery): Promise<LedgerRecord[]> => {
    const rows = LEDGER.filter((row) => {
      if (query.kind && row.kind !== query.kind) return false
      if (query.hypothesis_id && row.hypothesis_id !== query.hypothesis_id) return false
      if (query.component_id && !row.component_ids.includes(query.component_id)) return false
      return true
    })
    return delay(clone(rows))
  },

  narration: (runId: string): Promise<NarrationResponse> => {
    // Reuse the scripted stream's chunks so the streamed narration and the
    // fetched one are the same text, exactly as the backend guarantees.
    const chunks = loadMockSseMessages()
      .filter((msg) => msg.event === 'narration_chunk')
      .map((msg) => msg.data as { ts: number; text: string })
    return delay({ run_id: runId, chunks })
  },

  /**
   * Answers from the recording the run is actually replaying, so the number the
   * toggle shows agrees with the hypothesis card next to it. Mirrors the real
   * handler exactly: `affected_hypotheses` is the hypotheses whose OWN suspect
   * is the removed component (app.py:406-408), which is 0 or 1 of them — not
   * "hypotheses whose score would change".
   */
  counterfactual: (runId: string, req: CounterfactualRequest): Promise<CounterfactualResponse> => {
    const hit = finalHypotheses(runId).find((h) => h.suspect_component === req.remove_component)
    return delay(
      {
        removed: req.remove_component,
        anomalies_still_explained_pct: hit?.counterfactual.anomalies_still_explained_pct ?? 100,
        affected_hypotheses: hit ? [hit.hypothesis_id] : [],
      },
      400, // counterfactuals are expensive backend-side; don't pretend otherwise
    )
  },

  remediation: (_runId: string): Promise<RemediationReport | null> => delay(clone(REMEDIATION)),

  transcript: (agent: AgentName): Promise<TranscriptLine[] | null> => {
    const raw = TRANSCRIPTS[agent]
    return delay(raw ? parseNdjson(raw) : null)
  },

  benchmark: (): Promise<BenchmarkResponse> => delay(clone(BENCHMARK)),

  reportPdf: (): Promise<Blob> => delay(stubPdf(), 200),
}
