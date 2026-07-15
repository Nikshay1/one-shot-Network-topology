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

      {done && !transcript.loading && !transcript.found && liveSteps.length === 0 && (
        <p className="rounded border border-border bg-card p-2 text-[11px] leading-snug text-muted-foreground">
          This agent has no transcript for this run. That is a real outcome, not an error: the
          investigator is skipped entirely when the run falls back to the deterministic autopilot
          (rule 11), and only agents that actually ran are registered.
        </p>
      )}

      <AgentTranscript
        rows={rows}
        done={agentDone}
        finalText={transcript.result?.final_text}
        source={useHistorical ? 'transcript' : 'live'}
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
