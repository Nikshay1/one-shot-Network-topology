/**
 * Non-blocking toasts.
 *
 * The rule this exists for: a `pipeline_error` must NOT take the demo down. Rule
 * 11 says any agent error, timeout or budget exhaustion falls back to the
 * deterministic autopilot and the run still completes — so a mid-run failure is
 * a thing to mention, not a thing to stop for. It gets a toast and an amber
 * status bar; everything already rendered stays on screen.
 */
import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { MOTION_MS } from '@/theme/tokens'
import { cn } from '@/lib/utils'

export type ToastTone = 'info' | 'warn' | 'error'

export interface Toast {
  id: number
  tone: ToastTone
  title: string
  detail?: string | undefined
}

interface ToastContextValue {
  toast: (t: Omit<Toast, 'id'>) => void
}

const ToastContext = createContext<ToastContextValue>({ toast: () => {} })

export function useToast(): ToastContextValue {
  return useContext(ToastContext)
}

const TONE_CLASS: Record<ToastTone, string> = {
  info: 'border-border bg-card',
  warn: 'border-amber-500/40 bg-amber-500/10',
  error: 'border-rose-500/40 bg-rose-500/10',
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const toast = useCallback((t: Omit<Toast, 'id'>) => {
    // Date.now() is fine for a local key; nothing here is replayed.
    const id = Date.now() + Math.random()
    setToasts((prev) => [...prev, { ...t, id }])
    setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== id)), 8_000)
  }, [])

  const value = useMemo(() => ({ toast }), [toast])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2"
        role="status"
        aria-live="polite"
      >
        <AnimatePresence initial={false}>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              transition={{ duration: MOTION_MS.page / 1000 }}
              className={cn(
                'pointer-events-auto rounded-lg border p-3 shadow-xl backdrop-blur',
                TONE_CLASS[t.tone],
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-xs font-medium">{t.title}</p>
                <button
                  type="button"
                  onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
                  aria-label="dismiss"
                  className="shrink-0 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  ✕
                </button>
              </div>
              {t.detail && (
                <p className="mt-1 break-words text-[11px] leading-snug text-muted-foreground">
                  {t.detail}
                </p>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  )
}
