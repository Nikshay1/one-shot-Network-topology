/**
 * application/x-ndjson parsing for the agent-transcript endpoint. Extracted
 * from client.ts so the mock fixtures can reuse it without an import cycle
 * (client.ts imports the mocks).
 */
import type { TranscriptLine } from '@/types/api'

/**
 * Splits an NDJSON body into transcript lines.
 * An unparseable line is skipped rather than fatal: a truncated tail line is
 * not worth failing a whole transcript over.
 */
export function parseNdjson(text: string): TranscriptLine[] {
  const out: TranscriptLine[] = []
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      out.push(JSON.parse(trimmed) as TranscriptLine)
    } catch {
      // skip
    }
  }
  return out
}
