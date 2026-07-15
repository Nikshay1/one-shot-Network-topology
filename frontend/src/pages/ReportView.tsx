/**
 * The narration, rendered, with every [fact-…] citation resolvable.
 *
 * Live text comes from narration_chunk deltas; a reload falls back to
 * GET /run/{id}/narration, which returns the same text as chunks.
 *
 * CONTRACT GAP — the citations_valid banner. `Narration.citations_valid` is real
 * (narrator.py:36-43), is computed correctly, and is thrown away at the endpoint
 * boundary: /run/{id}/narration returns {run_id, chunks} and touches only
 * `.text`. mode, citations, stripped, citations_valid and attempts never leave
 * the process, and no other endpoint serialises the object — the PDF doesn't
 * show it either. So a run CAN legitimately end with citations_valid=false and a
 * report full of deleted claims, and the frontend has no way to know. Rather
 * than invent a banner that can never fire, this resolves every citation against
 * the ledger and reports what it finds: an unresolvable citation is the one
 * symptom of the failure that IS visible from here.
 */
import { useMemo, useState } from 'react'
import Markdown from 'react-markdown'
import { useLedger } from '@/hooks/useLedger'
import { useNarration } from '@/hooks/useNarration'
import { useRunStore } from '@/store/useRunStore'
import { reportPdfUrl, IS_MOCK, getReportPdf } from '@/api/client'
import { tokenizeCitations } from '@/lib/citations'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { LedgerRecord } from '@/types/ledger'

function CitationChip({ factId, fact }: { factId: string; fact: LedgerRecord | undefined }) {
  // Hover and click are tracked separately. One `open` toggled by both fights
  // itself: a click is preceded by a mouseenter, so the toggle would close what
  // the hover just opened. Hover previews; click and keyboard focus pin it open,
  // which is also what rule 7 needs — no action may require hover.
  const [pinned, setPinned] = useState(false)
  const [hovered, setHovered] = useState(false)
  const open = pinned || hovered

  return (
    <span className="relative inline-block align-baseline">
      <button
        type="button"
        onClick={() => setPinned((v) => !v)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setHovered(true)}
        onBlur={() => setHovered(false)}
        aria-expanded={open}
        className={cn(
          'mx-0.5 rounded border px-1 py-0.5 font-mono text-[10px] align-baseline',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          fact
            ? 'border-border bg-background text-muted-foreground hover:border-ring hover:text-foreground'
            : 'border-rose-500/50 bg-rose-500/10 text-rose-300',
        )}
      >
        {factId}
      </button>

      {open && (
        <span
          role="dialog"
          aria-label={`fact ${factId}`}
          className="absolute bottom-full left-0 z-50 mb-1 block w-80 rounded-md border border-border bg-popover p-2 shadow-xl"
        >
          {fact ? (
            <>
              <span className="mb-1 flex items-center gap-1.5">
                <Badge variant="outline">{fact.kind}</Badge>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {fact.component_ids.join(', ')}
                </span>
              </span>
              <span className="block text-[11px] leading-snug text-muted-foreground">
                {fact.statement}
              </span>
            </>
          ) : (
            <span className="block text-[11px] leading-snug text-rose-300">
              This citation does not resolve to any fact in the run&apos;s ledger. The narrator
              deletes whole claims whose citations don&apos;t resolve, so a surviving broken
              citation means validation did not clean it up.
            </span>
          )}
        </span>
      )}
    </span>
  )
}

/** Replaces [fact-…] runs inside any markdown text node with chips. */
function withCitations(children: React.ReactNode, facts: Map<string, LedgerRecord>): React.ReactNode {
  const render = (node: React.ReactNode, key: number): React.ReactNode => {
    if (typeof node !== 'string') return node
    const tokens = tokenizeCitations(node)
    if (tokens.length === 1 && tokens[0]!.kind === 'text') return node
    return (
      <span key={key}>
        {tokens.map((t, i) =>
          t.kind === 'text' ? (
            <span key={i}>{t.value}</span>
          ) : (
            <CitationChip key={i} factId={t.factId} fact={facts.get(t.factId)} />
          ),
        )}
      </span>
    )
  }

  return Array.isArray(children) ? children.map(render) : render(children, 0)
}

export function ReportView({ runId }: { runId: string }) {
  const status = useRunStore((s) => s.status)
  const done = status === 'done'
  const streamed = useRunStore((s) => s.narration)
  const { text: fetched, loading } = useNarration(runId, done && streamed.length === 0)
  const { facts } = useLedger(runId, {}, done ? 1 : 0)
  const [downloading, setDownloading] = useState(false)

  const text = streamed || fetched
  const factsById = useMemo(() => new Map(facts.map((f) => [f.fact_id, f])), [facts])

  const unresolved = useMemo(() => {
    if (facts.length === 0) return []
    return [...new Set(tokenizeCitations(text).flatMap((t) => (t.kind === 'citation' ? [t.factId] : [])))].filter(
      (id) => !factsById.has(id),
    )
  }, [text, facts.length, factsById])

  const download = async () => {
    setDownloading(true)
    try {
      const blob = await getReportPdf(runId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${runId}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-auto">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{streamed ? 'streamed live' : 'fetched'}</Badge>
        {facts.length > 0 && (
          <Badge variant="outline">{factsById.size} facts in ledger</Badge>
        )}
        <div className="ml-auto">
          {IS_MOCK ? (
            <Button size="sm" variant="outline" onClick={() => void download()} disabled={downloading}>
              {downloading ? 'Preparing…' : 'Download audit report (PDF)'}
            </Button>
          ) : (
            <Button size="sm" variant="outline" asChild>
              <a href={reportPdfUrl(runId)} target="_blank" rel="noreferrer">
                Download audit report (PDF)
              </a>
            </Button>
          )}
        </div>
      </div>

      {unresolved.length > 0 && (
        <div role="alert" className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-3">
          <p className="text-sm text-rose-300">
            {unresolved.length} citation{unresolved.length === 1 ? '' : 's'} in this report
            do{unresolved.length === 1 ? 'es' : ''} not resolve to a fact in the ledger.
          </p>
          <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
            Treat the surrounding claims as unsupported: {unresolved.join(', ')}
          </p>
        </div>
      )}

      {!text ? (
        <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-border">
          <p className="max-w-sm p-6 text-center text-sm text-muted-foreground">
            {loading
              ? 'Loading the narration…'
              : status === 'error'
                ? 'The run failed, so no report was written.'
                : 'The report is written last — narration streams once the verdict is settled.'}
          </p>
        </div>
      ) : (
        <article
          className="rounded-lg border border-border bg-card p-4"
          data-testid="report-markdown"
        >
          <div className="space-y-2 text-sm leading-relaxed">
            <Markdown
              components={{
                h2: ({ children }) => (
                  <h2 className="mt-4 text-base font-semibold tracking-tight first:mt-0">
                    {children}
                  </h2>
                ),
                h3: ({ children }) => (
                  <h3 className="mt-3 text-sm font-semibold text-muted-foreground">{children}</h3>
                ),
                p: ({ children }) => (
                  <p className="text-sm leading-relaxed text-foreground/90">
                    {withCitations(children, factsById)}
                  </p>
                ),
                li: ({ children }) => (
                  <li className="ml-4 list-disc text-sm leading-relaxed text-foreground/90">
                    {withCitations(children, factsById)}
                  </li>
                ),
                ul: ({ children }) => <ul className="space-y-1">{children}</ul>,
                code: ({ children }) => (
                  <code className="rounded bg-secondary px-1 py-0.5 font-mono text-[11px]">
                    {children}
                  </code>
                ),
                strong: ({ children }) => (
                  <strong className="font-semibold text-foreground">{children}</strong>
                ),
              }}
            >
              {text}
            </Markdown>
          </div>
        </article>
      )}

      <p className="text-[10px] leading-snug text-muted-foreground/60">
        The narrator can only cite ledger fact ids — its one tool is query_evidence_ledger, so it
        never sees an event id. Claims whose citations don&apos;t resolve are deleted from the
        report by the backend rather than flagged; the API does not expose the resulting
        citations_valid flag, so the check above is this page resolving every citation itself.
      </p>
    </div>
  )
}
