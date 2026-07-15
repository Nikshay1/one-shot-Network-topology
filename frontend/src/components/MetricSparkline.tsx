/**
 * Compact metric line with anomaly windows shaded amber.
 *
 * Data comes from the SSE stream and nowhere else — there is no metric endpoint
 * — so a series is live-only and may be silently truncated by the server's
 * replay cap. The caller shows the point count so a thin line is legible as
 * "little data" rather than "flat metric".
 */
import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { MetricPoint, MetricSeries } from '@/store/runStore'
import type { TimeWindow } from '@/types/anomaly'
import { formatClock } from '@/lib/format'

/** Requirement: a sparkline renders at most this many points. */
export const MAX_POINTS = 200

/**
 * Largest-Triangle-Three-Buckets. Picks the points that preserve the SHAPE of
 * the line — a plain every-Nth stride drops exactly the spikes an anomaly chart
 * exists to show. First and last points are always kept.
 */
export function downsample(points: MetricPoint[], threshold = MAX_POINTS): MetricPoint[] {
  const n = points.length
  if (threshold >= n || threshold < 3) return points

  const sampled: MetricPoint[] = [points[0]!]
  const every = (n - 2) / (threshold - 2)
  let a = 0

  for (let i = 0; i < threshold - 2; i += 1) {
    const rangeStart = Math.floor((i + 1) * every) + 1
    const rangeEnd = Math.min(Math.floor((i + 2) * every) + 1, n)

    let avgTs = 0
    let avgValue = 0
    const avgLen = rangeEnd - rangeStart || 1
    for (let j = rangeStart; j < rangeEnd; j += 1) {
      avgTs += points[j]!.ts
      avgValue += points[j]!.value
    }
    avgTs /= avgLen
    avgValue /= avgLen

    const bucketStart = Math.floor(i * every) + 1
    const bucketEnd = Math.floor((i + 1) * every) + 1
    const pointA = points[a]!

    let maxArea = -1
    let next = bucketStart
    for (let j = bucketStart; j < Math.min(bucketEnd, n); j += 1) {
      const p = points[j]!
      const area = Math.abs(
        (pointA.ts - avgTs) * (p.value - pointA.value) - (pointA.ts - p.ts) * (avgValue - pointA.value),
      )
      if (area > maxArea) {
        maxArea = area
        next = j
      }
    }
    sampled.push(points[next]!)
    a = next
  }

  sampled.push(points[n - 1]!)
  return sampled
}

export interface MetricSparklineProps {
  series: MetricSeries
  /** Windows to shade amber — the anomaly windows for this component. */
  windows?: TimeWindow[]
  height?: number
}

export function MetricSparkline({ series, windows = [], height = 44 }: MetricSparklineProps) {
  const data = useMemo(() => downsample(series.points), [series.points])

  if (data.length === 0) {
    return <p className="text-[11px] text-muted-foreground">no samples</p>
  }

  const id = `spark-${series.component_id}-${series.name}`.replace(/[^a-z0-9-]/gi, '')

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#38bdf8" stopOpacity={0} />
            </linearGradient>
          </defs>

          {/*
            A numeric x-scale keyed on ts is required, not optional: with the
            default category scale ReferenceArea's x1/x2 timestamps don't map to
            the axis and the amber windows land in the wrong place.
          */}
          <XAxis hide dataKey="ts" type="number" domain={['dataMin', 'dataMax']} />

          {/* Anomaly windows, shaded. */}
          {windows.map((w, i) => (
            <ReferenceArea
              key={`${w.start}-${w.end}-${i}`}
              x1={w.start}
              x2={w.end}
              fill="#fbbf24"
              fillOpacity={0.16}
              stroke="none"
            />
          ))}

          <YAxis hide domain={['dataMin', 'dataMax']} />
          <Tooltip
            contentStyle={{
              background: 'hsl(222 44% 9%)',
              border: '1px solid hsl(217 33% 20%)',
              borderRadius: 6,
              fontSize: 11,
            }}
            labelFormatter={(ts: number) => formatClock(ts)}
            formatter={(value: number) => [
              `${value}${series.unit ? ` ${series.unit}` : ''}`,
              series.name,
            ]}
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke="#38bdf8"
            strokeWidth={1.4}
            fill={`url(#${id})`}
            isAnimationActive={false}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
