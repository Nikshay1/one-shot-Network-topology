/**
 * The PULSEPOINT frame: dark chrome top and bottom, cream workspace between.
 *
 * Ported from the reference design. The nav is centred in the header rather
 * than a left rail, the brand is a red pulse mark plus a Playfair wordmark, and
 * live status sits far right.
 */
import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useRunStore } from '@/store/useRunStore'
import { useHealth } from '@/hooks/useHealth'
import { StageRail } from '@/components/StageRail'
import { cn } from '@/lib/utils'

function BrandMark() {
  return (
    <span
      className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-[#f9565e]/80"
      style={{
        background: 'radial-gradient(circle at 50% 45%, rgba(237,45,57,.35), rgba(116,10,18,.35))',
        boxShadow: '0 0 0 5px rgba(225,31,42,.1), 0 0 27px rgba(216,26,37,.3)',
      }}
      aria-hidden
    >
      <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="#fff" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="9" stroke="#ff777e" strokeWidth={1} />
        <path d="M4 12h3l2.5-5 3 10 2.5-5H20" />
      </svg>
    </span>
  )
}

function LiveStatus() {
  const { state, offline, reason } = useHealth()

  const title =
    reason === 'mock'
      ? 'MOCK MODE — fixtures only, the API is not being called'
      : state === 'online'
        ? 'API reachable'
        : state === 'offline'
          ? 'API unreachable — is the backend running?'
          : 'checking API…'

  return (
    <div className="flex items-center gap-2.5" title={title}>
      <span
        className={cn(
          'h-2 w-2 shrink-0 rounded-full',
          state === 'online'
            ? 'bg-[#f3424b] shadow-[0_0_8px_#f3424b]'
            : state === 'offline'
              ? 'bg-chrome-muted'
              : 'animate-pulse bg-chrome-muted',
        )}
        aria-hidden
      />
      <span className="font-mono text-[11px] uppercase tracking-[1.15px] text-chrome-muted">
        {reason === 'mock' ? 'mock data' : state === 'online' ? 'system live' : state}
      </span>
      {offline && (
        <span className="rounded border border-[#64252a] bg-[#721117]/30 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[#ff747b]">
          {reason === 'mock' ? 'offline · mock' : 'api unreachable'}
        </span>
      )}
    </div>
  )
}

interface NavItem {
  to: string
  label: string
  end?: boolean
}

export function Shell({ children }: { children: ReactNode }) {
  const runId = useRunStore((s) => s.runId)

  const nav: NavItem[] = [
    { to: '/', label: 'Console', end: true },
    // Only linked once there is a run to look at, rather than somewhere that 404s.
    { to: runId ? `/run/${runId}` : '/', label: 'Current Run' },
    // Chat replaced Benchmark here. /benchmark still exists and still works — it is
    // simply unlinked, like /demo-script.
    { to: '/chat', label: 'Chat' },
  ]

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <header className="chrome-texture relative z-20 flex h-[86px] shrink-0 items-center gap-12 border-b border-chrome-border px-[4.6%]">
        <NavLink
          to="/"
          className="flex items-center gap-3 rounded text-chrome-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <BrandMark />
          <span className="font-display text-[27px] leading-none tracking-[-0.9px]">
            pulse<span className="text-[#e71f2a]">point</span>
          </span>
        </NavLink>

        <nav className="mx-auto flex h-full items-stretch gap-1">
          {nav.map((item) => (
            <NavLink
              key={item.label}
              to={item.to}
              end={item.end ?? false}
              className={({ isActive }) =>
                cn(
                  'flex items-center border-b-2 px-5 text-sm font-bold transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                  isActive
                    ? 'border-[#e71f2a] text-white'
                    : 'border-transparent text-[#898482] hover:text-white',
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <LiveStatus />
      </header>

      <div className="workspace-texture relative flex min-h-0 flex-1 flex-col overflow-hidden">
        <StageRail />
        <main className="min-h-0 flex-1 overflow-auto">{children}</main>
      </div>

      <footer className="chrome-texture flex h-[42px] shrink-0 items-center justify-between border-t border-chrome-border px-[4.6%] font-mono text-[9px] uppercase tracking-[0.7px] text-[#6d6765]">
        <span>© 2026 pulsepoint</span>
        <span className="hidden sm:inline">network anomaly root-cause assistant</span>
        <span className="flex items-center gap-2 text-[#8c8581]">
          <i className="inline-block h-1.5 w-1.5 rounded-full bg-[#d91d27]" aria-hidden />
          {runId ? `run ${runId}` : 'telemetry stream connected'}
        </span>
      </footer>
    </div>
  )
}
