import { Badge } from '@/components/ui/badge'

export function Benchmark() {
  return (
    <div className="p-6">
      <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-border">
        <div className="space-y-2 p-8 text-center">
          <Badge variant="outline">F6</Badge>
          <p className="text-sm text-muted-foreground">
            Aggregate metrics from GET /benchmark land here.
          </p>
          <p className="mx-auto max-w-md text-xs text-muted-foreground/70">
            Note: per-case ground truth is redacted at the boundary, so there is no
            &ldquo;was it right?&rdquo; badge to render — only aggregates.
          </p>
        </div>
      </div>
    </div>
  )
}
