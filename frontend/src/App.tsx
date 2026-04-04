import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { LibraryPage } from './pages/LibraryPage'
import { BenchmarkPage } from './pages/BenchmarkPage'
import { ResultsPage } from './pages/ResultsPage'
import { ModelsPage } from './pages/ModelsPage'
import { TuningPage } from './pages/TuningPage'
import { HWFlowPage } from './pages/HWFlowPage'
import { DashboardPage } from './pages/DashboardPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/library" replace />} />
          <Route path="library" element={<LibraryPage />} />
          <Route path="benchmark" element={<BenchmarkPage />} />
          <Route path="benchmark/mic" element={<BenchmarkPage />} />
          <Route path="results" element={<ResultsPage />} />
          <Route path="tuning" element={<TuningPage />} />
          <Route path="models" element={<ModelsPage />} />
          {/* TranscribePage is always mounted in Layout — route renders empty placeholder */}
          <Route path="transcript" element={<></>} />
          <Route path="prepis" element={<Navigate to="/transcript" replace />} />
          <Route path="hwflow" element={<HWFlowPage />} />
          <Route path="hw-flow" element={<HWFlowPage />} />
          <Route path="HWFlow" element={<HWFlowPage />} />
          <Route path="dashboard" element={<DashboardPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
