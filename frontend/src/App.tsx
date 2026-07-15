import { Navigate, Route, Routes } from 'react-router-dom'
import { Shell } from '@/layout/Shell'
import { Console } from '@/pages/Console'
import { RunPage } from '@/pages/RunPage'
import { Benchmark } from '@/pages/Benchmark'

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Console />} />
        <Route path="/run/:runId" element={<RunPage />} />
        <Route path="/benchmark" element={<Benchmark />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}
