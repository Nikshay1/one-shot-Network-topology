/**
 * Ask the evidence.
 *
 * A case picker plus a chat window over POST /run/{id}/chat, which is RAG across
 * the selected run's ledger. Things this page is deliberate about:
 *
 *  1. **It never implies the model decided anything.** Tiers and ranks come from
 *     the scorer (rules 5 and 12); chat only explains what is already in the
 *     ledger. The header says so, because a chat box next to a verdict invites
 *     exactly the opposite assumption.
 *  2. **`mode` is always visible.** `deterministic` means no model answered — no
 *     key, OFFLINE, or the spend cap tripped. `refused` means the question was not
 *     about the incident and no model was called at all. A 200 is not proof an LLM
 *     spoke, so the badge is not decoration.
 *  3. **Citations are chips, not raw `[fact-...]`.** Every id in `citations`
 *     resolved backend-side; anything that did not had its claim deleted.
 *     `stripped` being non-empty means the model tried to invent one.
 *  4. **Each case keeps its own conversation.** Switching case must not carry
 *     history across: the evidence is different, so a follow-up like "and the
 *     alternative?" would be answered from the wrong run's ledger.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowUp, MessageSquare, ShieldCheck, TriangleAlert } from 'lucide-react'
import { ApiError, getChatRuns, postChat } from '@/api/client'
import { useRunStore } from '@/store/useRunStore'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { TIER_TOKEN } from '@/theme/tokens'
import { cn } from '@/lib/utils'
import type { ChatResponse, ChatRetrieved, ChatRun, ChatTurn } from '@/types/api'
import type { Tier } from '@/types/hypothesis'

/** The two questions this feature was built for, plus the one that shows the discipline. */
const SUGGESTIONS = [
  'What should I do to fix this?',
  'What is the evidence for the top suspect?',
  'What did you rule out, and why?',
]

const MODE_TONE: Record<ChatResponse['mode'], string> = {
  llm: 'border-tier-confirmed/40 bg-tier-confirmed/10 text-tier-confirmed',
  cached: 'border-border bg-secondary text-muted-foreground',
  deterministic: 'border-tier-correlated/40 bg-tier-correlated/10 text-tier-correlated',
  refused: 'border-border bg-secondary text-muted-foreground',
}

const MODE_HELP: Record<ChatResponse['mode'], string> = {
  llm: 'A model answered, grounded in retrieved ledger facts. Every citation resolves.',
  cached: 'This exact question was already answered for this run — replayed for $0.00.',
  deterministic:
    'No model answered (no API key, OFFLINE mode, or the spend cap tripped). These are ' +
    'the retrieved facts quoted directly — the evidence, without the prose.',
  refused:
    'Not a question about this incident, so it was declined before any model was called. ' +
    'The scope gate is code, not a prompt instruction.',
}

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  meta?: ChatResponse
  failed?: boolean
}

/** `[fact-catalogue-0003]` → a chip. Everything else renders as-is. */
function withCitations(text: string) {
  const parts = text.split(/(\[fact-[A-Za-z0-9_.]+-\d{4}\])/g)
  return parts.map((part, i) => {
    const m = part.match(/^\[(fact-[A-Za-z0-9_.]+-\d{4})\]$/)
    if (!m) return <span key={i}>{part}</span>
    return (
      <span
        key={i}
        title={`${m[1]} — a fact id in this run's evidence ledger`}
        className="mx-0.5 inline-block rounded border border-primary/30 bg-primary/10 px-1 py-px align-baseline font-mono text-[10px] text-primary"
      >
        {m[1]}
      </span>
    )
  })
}

/**
 * Minimal markdown: the backend emits `- ` bullets and `**bold**`, and nothing
 * else. Pulling in a markdown renderer for two constructs would be more surface
 * area than the feature has.
 */
function AnswerBody({ text }: { text: string }) {
  return (
    <div className="space-y-1.5 text-sm leading-relaxed">
      {text.split('\n').map((line, i) => {
        if (!line.trim()) return <div key={i} className="h-1.5" />
        const bullet = line.trimStart().startsWith('- ')
        const body = bullet ? line.trimStart().slice(2) : line
        const bolded = body.split(/(\*\*[^*]+\*\*)/g).map((seg, j) =>
          seg.startsWith('**') && seg.endsWith('**') ? (
            <strong key={j} className="font-semibold text-foreground">
              {withCitations(seg.slice(2, -2))}
            </strong>
          ) : (
            <span key={j}>{withCitations(seg)}</span>
          ),
        )
        return (
          <div key={i} className={cn('text-foreground/90', bullet && 'flex gap-2 pl-1')}>
            {bullet && <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-primary" />}
            <span>{bolded}</span>
          </div>
        )
      })}
    </div>
  )
}

function EvidenceUsed({ retrieved }: { retrieved: ChatRetrieved[] }) {
  const [open, setOpen] = useState(false)
  if (retrieved.length === 0) return null
  return (
    <div className="mt-3 border-t border-border/60 pt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground hover:text-foreground"
      >
        {open ? '▾' : '▸'} {retrieved.length} chunks retrieved
      </button>
      {open && (
        <ul className="mt-2 space-y-1.5">
          {retrieved.map((r, i) => (
            <li key={i} className="flex gap-2 text-xs text-muted-foreground">
              <span className="shrink-0 font-mono text-[10px] text-primary/70">
                {r.fact_id ?? '—'}
              </span>
              <span className="truncate" title={r.text}>
                {r.text}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function MessageBubble({ msg }: { msg: Message }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-lg rounded-br-sm bg-primary/15 px-3.5 py-2.5 text-sm text-foreground">
          {msg.content}
        </div>
      </div>
    )
  }
  return (
    <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}>
      <div
        className={cn(
          'max-w-[92%] rounded-lg rounded-bl-sm border border-border bg-card px-3.5 py-3',
          msg.failed && 'border-rose-500/40 bg-rose-500/5',
        )}
      >
        {msg.meta && (
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge
              className={cn('font-mono text-[10px] uppercase', MODE_TONE[msg.meta.mode])}
              title={MODE_HELP[msg.meta.mode]}
            >
              {msg.meta.mode}
            </Badge>
            {msg.meta.usd > 0 && (
              <span className="font-mono text-[10px] text-muted-foreground">
                ${msg.meta.usd.toFixed(4)}
              </span>
            )}
            {msg.meta.mode !== 'refused' &&
              (msg.meta.citations_valid ? (
                <span
                  className="flex items-center gap-1 font-mono text-[10px] text-muted-foreground"
                  title="Every [fact-...] in this answer resolves in the run's ledger."
                >
                  <ShieldCheck className="h-3 w-3" /> citations resolve
                </span>
              ) : (
                <span
                  className="flex items-center gap-1 font-mono text-[10px] text-tier-correlated"
                  title={`Unresolvable citations: ${msg.meta.stripped.join(', ')}. The claims carrying them were deleted.`}
                >
                  <TriangleAlert className="h-3 w-3" /> {msg.meta.stripped.length} claim(s) stripped
                </span>
              ))}
          </div>
        )}
        <AnswerBody text={msg.content} />
        {msg.meta && <EvidenceUsed retrieved={msg.meta.retrieved} />}
      </div>
    </motion.div>
  )
}

function TierChip({ tier }: { tier: Tier | null }) {
  if (!tier) return null
  const t = TIER_TOKEN[tier]
  return (
    <span
      className={cn('rounded px-1 py-px font-mono text-[9px] uppercase', t?.bg, t?.text)}
      title={`The scorer's tier for this run's leading suspect`}
    >
      {tier === 'MISSING_EVIDENCE' ? 'missing' : tier.toLowerCase()}
    </span>
  )
}

function CaseList({
  runs,
  selected,
  onSelect,
}: {
  runs: ChatRun[]
  selected: string | null
  onSelect: (id: string) => void
}) {
  return (
    <ul className="space-y-1">
      {runs.map((r) => {
        const active = r.run_id === selected
        return (
          <li key={r.run_id}>
            <button
              onClick={() => onSelect(r.run_id)}
              aria-current={active ? 'true' : undefined}
              className={cn(
                'w-full rounded-lg border px-2.5 py-2 text-left transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                active
                  ? 'border-primary/50 bg-primary/5'
                  : 'border-border bg-card hover:border-primary/30',
              )}
            >
              {/* The id gets the whole line. Sharing it with the tier chip truncated
                  BOTH red_herring_config variants to "red_herring_config-…", which
                  made the two cases indistinguishable in the one place whose entire
                  job is telling them apart. */}
              <div className="font-mono text-[11px] leading-tight text-foreground">{r.run_id}</div>
              <div className="mt-1 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <TierChip tier={r.tier} />
                {r.top_suspect ? (
                  <span className="truncate text-foreground/80">{r.top_suspect}</span>
                ) : (
                  <span className="italic">no verdict parsed</span>
                )}
                <span className="ml-auto shrink-0 font-mono tabular-nums">{r.n_facts}f</span>
              </div>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export function ChatView() {
  const currentRunId = useRunStore((s) => s.runId)
  const [runs, setRuns] = useState<ChatRun[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  // Per-case, because the evidence is per-case: carrying a follow-up across a
  // switch would answer it from a different run's ledger.
  const [threads, setThreads] = useState<Record<string, Message[]>>({})
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  const messages = useMemo(() => (selected ? (threads[selected] ?? []) : []), [threads, selected])

  useEffect(() => {
    let live = true
    getChatRuns()
      .then((rows) => {
        if (!live) return
        setRuns(rows)
        // Prefer the run being watched right now; otherwise the newest finished one.
        setSelected((prev) => {
          if (prev && rows.some((r) => r.run_id === prev)) return prev
          if (currentRunId && rows.some((r) => r.run_id === currentRunId)) return currentRunId
          return rows.length ? rows[rows.length - 1]!.run_id : null
        })
      })
      .catch((err) => live && setLoadError(err instanceof Error ? err.message : String(err)))
    return () => {
      live = false
    }
  }, [currentRunId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, busy])

  const push = (runId: string, msg: Message) =>
    setThreads((prev) => ({ ...prev, [runId]: [...(prev[runId] ?? []), msg] }))

  async function ask(question: string) {
    const q = question.trim()
    const runId = selected
    if (!q || busy || !runId) return
    setInput('')
    setBusy(true)

    // History is this case's transcript, so a follow-up ("and the alternative?")
    // has an antecedent. The backend caps it at 6 turns and trims each one.
    const history: ChatTurn[] = (threads[runId] ?? []).map((m) => ({
      role: m.role,
      content: m.content,
    }))
    push(runId, { id: `u-${(threads[runId] ?? []).length}`, role: 'user', content: q })

    try {
      const res = await postChat(runId, { question: q, history })
      push(runId, {
        id: `a-${(threads[runId] ?? []).length}-${Date.now()}`,
        role: 'assistant',
        content: res.answer,
        meta: res,
      })
    } catch (err) {
      const notReady = err instanceof ApiError && err.status === 404
      push(runId, {
        id: `e-${Date.now()}`,
        role: 'assistant',
        failed: true,
        content: notReady
          ? 'This run has not produced a verdict yet, so there is no evidence to answer from. Wait for the pipeline to finish and ask again.'
          : `The chat request failed: ${err instanceof Error ? err.message : String(err)}`,
      })
    } finally {
      setBusy(false)
    }
  }

  // ── empty states ──────────────────────────────────────────────────────────
  if (loadError) {
    return (
      <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center gap-3 px-6 text-center">
        <TriangleAlert className="h-9 w-9 text-tier-correlated" />
        <h1 className="font-display text-2xl">Could not load the case list</h1>
        <p className="max-w-md text-sm text-muted-foreground">{loadError}</p>
        <p className="text-xs text-muted-foreground">Is the VERDICT API running on :8000?</p>
      </div>
    )
  }

  if (runs !== null && runs.length === 0) {
    return (
      <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center gap-4 px-6 text-center">
        <MessageSquare className="h-10 w-10 text-muted-foreground/50" />
        <h1 className="font-display text-2xl">No finished cases yet</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          The chat answers from a run's evidence ledger — the anomalies, the counterfactuals,
          the twin, and the rehearsed fixes. Run a case and every one of those becomes
          something you can ask about.
        </p>
        <Button asChild>
          <Link to="/">Pick a case</Link>
        </Button>
      </div>
    )
  }

  return (
    <div className="mx-auto flex h-full max-w-6xl gap-6 px-6 py-5">
      {/* ── the case picker ───────────────────────────────────────────────── */}
      <aside className="flex w-60 shrink-0 flex-col">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
          {runs === null ? 'Loading cases…' : `${runs.length} finished case${runs.length === 1 ? '' : 's'}`}
        </p>
        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          {runs === null ? (
            <div className="space-y-1">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : (
            <CaseList runs={runs} selected={selected} onSelect={setSelected} />
          )}
        </div>
      </aside>

      {/* ── the conversation ──────────────────────────────────────────────── */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="mb-4 shrink-0">
          <h1 className="font-display text-2xl leading-none tracking-tight">Ask the evidence</h1>
          <p className="mt-1.5 text-xs text-muted-foreground">
            {selected ? (
              <>
                Grounded in <span className="font-mono text-foreground/80">{selected}</span>'s
                ledger. Answers cite fact ids that resolve, or the claim is deleted before you
                see it. The ranking and tiers are the scorer's — chat explains the evidence, it
                does not decide the verdict.
              </>
            ) : (
              'Pick a case on the left to ask about it.'
            )}
          </p>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pb-4">
          {selected && messages.length === 0 && (
            <div className="space-y-2 pt-4">
              <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                Try asking
              </p>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => void ask(s)}
                  className="block w-full rounded-lg border border-border bg-card px-3.5 py-2.5 text-left text-sm text-foreground/80 transition-colors hover:border-primary/40 hover:text-foreground"
                >
                  {s}
                </button>
              ))}
              <p className="pt-1 text-[11px] text-muted-foreground">
                Questions that aren't about this incident are declined — no model is called.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} msg={m} />
          ))}
          {busy && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
              retrieving evidence…
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            void ask(input)
          }}
          className="flex shrink-0 items-center gap-2 border-t border-border pt-3"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy || !selected}
            placeholder={
              selected ? 'Ask about the evidence, or what to do about it…' : 'Pick a case first…'
            }
            aria-label="Ask a question about this run"
            className="min-w-0 flex-1 rounded-lg border border-border bg-card px-3.5 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-60"
          />
          <Button type="submit" size="icon" disabled={busy || !input.trim() || !selected} aria-label="Send">
            <ArrowUp className="h-4 w-4" />
          </Button>
        </form>
      </div>
    </div>
  )
}
