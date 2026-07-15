/**
 * Scripted SSE replayer for MOCK MODE (VITE_MOCK=1).
 *
 * There are TWO recordings, because a run ends in EITHER `pipeline_done` OR
 * `pipeline_error` — the bus drops everything published after a terminal event,
 * so no single run can contain both:
 *
 *  - sse_sequence.jsonl       the happy path: 14 event types, ends pipeline_done.
 *                             Also carries one deliberately unknown event type,
 *                             to prove the parser drops it rather than throwing.
 *  - sse_sequence_error.jsonl the failure path: ends pipeline_error.
 *
 * Together they cover all 15 contract types. Select the failure path with a
 * runId ending in `-error`, so the demo can reach it from a URL.
 *
 * Both go through the same toSseMessage() parser as the real EventSource path,
 * so mock mode exercises the real dispatch code rather than a parallel one, and
 * both honour the StreamHandle contract — including closing on a terminal event.
 */
import type { RunStreamOptions, StreamHandle, StreamStatus } from '@/api/stream'
import type { SseMessage } from '@/types/api'
import { isTerminalSseEvent } from '@/types/api'
import { toSseMessage } from '@/api/sseParse'
import rawSequence from './sse_sequence.jsonl?raw'
import rawErrorSequence from './sse_sequence_error.jsonl?raw'
import rawRedHerringSequence from './sse_sequence_redherring.jsonl?raw'

/** The raw recordings, by scenario. */
export const MOCK_RECORDINGS = {
  happy: rawSequence,
  error: rawErrorSequence,
  redherring: rawRedHerringSequence,
} as const

export type MockScenario = keyof typeof MOCK_RECORDINGS

/**
 * Which recording a run replays.
 *
 * `red_herring_config-*` gets the red-herring story, so DEMO 2 tells it without
 * the presenter doing anything. A `-error` suffix forces the failure path, which
 * is otherwise unreachable from the console.
 */
export function scenarioForRun(runId: string): MockScenario {
  if (runId.endsWith('-error')) return 'error'
  if (runId.startsWith('red_herring_config')) return 'redherring'
  return 'happy'
}

export interface SseRecord {
  event: string
  /** Kept as text so the mock path parses exactly like the wire path does. */
  raw: string
}

/** Parses the recording. Blank and malformed lines are skipped. */
export function loadMockSseRecords(text: string = rawSequence): SseRecord[] {
  const out: SseRecord[] = []
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      const parsed = JSON.parse(trimmed) as { event: string; data: unknown }
      if (typeof parsed.event !== 'string') continue
      out.push({ event: parsed.event, raw: JSON.stringify(parsed.data) })
    } catch {
      // A malformed line in a fixture shouldn't take the demo down.
    }
  }
  return out
}

/** The recording as typed messages, with unknown types dropped. For tests. */
export function loadMockSseMessages(text: string = rawSequence): SseMessage[] {
  const out: SseMessage[] = []
  for (const rec of loadMockSseRecords(text)) {
    const msg = toSseMessage(rec.event, rec.raw)
    if (msg) out.push(msg)
  }
  return out
}

/**
 * Per-type pacing, in ms. Chosen so the replay reads like a real run: ingest
 * bursts fast, agents think between tool calls, narration streams like tokens.
 * All values sit inside the 50–200ms (5–20 msg/s) band.
 */
const DELAY_MS: Record<string, number> = {
  event_ingested: 55,
  anomaly_detected: 90,
  blast_radius: 120,
  hypothesis_ranked: 140,
  tier_changed: 110,
  counterfactual_result: 150,
  twin_started: 100,
  twin_result: 160,
  challenger_attack: 170,
  agent_step: 180,
  agent_done: 190,
  remediation_result: 160,
  narration_chunk: 60,
}

const DEFAULT_DELAY_MS = 100
const JITTER = 0.3
const CONNECT_DELAY_MS = 120

function delayFor(event: string): number {
  const base = DELAY_MS[event] ?? DEFAULT_DELAY_MS
  return Math.round(base * (1 - JITTER / 2 + Math.random() * JITTER))
}

export interface MockStreamOptions extends RunStreamOptions {
  /** Override the recording (tests). */
  sequence?: SseRecord[]
  /** Multiply every delay; 0 replays as fast as the event loop allows. */
  speed?: number
}

/**
 * Same signature and semantics as openRunStream(), so no caller can tell the
 * difference between mock mode and a live backend.
 */
export function openMockRunStream(opts: MockStreamOptions): StreamHandle {
  const { runId, onMessage, onOpen, onStatus, onUnknown, sequence, speed = 1 } = opts
  const records = sequence ?? loadMockSseRecords(MOCK_RECORDINGS[scenarioForRun(runId)])

  let index = 0
  let closed = false
  let status: StreamStatus = 'connecting'
  let timer: ReturnType<typeof setTimeout> | undefined

  const setStatus = (next: StreamStatus, detail?: string) => {
    status = next
    onStatus?.(next, detail)
  }

  const stop = () => {
    if (timer) {
      clearTimeout(timer)
      timer = undefined
    }
  }

  const step = () => {
    if (closed) return

    const rec = records[index]
    if (!rec) {
      // Recording exhausted without a terminal event — treat as a clean close.
      setStatus('closed')
      return
    }
    index += 1

    const msg = toSseMessage(rec.event, rec.raw)
    if (!msg) {
      onUnknown?.(rec.event, rec.raw)
    } else {
      onMessage(msg)
      if (isTerminalSseEvent(msg.event)) {
        closed = true
        stop()
        setStatus(
          msg.event === 'pipeline_done' ? 'done' : 'error',
          msg.event === 'pipeline_error' ? (msg.data as { error: string }).error : undefined,
        )
        return
      }
    }

    const next = records[index]
    if (!next) {
      setStatus('closed')
      return
    }
    timer = setTimeout(step, delayFor(next.event) * speed)
  }

  timer = setTimeout(() => {
    if (closed) return
    setStatus('open')
    // Mirrors the real bus: every open replays the run from index 0.
    onOpen?.()
    step()
  }, CONNECT_DELAY_MS * speed)

  return {
    close: () => {
      closed = true
      stop()
      setStatus('closed')
    },
    status: () => status,
  }
}
