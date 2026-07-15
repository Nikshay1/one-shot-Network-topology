/**
 * Agent theater: what the bounded agents actually did.
 */
import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AgentTranscript,
  rowsFromSteps,
  rowsFromTranscript,
} from '@/components/AgentTranscript'
import { BudgetMeter } from '@/components/BudgetMeter'
import { ChallengerPanel } from '@/components/ChallengerPanel'
import { TwinCompare } from '@/components/TwinCompare'
import { RemediationPanel } from '@/components/RemediationPanel'
import { useTranscript } from '@/hooks/useTranscript'
import { useRemediation } from '@/hooks/useRemediation'
import { useRunStore, useTopHypothesis } from '@/store/useRunStore'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import { AGENT_NAMES } from '@/types/api'
import type { AgentName } from '@/types/api'

const AGENT_TABS: { agent: AgentName; label: string }[] = [
  { agent: 'investigator', label: 'Investigator' },
  { agent: 'challenger', label: 'Challenger' },
  { agent: 'remediation', label: 'Fix-Rehearsal' },
]

function AgentPane({ runId, agent, done }: { runId: string; agent: AgentName; done: boolean }) {
  const steps = useRunStore((s) => s.agentSteps[agent])
  const agentDone = useRunStore((s) => s.agentDone[agent])
  const transcript = useTranscript(runId, agent, done)

  const liveSteps = useMemo(() => steps ?? [], [steps])

  // The transcript is richer (timestamps, full args, per-step ok) but only
  // exists once the run has finished; live steps carry the run as it happens.
  const useHistorical = transcript.found && transcript.steps.length > 0
  const rows = useHistorical ? rowsFromTranscript(transcript.steps) : rowsFromSteps(liveSteps)

  return (
    <div className="space-y-3">
      <BudgetMeter
        agent={agent}
        steps={liveSteps}
        done={agentDone}
        elapsedS={transcript.elapsedS}
      />

      {/*
        An agent that did nothing needs explaining, or the most interesting view
        in the product reads as "broken". Three ways it legitimately happens:
        no LLM and no cached transcript to replay; the harness cut it off; or the
        run fell back to the deterministic autopilot. In every case rule 11 means
        the verdict on the other tabs is still real — it was computed without it.
      */}
      {done && !transcript.loading && rows.length === 0 && (
        <div className="space-y-1.5 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
          <p className="text-xs text-amber-300">This agent contributed nothing to this run.</p>
          {agentDone?.summary && (
            <p className="font-mono text-[10px] text-muted-foreground">{agentDone.summary}</p>
          )}
          <p className="text-[11px] leading-snug text-muted-foreground">
            {(agentDone?.summary ?? '').includes('no LLM available')
              ? 'There was no API key and no cached transcript keyed to this run, so the agent could not act. The cache key is sha256(run_id + the ledger digest at agent start + prompt version), so a transcript warmed from a different run does not match.'
              : 'The agent did not complete any tool calls.'}{' '}
            Rule 11 then hands the run to the deterministic autopilot, which is why the verdict, the
            counterfactuals and the twin on the other tabs still exist — they were computed without
            this agent.
          </p>
        </div>
      )}

      <AgentTranscript
        rows={rows}
        done={agentDone}
        finalText={transcript.result?.final_text}
        source={useHistorical ? 'transcript' : 'live'}
        emptyNote="No tool calls."
      />
    </div>
  )
}

export function AgentsView({ runId }: { runId: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const status = useRunStore((s) => s.status)
  const done = status === 'done'

  const top = useTopHypothesis()
  const twinResults = useRunStore((s) => s.twinResults)
  const challengerAttacks = useRunStore((s) => s.challengerAttacks)
  const remediationEvents = useRunStore((s) => s.remediation)
  const challengerTranscript = useTranscript(runId, 'challenger', done)
  const { report, loading } = useRemediation(runId, done)

  const attacks = useMemo(() => [...challengerAttacks.values()].flat(), [challengerAttacks])
  const live = useMemo(() => [...remediationEvents.values()], [remediationEvents])
  const twinResult = top ? twinResults.get(top.hypothesis_id) : undefined

  const pane = searchParams.get('agent') ?? 'investigator'
  const setPane = (next: string) => {
    const params = new URLSearchParams(searchParams)
    params.set('agent', next)
    setSearchParams(params, { replace: true })
  }

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-auto">
      <Tabs value={pane} onValueChange={setPane} className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <TabsList>
            {AGENT_TABS.map((t) => (
              <TabsTrigger key={t.agent} value={t.agent}>
                {t.label}
              </TabsTrigger>
            ))}
            <TabsTrigger value="twin">Twin</TabsTrigger>
          </TabsList>
          {!done && <Badge variant="outline">streaming — transcripts land when the run ends</Badge>}
        </div>

        {AGENT_NAMES.map((agent) => (
          <TabsContent key={agent} value={agent}>
            {agent === 'challenger' ? (
              <div className="space-y-4">
                <AgentPane runId={runId} agent="challenger" done={done} />
                <ChallengerPanel
                  attacks={attacks}
                  finalText={challengerTranscript.result?.final_text}
                />
              </div>
            ) : agent === 'remediation' ? (
              <div className="space-y-4">
                <AgentPane runId={runId} agent="remediation" done={done} />
                <RemediationPanel report={report} loading={loading} live={live} />
              </div>
            ) : (
              <AgentPane runId={runId} agent={agent} done={done} />
            )}
          </TabsContent>
        ))}

        <TabsContent value="twin">
          {top ? (
            <TwinCompare hypothesis={top} twinResult={twinResult} />
          ) : (
            <p className="text-sm text-muted-foreground">No hypothesis ranked yet.</p>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
