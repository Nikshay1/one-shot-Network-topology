import { useParams, useSearchParams } from 'react-router-dom'
import { useRunStream } from '@/hooks/useRunStream'
import { useRunStore } from '@/store/useRunStore'
import { IncidentView } from '@/pages/IncidentView'
import { VerdictView } from '@/pages/VerdictView'
import { AgentsView } from '@/pages/AgentsView'
import { ReportView } from '@/pages/ReportView'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

/**
 * Every demo-critical state is URL-addressable (`/run/{id}?view=verdict`) so the
 * presenter can jump straight to it instead of remembering a click path.
 */
export const RUN_VIEWS = ['incident', 'verdict', 'agents', 'report'] as const
export type RunView = (typeof RUN_VIEWS)[number]

export const DEFAULT_VIEW: RunView = 'incident'

export function parseView(raw: string | null): RunView {
  return RUN_VIEWS.includes((raw ?? '') as RunView) ? (raw as RunView) : DEFAULT_VIEW
}

export function RunPage() {
  const { runId } = useParams<{ runId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const view = parseView(searchParams.get('view'))

  // Dispatch logging is on in dev (the F1/F2 verify step reads it) but off under
  // test, where it would bury the actual failures.
  useRunStream({ runId, debug: import.meta.env.DEV && import.meta.env.MODE !== 'test' })

  const status = useRunStore((s) => s.status)
  const errorInfo = useRunStore((s) => s.errorInfo)

  const setView = (next: string) => {
    // replace: the tab is a view, not a navigation step — don't stack history
    // entries the presenter has to back out of on stage.
    setSearchParams({ view: next }, { replace: true })
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-6">
      <div className="flex shrink-0 items-center justify-between gap-4">
        <Tabs value={view} onValueChange={setView}>
          <TabsList>
            <TabsTrigger value="incident">Incident</TabsTrigger>
            <TabsTrigger value="verdict">Verdict</TabsTrigger>
            <TabsTrigger value="agents">Agents</TabsTrigger>
            <TabsTrigger value="report">Report</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {status === 'error' && errorInfo && (
        <div role="alert" className="shrink-0 rounded-lg border border-rose-500/30 bg-rose-500/5 p-3">
          <p className="text-sm text-rose-300">
            Pipeline failed at stage <span className="font-mono">{errorInfo.stage}</span> —{' '}
            <span className="font-mono">{errorInfo.error}</span>
          </p>
        </div>
      )}

      <Tabs value={view} onValueChange={setView} className="flex min-h-0 flex-1 flex-col">
        <TabsContent value="incident" className="flex min-h-0 flex-1 data-[state=inactive]:hidden">
          {runId && <IncidentView runId={runId} />}
        </TabsContent>
        <TabsContent
          value="verdict"
          className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
        >
          {runId && <VerdictView runId={runId} />}
        </TabsContent>
        <TabsContent
          value="agents"
          className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
        >
          {runId && <AgentsView runId={runId} />}
        </TabsContent>
        <TabsContent
          value="report"
          className="flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
        >
          {runId && <ReportView runId={runId} />}
        </TabsContent>
      </Tabs>
    </div>
  )
}
