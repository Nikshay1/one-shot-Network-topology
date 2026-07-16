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
  ChatRequest,
  ChatResponse,
  ChatRetrieved,
  ChatRun,
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
  health: (): Promise<HealthResponse> => delay({ status: 'ok', version: '1.2' }, 20),

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

  /**
   * Mock chat always answers `deterministic` — and that is the honest shape, not a
   * shortcut: mock mode has no model, which is exactly the state the real backend is
   * in with no key, OFFLINE=1, or a tripped spend cap. So the fixture exercises the
   * degraded path the demo actually runs on, rather than a fantasy LLM reply.
   *
   * Retrieval is a crude word-overlap stand-in for the backend's TF-IDF. It does not
   * need to rank identically — it needs to return real fixture facts with real
   * fact_ids, so citation rendering is tested against ids that resolve.
   *
   * It does mirror one backend behaviour deliberately: the recommended fix is PINNED,
   * present whatever the question was. Without it "what should I do to fix this?"
   * retrieves nothing here — the word "fix" appears in no fixture statement — which
   * is the same vocabulary gap the real retriever needed `corpus.pinned()` to close.
   */
  /** GET /runs — the fixture ledger describes one case, so the picker shows one. */
  runs: (): Promise<ChatRun[]> =>
    delay([
      {
        run_id: 'case-001',
        case_id: 'case-001',
        n_facts: LEDGER.length,
        top_suspect: 'catalogue-db',
        tier: 'CONFIRMED',
        source: 'run',
      },
    ]),

  chat: (req: ChatRequest): Promise<ChatResponse> => {
    // Mirror the backend's scope gate. Not for fidelity's sake: without it, mock mode
    // would cheerfully answer "what's the weather" while the real backend refuses, and
    // the UI would be developed against a bot that behaves differently from the one
    // that ships.
    const qt = (req.question.toLowerCase().match(/[a-z0-9_-]+/g) ?? []).map((w) => w)
    // Mirrors backend/chat/scope.py's lexicon. Note what is NOT here: interrogatives
    // ("why", "what") and bare "recommend". The backend excludes them because they
    // carry no domain signal — "recommend" let a restaurant question through — and a
    // mock that is more permissive than the real gate is a mock that lies.
    const DOMAIN =
      /\b(anomal|evidence|fact|ledger|cause|root|suspect|blame|fix|remed|rehears|recommended|alternativ|counterfactual|twin|tier|confirmed|correlated|missing|rank|score|verdict|incident|latency|cpu|error|config|topology|path|component|service|cascade|symptom|detect|investigat|agent|ensemble|rule|innocent|redundant|restart|rollback|scale|throttle|instrument|telemetry|metric|log|alert|timeline)/
    const NAMES = new Set(LEDGER.flatMap((r) => r.component_ids.map((c) => c.toLowerCase())))
    const inScope =
      DOMAIN.test(req.question.toLowerCase()) || qt.some((w) => NAMES.has(w))

    if (!inScope) {
      const greeting = /^(hi|hello|hey|thanks|thank you|bye|ok)\b/i.test(req.question.trim())
      return delay(
        {
          answer: greeting
            ? "Hello. I'm the evidence assistant for this incident run — ask me about what " +
              'was detected, why a component is ranked where it is, or what to do about it.'
            : 'I can only answer questions about this incident — the evidence in this run’s ' +
              'ledger, the ranked suspects, and the fixes that were rehearsed. I don’t have ' +
              'anything to say about that.\n\nThings I can answer for this case:\n' +
              '- What should I do to fix this?\n' +
              '- What is the evidence for the top suspect?\n' +
              '- What did you rule out, and why?',
          mode: 'refused',
          citations: [],
          stripped: [],
          citations_valid: true,
          retrieved: [],
          usd: 0,
          attempts: 1,
        },
        200,
      )
    }

    const words = req.question.toLowerCase().match(/[a-z0-9_]+/g) ?? []
    const scored = LEDGER.map((row) => {
      const hay = `${row.kind} ${row.statement} ${row.component_ids.join(' ')}`.toLowerCase()
      return { row, score: words.filter((w) => w.length > 2 && hay.includes(w)).length }
    })
      .filter((s) => s.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 6)

    const rec = REMEDIATION.recommended
    const pinned: ChatRetrieved[] =
      rec && rec.fact_id
        ? [
            {
              fact_id: rec.fact_id,
              kind: 'remediation',
              text:
                `[remediation] RECOMMENDED remedy for ${REMEDIATION.component}: ${rec.remedy}. ` +
                `Rehearsed in the digital twin it cleared ${rec.symptoms_cleared_pct}% of ` +
                `simulated symptoms.`,
            },
          ]
        : []

    const retrieved: ChatRetrieved[] = [
      ...pinned,
      ...scored
        .filter((s) => s.row.fact_id !== rec?.fact_id)
        .map((s) => ({
          fact_id: s.row.fact_id,
          kind: 'ledger_fact' as const,
          text: `[${s.row.kind}] ${s.row.statement}`,
        })),
    ]

    const lines = scored.map((s) => `- [${s.row.kind}] ${s.row.statement} [${s.row.fact_id}]`)
    const answer = retrieved.length
      ? [
          'Answering from the evidence ledger directly (mock mode — no model available):',
          '',
          ...lines,
          ...(rec
            ? [
                '',
                `Recommended fix: **${rec.remedy}** on \`${REMEDIATION.component}\` — cleared ` +
                  `${rec.symptoms_cleared_pct}% of symptoms **in the digital twin**, not in ` +
                  `production${rec.fact_id ? ` [${rec.fact_id}]` : ''}.`,
              ]
            : []),
        ].join('\n')
      : 'No evidence in this run’s ledger matches that question. Try naming a component, or ' +
        'ask about the ranking, the counterfactual, the twin, or the recommended fix.'

    return delay(
      {
        answer,
        mode: 'deterministic',
        citations: retrieved.map((r) => r.fact_id).filter((id): id is string => Boolean(id)),
        stripped: [],
        citations_valid: true,
        retrieved,
        usd: 0,
        attempts: 1,
      },
      500, // a real chat call is a round-trip to a model; don't pretend it is instant
    )
  },

  reportPdf: (): Promise<Blob> => delay(stubPdf(), 200),
}
