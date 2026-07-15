import { MotionConfig, motion } from 'framer-motion'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Shell } from '@/layout/Shell'
import { Console } from '@/pages/Console'
import { RunPage } from '@/pages/RunPage'
import { BenchmarkView } from '@/pages/BenchmarkView'
import { DemoScript } from '@/pages/DemoScript'
import { ToastProvider } from '@/components/Toaster'
import { MOTION_MS } from '@/theme/tokens'

/** Page transition. Capped at MOTION_MS.page — nothing on stage should wait. */
function Page({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: MOTION_MS.page / 1000, ease: 'easeOut' }}
      className="flex h-full min-h-0 flex-col"
    >
      {children}
    </motion.div>
  )
}

function AppRoutes() {
  const location = useLocation()
  return (
    <Routes location={location} key={location.pathname}>
      <Route
        path="/"
        element={
          <Page>
            <Console />
          </Page>
        }
      />
      <Route
        path="/run/:runId"
        element={
          <Page>
            <RunPage />
          </Page>
        }
      />
      <Route
        path="/benchmark"
        element={
          <Page>
            <BenchmarkView />
          </Page>
        }
      />
      {/* Unlinked: the presenter's teleprompter. */}
      <Route
        path="/demo-script"
        element={
          <Page>
            <DemoScript />
          </Page>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    // reducedMotion="user" makes every framer-motion animation in the app honour
    // prefers-reduced-motion, so individual components don't each have to ask.
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <Shell>
          <AppRoutes />
        </Shell>
      </ToastProvider>
    </MotionConfig>
  )
}
