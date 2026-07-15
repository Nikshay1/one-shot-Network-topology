/**
 * One agent's thread: every tool call it made, in order.
 *
 * Live rows come from `agent_step` — {agent, tool, args_summary, result_summary}
 * and nothing else, so a live row has no timestamp, no cost and no ok flag. The
 * historical transcript (application/x-ndjson) has ts/args/ok but only lands
 * once the run finishes and the agent is registered on RunVerdict.transcripts.
 * Both are shown; the source of each row is labelled rather than blended.
 *
 * The SPENT marker is mirrored from the tool registry, not read off the wire —
 * see lib/budget.ts.
 */
import { costOf, isExpensive } from '@/lib/budget'
import { Badge } from '@/components/ui/badge'
import { formatClock } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { AgentDoneEvent, AgentStepEvent, TranscriptStepLine } from '@/types/api'

export interface TranscriptRow {
  tool: string | null
  argsSummary: string
  resultSummary: string
  /** Only the transcript knows these. */
  ts?: number | undefined
  ok?: boolean | undefined
  args?: Record<string, unknown> | undefined
}

export function rowsFromSteps(steps: AgentStepEvent[]): TranscriptRow[] {
  return steps.map((s) => ({
    tool: s.tool,
    argsSummary: s.args_summary,
    resultSummary: s.result_summary,
  }))
}

export function rowsFromTranscript(steps: TranscriptStepLine[]): TranscriptRow[] {
  return steps.map((s) => ({
    tool: s.tool,
    argsSummary: JSON.stringify(s.args),
    resultSummary: s.result_summary,
    ts: s.ts,
    ok: s.ok,
    args: s.args,
  }))
}

function FiledFact({ row }: { row: TranscriptRow }) {
  // file_finding is the ONLY way an agent mutates anything (rule 9): it appends
  // to the ledger and can do nothing else.
  const factId = /fact-[A-Za-z0-9_.]+-[0-9]{4}/.exec(row.resultSummary)?.[0]
  const statement =
    typeof row.args?.statement === 'string' ? row.args.statement : row.argsSummary

  return (
    <div className="mt-1 rounded border border-emerald-500/30 bg-emerald-500/5 p-1.5">
      <div className="flex items-center gap-1.5">
        <Badge variant="outline" className="border-emerald-500/40 text-emerald-300">
          filed to ledger
        </Badge>
        {factId && <span className="font-mono text-[10px] text-emerald-300/80">{factId}</span>}
      </div>
      <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{statement}</p>
    </div>
  )
}

function Row({ row }: { row: TranscriptRow }) {
  const cost = costOf(row.tool)
  const failed = row.ok === false

  return (
    <li
      className={cn(
        'rounded-lg border border-border bg-card p-2',
        failed && 'border-rose-500/40 bg-rose-500/5',
      )}
      data-testid="transcript-row"
      data-tool={row.tool ?? ''}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant={failed ? 'danger' : 'default'} className="font-mono">
          {row.tool ?? 'unknown tool'}
        </Badge>

        {isExpensive(row.tool) && (
          <Badge
            variant="anomaly"
            className="font-bold"
            title="Cost mirrored from the backend tool registry — the stream carries no cost field."
          >
            SPENT {cost}/{cost === 1 ? '1' : '2'} pt{cost === 1 ? '' : 's'}
          </Badge>
        )}

        {failed && <Badge variant="danger">failed</Badge>}

        {row.ts !== undefined && (
          <span className="ml-auto font-mono text-[10px] text-muted-foreground">
            {formatClock(row.ts)}
          </span>
        )}
      </div>

      {row.argsSummary && (
        <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground/80">
          {row.argsSummary}
        </p>
      )}

      {row.tool === 'file_finding' ? (
        <FiledFact row={row} />
      ) : (
        row.resultSummary && (
          <p className="mt-1 break-words text-[11px] leading-snug text-muted-foreground">
            {row.resultSummary}
          </p>
        )
      )}
    </li>
  )
}

export interface AgentTranscriptProps {
  rows: TranscriptRow[]
  done?: AgentDoneEvent | undefined
  finalText?: string | null | undefined
  /** Where the rows came from, so nobody mistakes a live row for a full one. */
  source: 'live' | 'transcript'
  emptyNote?: string
}

export function AgentTranscript({
  rows,
  done,
  finalText,
  source,
  emptyNote = 'This agent has not called a tool yet.',
}: AgentTranscriptProps) {
  return (
    <div className="space-y-2">
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">{emptyNote}</p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((row, i) => (
            <Row key={`${row.tool}-${i}`} row={row} />
          ))}
        </ul>
      )}

      {done && (
        <div
          className={cn(
            'rounded-lg border p-2',
            done.status === 'completed'
              ? 'border-emerald-500/30 bg-emerald-500/5'
              : 'border-amber-500/30 bg-amber-500/5',
          )}
          role="status"
        >
          <div className="flex items-center gap-2">
            <Badge variant={done.status === 'completed' ? 'outline' : 'anomaly'}>
              {done.status}
            </Badge>
            <span className="text-[10px] text-muted-foreground">
              {done.status === 'budget_exhausted'
                ? 'the harness stopped it — rule 10, budgets are code not prompt text'
                : 'agent finished'}
            </span>
          </div>
          {/* `summary` is genuinely nullable on the wire. */}
          {done.summary && (
            <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{done.summary}</p>
          )}
        </div>
      )}

      {finalText && (
        <details className="rounded-lg border border-border bg-card p-2">
          <summary className="cursor-pointer text-[11px] text-muted-foreground">
            Agent&apos;s final report
          </summary>
          <p className="mt-1.5 whitespace-pre-wrap text-[11px] leading-relaxed text-muted-foreground">
            {finalText}
          </p>
        </details>
      )}

      <p className="text-[10px] text-muted-foreground/60">
        {source === 'live'
          ? 'Live from the run stream: agent_step carries a tool name and two summaries — no timestamp, no cost, no success flag.'
          : 'From the cached transcript (application/x-ndjson), which adds timestamps, full args and per-step success.'}
      </p>
    </div>
  )
}
