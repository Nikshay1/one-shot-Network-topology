import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useRunStore } from '@/store/useRunStore'
import { StatusBar } from '@/components/StatusBar'
import { cn } from '@/lib/utils'

interface NavItem {
  to: string
  label: string
  end?: boolean
}

function SidebarLink({ item }: { item: NavItem }) {
  return (
    <NavLink
      to={item.to}
      end={item.end ?? false}
      className={({ isActive }) =>
        cn(
          'rounded-md px-3 py-2 text-sm transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          isActive
            ? 'bg-secondary text-foreground'
            : 'text-muted-foreground hover:bg-accent hover:text-foreground',
        )
      }
    >
      {item.label}
    </NavLink>
  )
}

export function Shell({ children }: { children: ReactNode }) {
  const runId = useRunStore((s) => s.runId)

  const nav: NavItem[] = [
    { to: '/', label: 'Console', end: true },
    // Run is only reachable once there is a run to look at; keep the nav honest
    // rather than linking somewhere that 404s.
    { to: runId ? `/run/${runId}` : '/', label: 'Run' },
    { to: '/benchmark', label: 'Benchmark' },
  ]

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <nav className="flex w-52 shrink-0 flex-col gap-6 border-r border-border p-4">
        <NavLink to="/" className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded">
          <div className="text-lg font-semibold tracking-[0.2em]">VERDICT</div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            network RCA
          </div>
        </NavLink>

        <div className="flex flex-col gap-1">
          {nav.map((item) => (
            <SidebarLink key={item.label} item={item} />
          ))}
        </div>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <StatusBar />
        <main className="min-h-0 flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
